from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
import time
import uuid
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import state_dir
from .authz import Action, PolicyEngine, Principal, Resource
from .database import (
    DatabaseError,
    PostgresDatabase,
    database_url_from_env,
    is_postgres_url,
)

ALLOWED_ROLES = {"owner", "admin", "developer", "auditor", "viewer"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EnterpriseStore:
    """Tenant data model with SQLite (default) and PostgreSQL (opt-in) backends."""

    def __init__(self, db_path: Path | None = None, policy: PolicyEngine | None = None,
                 *, database_url: str | None = None):
        self._database_url = database_url or database_url_from_env()
        self._postgres: PostgresDatabase | None = None
        if self._database_url:
            if not is_postgres_url(self._database_url):
                raise DatabaseError("OGX_DATABASE_URL must be a postgresql:// URL.")
            self._postgres = PostgresDatabase(self._database_url)
        self.db_path = db_path or (state_dir() / "enterprise" / "ogx-enterprise.sqlite3")
        self._policy = policy or PolicyEngine()
        self._migrated = False

    @property
    def policy(self) -> PolicyEngine:
        return self._policy

    @property
    def backend(self) -> str:
        return "postgresql" if self._postgres is not None else "sqlite"

    @property
    def database_url(self) -> str | None:
        """The configured URL (never log it; use ``database_display``)."""

        return self._database_url

    @property
    def database_display(self) -> str:
        if self._postgres is not None:
            return self._postgres.display
        return str(self.db_path)

    def _connect(self):
        if self._postgres is not None:
            return self._postgres.connect()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.is_symlink():
            raise ValueError("Enterprise database path must not be a symlink.")
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> Path | str:
        schema = files("ogx.enterprise").joinpath("schema.sql").read_text(encoding="utf-8")
        if self._postgres is not None:
            self._postgres.migrate(schema)
            self._migrated = True
            return self.database_display
        with self._connection() as conn:
            conn.executescript(schema)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL,"
                " description TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at, description)"
                " VALUES(1, ?, 'initial enterprise schema')"
                " ON CONFLICT(version) DO NOTHING",
                (_now(),),
            )
        self._migrated = True
        return self.db_path

    def table_names(self) -> set[str]:
        """Return the application tables for readiness and diagnostics."""

        if self._postgres is not None:
            return self._postgres.table_names()
        with self._connection() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {str(row[0]) for row in rows}

    def schema_version(self) -> int:
        if self._postgres is not None:
            return self._postgres.schema_version()
        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def health(self) -> dict[str, Any]:
        """Backend health with no credentials in the payload."""

        started = time.monotonic()
        try:
            with self._connection() as conn:
                conn.execute("SELECT 1").fetchone()
            tables = self.table_names()
            return {
                "backend": self.backend,
                "display": self.database_display,
                "ok": True,
                "schema_version": self.schema_version(),
                "tables": len(tables),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
            }
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {
                "backend": self.backend,
                "display": self.database_display,
                "ok": False,
                "error": type(exc).__name__,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
            }

    def close(self) -> None:
        if self._postgres is not None:
            self._postgres.close()


    def create_organization(self, slug: str, display_name: str, owner_subject: str) -> dict[str, Any]:
        if not slug or len(slug) > 64 or not all(c.islower() or c.isdigit() or c in "-_" for c in slug):
            raise ValueError("Organization slug must be lowercase letters, digits, hyphens or underscores (max 64).")
        if not display_name.strip() or not owner_subject.strip():
            raise ValueError("Organization name and external owner subject are required.")
        org_id, user_id = uuid.uuid4().hex, uuid.uuid4().hex
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO users(id, external_subject, created_at) VALUES(?,?,?)"
                         " ON CONFLICT DO NOTHING",
                         (user_id, owner_subject, _now()))
            row = conn.execute("SELECT id FROM users WHERE external_subject=?", (owner_subject,)).fetchone()
            user_id = row["id"]
            conn.execute("INSERT INTO organizations(id,slug,display_name,created_at) VALUES(?,?,?,?)",
                         (org_id, slug, display_name.strip(), _now()))
            conn.execute("INSERT INTO memberships(organization_id,user_id,role,created_at) VALUES(?,?,?,?)",
                         (org_id, user_id, "owner", _now()))
            self._audit(conn, org_id, user_id, "organization.create", "organization", org_id, {"slug": slug})
        return {"organization_id": org_id, "slug": slug, "display_name": display_name.strip(), "owner_user_id": user_id}

    @staticmethod
    def _audit(conn: sqlite3.Connection, org_id: str, actor_id: str | None, action: str,
               resource_type: str, resource_id: str | None, details: dict[str, Any]) -> None:
        conn.execute("INSERT INTO audit_events(id,organization_id,actor_user_id,action,resource_type,resource_id,occurred_at,details_json) VALUES(?,?,?,?,?,?,?,?)",
                     (uuid.uuid4().hex, org_id, actor_id, action, resource_type, resource_id, _now(), json.dumps(details, sort_keys=True)))

    def add_member(self, organization_id: str, external_subject: str, role: str, actor_subject: str) -> dict[str, Any]:
        if role not in ALLOWED_ROLES:
            raise ValueError("Unsupported organization role.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor = conn.execute("SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND u.external_subject=?",
                                 (organization_id, actor_subject)).fetchone()
            if not actor or actor["role"] not in {"owner", "admin"}:
                raise PermissionError("Only an organization owner/admin may add members.")
            if role in {"owner", "admin"} and actor["role"] != "owner":
                raise PermissionError("Only an owner may grant owner or admin privileges.")
            if conn.execute("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND u.external_subject=?",
                            (organization_id, external_subject)).fetchone():
                raise ValueError("This identity is already a member of the organization.")
            conn.execute("INSERT INTO users(id,external_subject,created_at) VALUES(?,?,?)"
                         " ON CONFLICT DO NOTHING", (uuid.uuid4().hex, external_subject, _now()))
            user = conn.execute("SELECT id FROM users WHERE external_subject=?", (external_subject,)).fetchone()
            conn.execute("INSERT INTO memberships(organization_id,user_id,role,created_at) VALUES(?,?,?,?)",
                         (organization_id, user["id"], role, _now()))
            self._audit(conn, organization_id, actor["id"], "membership.create", "user", user["id"], {"role": role})
        return {"organization_id": organization_id, "user_id": user["id"], "role": role}

    @staticmethod
    def _validate_repository_ref(repository_ref: str | None) -> str | None:
        if repository_ref is None:
            return None
        value = repository_ref.strip()
        if not value:
            return None
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.username or parsed.password:
                raise ValueError("Repository URLs must not contain embedded credentials; use a provider credential helper.")
            if parsed.query or parsed.fragment:
                raise ValueError("Repository URLs with query strings or fragments are not accepted.")
            if parsed.scheme not in {"https", "ssh", "git", "http"} or not parsed.hostname:
                raise ValueError("Unsupported repository URL.")
        return value

    def register_project(self, organization_id: str, name: str, provider: str = "local", repository_ref: str | None = None,
                         actor_subject: str | None = None) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Project name is required.")
        repository_ref = self._validate_repository_ref(repository_ref)
        if not actor_subject:
            raise PermissionError("An organization member identity is required to register a project.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor = conn.execute("SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND u.external_subject=?",
                                 (organization_id, actor_subject)).fetchone()
            if not actor or actor["role"] not in {"owner", "admin", "developer"}:
                raise PermissionError("This identity cannot register a project in the organization.")
            exists = conn.execute("SELECT id FROM organizations WHERE id=? AND status='active'", (organization_id,)).fetchone()
            if not exists: raise ValueError("Organization not found or inactive.")
            project_id = uuid.uuid4().hex
            conn.execute("INSERT INTO projects(id,organization_id,name,provider,repository_ref,created_at) VALUES(?,?,?,?,?,?)",
                         (project_id, organization_id, name.strip(), provider, repository_ref, _now()))
            self._audit(conn, organization_id, actor["id"], "project.register", "project", project_id,
                        {"name": name.strip(), "provider": provider})
        return {"project_id": project_id, "organization_id": organization_id, "name": name.strip(), "provider": provider}

    def list_projects(self, organization_id: str, actor_subject: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            access = conn.execute("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND u.external_subject=?",
                                  (organization_id, actor_subject)).fetchone()
            if not access: raise PermissionError("No membership in this organization.")
            rows = conn.execute("SELECT id,name,provider,repository_ref,created_at,status FROM projects WHERE organization_id=? ORDER BY name",
                                (organization_id,)).fetchall()
            return [dict(row) for row in rows]

    def list_audit_events(self, organization_id: str, actor_subject: str, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000: raise ValueError("limit must be between 1 and 1000.")
        with self._connection() as conn:
            access = conn.execute("SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND u.external_subject=?",
                                  (organization_id, actor_subject)).fetchone()
            if not access or access["role"] not in {"owner", "admin", "auditor"}:
                raise PermissionError("Only owners, admins or auditors may read organization audit events.")
            rows = conn.execute("SELECT action,resource_type,resource_id,occurred_at,details_json FROM audit_events WHERE organization_id=? ORDER BY occurred_at DESC LIMIT ?",
                                (organization_id, limit)).fetchall()
            return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]


    def create_governance_record(self, organization_id: str, record_type: str, title: str,
                                 owner_subject: str, priority: str, status: str,
                                 actor_subject: str, due_date: str | None = None,
                                 details: dict[str, Any] | None = None) -> dict[str, Any]:
        from .governance import RECORD_PRIORITIES, RECORD_STATUSES, RECORD_TYPES
        if record_type not in RECORD_TYPES:
            raise ValueError(f"Unsupported governance record type: {record_type}")
        if priority not in RECORD_PRIORITIES or status not in RECORD_STATUSES:
            raise ValueError("Unsupported priority or status value.")
        if not title.strip() or not owner_subject.strip():
            raise ValueError("Title and accountable owner subject are required.")
        if due_date is not None and (len(due_date) != 10 or due_date[4] != "-" or due_date[7] != "-"):
            raise ValueError("due_date must use YYYY-MM-DD format.")
        allowed_roles = {"owner", "admin"} if record_type in {"objective", "initiative", "decision"} else {"owner", "admin", "developer"}
        record_id = uuid.uuid4().hex
        now = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor = conn.execute(
                "SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, actor_subject),
            ).fetchone()
            if not actor or actor["role"] not in allowed_roles:
                raise PermissionError(f"Role must be one of {sorted(allowed_roles)} to create {record_type} records.")
            conn.execute(
                "INSERT INTO governance_records(id,organization_id,record_type,title,owner_subject,priority,status,due_date,details_json,created_by_user_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, organization_id, record_type, title.strip(), owner_subject.strip(), priority,
                 status, due_date, json.dumps(details or {}, sort_keys=True), actor["id"], now, now),
            )
            self._audit(conn, organization_id, actor["id"], f"governance.{record_type}.create",
                        "governance_record", record_id,
                        {"title": title.strip(), "priority": priority, "status": status})
        return {"record_id": record_id, "organization_id": organization_id,
                "record_type": record_type, "title": title.strip(), "owner_subject": owner_subject.strip(),
                "priority": priority, "status": status, "due_date": due_date}

    def list_governance_records(self, organization_id: str, actor_subject: str,
                                record_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        from .governance import RECORD_TYPES
        if record_type and record_type not in RECORD_TYPES:
            raise ValueError(f"Unsupported governance record type: {record_type}")
        with self._connection() as conn:
            access = conn.execute(
                "SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, actor_subject),
            ).fetchone()
            if not access:
                raise PermissionError("No membership in this organization.")
            query = ("SELECT id,record_type,title,owner_subject,priority,status,due_date,details_json,created_at,updated_at "
                     "FROM governance_records WHERE organization_id=?")
            params: list[Any] = [organization_id]
            if record_type:
                query += " AND record_type=?"
                params.append(record_type)
            query += " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    def governance_overview(self, organization_id: str, actor_subject: str) -> dict[str, Any]:
        with self._connection() as conn:
            access = conn.execute(
                "SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, actor_subject),
            ).fetchone()
            if not access or access["role"] not in {"owner", "admin", "auditor"}:
                raise PermissionError("Only owners, admins or auditors may read the enterprise overview.")
            org = conn.execute("SELECT slug,display_name,status FROM organizations WHERE id=?", (organization_id,)).fetchone()
            if not org:
                raise ValueError("Organization not found.")
            counts = conn.execute(
                "SELECT record_type,status,priority,COUNT(*) AS count FROM governance_records "
                "WHERE organization_id=? GROUP BY record_type,status,priority", (organization_id,)
            ).fetchall()
            projects = conn.execute("SELECT COUNT(*) FROM projects WHERE organization_id=? AND status='active'", (organization_id,)).fetchone()[0]
            members = conn.execute("SELECT COUNT(*) FROM memberships WHERE organization_id=?", (organization_id,)).fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM approval_requests WHERE status='pending'").fetchone()[0]
            return {"organization": dict(org), "active_projects": projects, "members": members,
                    "local_pending_approvals": pending,
                    "governance_records": [dict(row) for row in counts]}

    def list_members(self, organization_id: str, actor_subject: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            access = conn.execute(
                "SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, actor_subject),
            ).fetchone()
            if not access:
                raise PermissionError("No membership in this organization.")
            rows = conn.execute(
                "SELECT u.external_subject,u.status,m.role,m.created_at FROM memberships m "
                "JOIN users u ON u.id=m.user_id WHERE m.organization_id=? ORDER BY m.role,u.external_subject",
                (organization_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_member_role(self, organization_id: str, external_subject: str, role: str,
                        actor_subject: str) -> dict[str, Any]:
        if role not in ALLOWED_ROLES:
            raise ValueError("Unsupported organization role.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor = conn.execute(
                "SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, actor_subject),
            ).fetchone()
            target = conn.execute(
                "SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, external_subject),
            ).fetchone()
            if not actor or actor["role"] not in {"owner", "admin"}:
                raise PermissionError("Only an organization owner/admin may change member roles.")
            if not target:
                raise ValueError("Member not found in this organization.")
            if role in {"owner", "admin"} and actor["role"] != "owner":
                raise PermissionError("Only an owner may grant owner or admin privileges.")
            if target["role"] == "owner" and role != "owner":
                owner_count = conn.execute(
                    "SELECT COUNT(*) FROM memberships WHERE organization_id=? AND role='owner'", (organization_id,)
                ).fetchone()[0]
                if owner_count <= 1:
                    raise PermissionError("The last organization owner cannot be demoted.")
                if actor["role"] != "owner":
                    raise PermissionError("Only an owner may change another owner's role.")
            conn.execute("UPDATE memberships SET role=? WHERE organization_id=? AND user_id=?",
                         (role, organization_id, target["id"]))
            self._audit(conn, organization_id, actor["id"], "membership.role.update", "user", target["id"],
                        {"external_subject": external_subject, "old_role": target["role"], "new_role": role})
        return {"organization_id": organization_id, "external_subject": external_subject, "role": role}

    def remove_member(self, organization_id: str, external_subject: str, actor_subject: str) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor = conn.execute(
                "SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, actor_subject),
            ).fetchone()
            target = conn.execute(
                "SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, external_subject),
            ).fetchone()
            if not actor or actor["role"] not in {"owner", "admin"}:
                raise PermissionError("Only an organization owner/admin may remove members.")
            if not target:
                raise ValueError("Member not found in this organization.")
            if target["role"] == "owner":
                owner_count = conn.execute(
                    "SELECT COUNT(*) FROM memberships WHERE organization_id=? AND role='owner'", (organization_id,)
                ).fetchone()[0]
                if owner_count <= 1:
                    raise PermissionError("The last organization owner cannot be removed.")
                if actor["role"] != "owner":
                    raise PermissionError("Only an owner may remove another owner.")
            if target["role"] == "admin" and actor["role"] != "owner":
                raise PermissionError("Only an owner may remove an admin.")
            conn.execute("DELETE FROM memberships WHERE organization_id=? AND user_id=?",
                         (organization_id, target["id"]))
            self._audit(conn, organization_id, actor["id"], "membership.remove", "user", target["id"],
                        {"external_subject": external_subject, "old_role": target["role"]})
        return {"organization_id": organization_id, "external_subject": external_subject, "removed": True}

    def update_governance_record(self, organization_id: str, record_id: str, actor_subject: str,
                                 status: str | None = None, priority: str | None = None,
                                 owner_subject: str | None = None, due_date: str | None = None,
                                 details: dict[str, Any] | None = None) -> dict[str, Any]:
        from .governance import RECORD_PRIORITIES, RECORD_STATUSES
        if status is not None and status not in RECORD_STATUSES:
            raise ValueError("Unsupported governance status.")
        if priority is not None and priority not in RECORD_PRIORITIES:
            raise ValueError("Unsupported governance priority.")
        if due_date is not None and (len(due_date) != 10 or due_date[4] != "-" or due_date[7] != "-"):
            raise ValueError("due_date must use YYYY-MM-DD format.")
        if not any(v is not None for v in (status, priority, owner_subject, due_date, details)):
            raise ValueError("Provide at least one field to update.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor = conn.execute(
                "SELECT u.id,m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, actor_subject),
            ).fetchone()
            record = conn.execute("SELECT * FROM governance_records WHERE organization_id=? AND id=?",
                                  (organization_id, record_id)).fetchone()
            if not actor:
                raise PermissionError("No membership in this organization.")
            if not record:
                raise ValueError("Governance record not found in this organization.")
            if status is not None:
                from .governance import STATUS_TRANSITIONS
                allowed_next = STATUS_TRANSITIONS.get(record["status"], set())
                if status != record["status"] and status not in allowed_next:
                    raise ValueError(f"Invalid status transition: {record['status']} -> {status}.")
            if actor["role"] not in {"owner", "admin"}:
                is_operational = record["record_type"] in {"risk", "change", "incident", "service_metric"}
                if actor["role"] != "developer" or not is_operational or record["owner_subject"] != actor_subject:
                    raise PermissionError("Only owner/admin or the accountable operational record owner may update this record.")
            merged_details = json.loads(record["details_json"])
            if details is not None:
                if not isinstance(details, dict):
                    raise ValueError("details must be a JSON object.")
                merged_details.update(details)
            new_values = {
                "status": status if status is not None else record["status"],
                "priority": priority if priority is not None else record["priority"],
                "owner_subject": owner_subject.strip() if owner_subject is not None else record["owner_subject"],
                "due_date": due_date if due_date is not None else record["due_date"],
                "details_json": json.dumps(merged_details, sort_keys=True),
                "updated_at": _now(),
            }
            conn.execute(
                "UPDATE governance_records SET status=?,priority=?,owner_subject=?,due_date=?,details_json=?,updated_at=? WHERE id=?",
                (new_values["status"], new_values["priority"], new_values["owner_subject"],
                 new_values["due_date"], new_values["details_json"], new_values["updated_at"], record_id),
            )
            self._audit(conn, organization_id, actor["id"], "governance.record.update", "governance_record", record_id,
                        {"before": {"status": record["status"], "priority": record["priority"]},
                         "after": {"status": new_values["status"], "priority": new_values["priority"]}})
        return {"record_id": record_id, "status": new_values["status"],
                "priority": new_values["priority"], "owner_subject": new_values["owner_subject"],
                "due_date": new_values["due_date"], "details": merged_details}

    # ------------------------------------------------------- authorization map
    def member_role(self, organization_id: str, subject: str) -> str | None:
        """Return the stored role for a member, or ``None`` for a non-member."""

        with self._connection() as conn:
            row = conn.execute(
                "SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND u.external_subject=?",
                (organization_id, subject),
            ).fetchone()
        return row["role"] if row else None

    def _require_action(self, conn: sqlite3.Connection, organization_id: str, subject: str,
                        action: Action, *, resource_kind: str = "organization",
                        resource_id: str | None = None) -> str:
        """Authorize an actor through the shared policy engine and return its role."""

        row = conn.execute(
            "SELECT m.role FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.organization_id=? AND u.external_subject=?",
            (organization_id, subject),
        ).fetchone()
        if row is None:
            raise PermissionError("Not a member of this organization.")
        principal = Principal(subject=subject, roles=(row["role"],), tenant_id=organization_id)
        self._policy.require(
            principal,
            action,
            Resource(kind=resource_kind, id=resource_id, tenant_id=organization_id),
        )
        return row["role"]

    @staticmethod
    def _user_id(conn: sqlite3.Connection, subject: str | None) -> str | None:
        if not subject:
            return None
        row = conn.execute("SELECT id FROM users WHERE external_subject=?", (subject,)).fetchone()
        return row["id"] if row else None

    def get_organization(self, organization_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id,slug,display_name,created_at,status FROM organizations WHERE id=?",
                (organization_id,),
            ).fetchone()
        return dict(row) if row else None

    def record_audit(self, organization_id: str, action: str, resource_type: str,
                     resource_id: str | None, details: dict[str, Any] | None = None, *,
                     actor_label: str | None = None, request_id: str | None = None) -> str:
        """Append an audit event for a non-membership actor (API/service token)."""

        event_id = uuid.uuid4().hex
        payload = dict(details or {})
        if actor_label:
            payload.setdefault("actor", actor_label)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO audit_events(id,organization_id,actor_user_id,action,resource_type,"
                "resource_id,occurred_at,request_id,details_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (event_id, organization_id, self._user_id(conn, actor_label), action,
                 resource_type, resource_id, _now(), request_id,
                 json.dumps(payload, sort_keys=True)),
            )
        return event_id

    # --------------------------------------------------------- service accounts
    def create_service_account(self, organization_id: str, name: str, base_role: str,
                               scopes: list[str], actor_subject: str) -> dict[str, Any]:
        account_id = uuid.uuid4().hex
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_action(conn, organization_id, actor_subject, Action.SERVICE_ACCOUNT_MANAGE)
            active = conn.execute(
                "SELECT 1 FROM organizations WHERE id=? AND status='active'", (organization_id,)
            ).fetchone()
            if not active:
                raise ValueError("Organization not found or inactive.")
            if conn.execute(
                "SELECT 1 FROM service_accounts WHERE organization_id=? AND name=?",
                (organization_id, name),
            ).fetchone():
                raise ValueError("A service account with that name already exists.")
            conn.execute(
                "INSERT INTO service_accounts(id,organization_id,name,base_role,scopes_json,"
                "status,created_at,created_by_user_id) VALUES(?,?,?,?,?,?,?,?)",
                (account_id, organization_id, name, base_role,
                 json.dumps(list(scopes), sort_keys=True), "active", _now(),
                 self._user_id(conn, actor_subject)),
            )
            self._audit(conn, organization_id, self._user_id(conn, actor_subject),
                        "service_account.create", "service_account", account_id,
                        {"name": name, "base_role": base_role, "scopes": list(scopes)})
        return {
            "account_id": account_id,
            "organization_id": organization_id,
            "name": name,
            "base_role": base_role,
            "scopes": list(scopes),
            "status": "active",
        }

    def get_service_account(self, account_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id,name,organization_id,base_role,scopes_json,status,created_at "
                "FROM service_accounts WHERE id=?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        account = dict(row)
        account["account_id"] = account.pop("id")
        account["scopes"] = json.loads(account.pop("scopes_json"))
        return account

    def list_service_accounts(self, organization_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id,name,base_role,scopes_json,status,created_at FROM service_accounts "
                "WHERE organization_id=? ORDER BY name",
                (organization_id,),
            ).fetchall()
        return [
            {
                "account_id": row["id"],
                "name": row["name"],
                "base_role": row["base_role"],
                "scopes": json.loads(row["scopes_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def insert_service_token(self, *, token_id: str, account_id: str, token_hash: str,
                             fingerprint_value: str, scopes: list[str], expires_at: str,
                             actor_subject: str) -> None:
        account = self.get_service_account(account_id)
        if account is None:
            raise ValueError("Service account not found.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO service_tokens(id,service_account_id,token_hash,fingerprint,"
                "scopes_json,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (token_id, account_id, token_hash, fingerprint_value,
                 json.dumps(list(scopes), sort_keys=True), _now(), expires_at),
            )
            self._audit(conn, account["organization_id"], self._user_id(conn, actor_subject),
                        "service_token.issue", "service_token", token_id,
                        {"account_id": account_id, "expires_at": expires_at})

    def find_service_token(self, token_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id,service_account_id,token_hash,fingerprint,scopes_json,created_at,"
                "expires_at,revoked_at,last_used_at FROM service_tokens WHERE id=?",
                (token_id,),
            ).fetchone()
        if row is None:
            return None
        token = dict(row)
        token["token_id"] = token.pop("id")
        token["scopes"] = json.loads(token.pop("scopes_json"))
        return token

    def touch_service_token(self, token_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE service_tokens SET last_used_at=? WHERE id=?", (_now(), token_id)
            )

    def revoke_service_token(self, organization_id: str, token_id: str,
                             actor_subject: str) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_action(conn, organization_id, actor_subject,
                                 Action.SERVICE_ACCOUNT_MANAGE, resource_kind="service_token",
                                 resource_id=token_id)
            row = conn.execute(
                "SELECT t.id,t.revoked_at,a.organization_id FROM service_tokens t "
                "JOIN service_accounts a ON a.id=t.service_account_id WHERE t.id=?",
                (token_id,),
            ).fetchone()
            if row is None or row["organization_id"] != organization_id:
                raise ValueError("Service token not found in this organization.")
            if row["revoked_at"]:
                return {"token_id": token_id, "revoked": True, "already_revoked": True}
            conn.execute(
                "UPDATE service_tokens SET revoked_at=?,revoked_by_user_id=? WHERE id=?",
                (_now(), self._user_id(conn, actor_subject), token_id),
            )
            self._audit(conn, organization_id, self._user_id(conn, actor_subject),
                        "service_token.revoke", "service_token", token_id, {})
        return {"token_id": token_id, "revoked": True, "already_revoked": False}

    def list_service_tokens(self, organization_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT t.id,t.service_account_id,t.fingerprint,t.created_at,t.expires_at,"
                "t.revoked_at,t.last_used_at,a.name FROM service_tokens t "
                "JOIN service_accounts a ON a.id=t.service_account_id "
                "WHERE a.organization_id=? ORDER BY t.created_at DESC",
                (organization_id,),
            ).fetchall()
        return [
            {
                "token_id": row["id"],
                "account_id": row["service_account_id"],
                "account_name": row["name"],
                "fingerprint": row["fingerprint"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "revoked_at": row["revoked_at"],
                "last_used_at": row["last_used_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------ tenant secrets
    def upsert_tenant_secret(self, organization_id: str, name: str, envelope_json: str,
                             key_id: str) -> None:
        now = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM tenant_secrets WHERE organization_id=? AND name=?",
                (organization_id, name),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE tenant_secrets SET envelope_json=?,key_id=?,updated_at=? WHERE id=?",
                    (envelope_json, key_id, now, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO tenant_secrets(id,organization_id,name,envelope_json,key_id,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, organization_id, name, envelope_json, key_id, now, now),
                )

    def get_tenant_secret(self, organization_id: str, name: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT name,envelope_json,key_id,created_at,updated_at FROM tenant_secrets "
                "WHERE organization_id=? AND name=?",
                (organization_id, name),
            ).fetchone()
        return dict(row) if row else None

    def list_tenant_secret_names(self, organization_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT name,key_id,created_at,updated_at FROM tenant_secrets "
                "WHERE organization_id=? ORDER BY name",
                (organization_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------- idempotency
    def get_idempotent(self, organization_id: str, key: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT route,request_digest,response_json,status_code FROM idempotency_records "
                "WHERE organization_id=? AND idempotency_key=?",
                (organization_id, key),
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["response"] = json.loads(record.pop("response_json"))
        return record

    def save_idempotent(self, organization_id: str, key: str, route: str,
                        request_digest: str, response: dict[str, Any],
                        status_code: int) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO idempotency_records(id,organization_id,idempotency_key,"
                "route,request_digest,response_json,status_code,created_at) "
                "VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(organization_id, idempotency_key) DO UPDATE SET "
                "route=excluded.route, request_digest=excluded.request_digest, "
                "response_json=excluded.response_json, status_code=excluded.status_code, "
                "created_at=excluded.created_at",
                (uuid.uuid4().hex, organization_id, key, route, request_digest,
                 json.dumps(response, sort_keys=True), status_code, _now()),
            )

    # ------------------------------------------- pre-authorized query surface
    # These methods deliberately perform no authorization of their own. They are
    # only called by interfaces that have already taken a policy decision (the
    # REST API and the CLI), so that a service identity is not forced to have a
    # human membership row while tenant scoping is still enforced by the
    # ``WHERE organization_id=?`` predicate on every query.
    @staticmethod
    def _window(limit: int, offset: int) -> tuple[int, int]:
        if not 1 <= int(limit) <= 200:
            raise ValueError("limit must be between 1 and 200.")
        if int(offset) < 0 or int(offset) > 1_000_000:
            raise ValueError("offset is out of range.")
        return int(limit), int(offset)

    def list_projects_scoped(self, organization_id: str, *, limit: int = 50,
                             offset: int = 0) -> dict[str, Any]:
        limit, offset = self._window(limit, offset)
        with self._connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM projects WHERE organization_id=?", (organization_id,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id,name,provider,repository_ref,created_at,status FROM projects "
                "WHERE organization_id=? ORDER BY name LIMIT ? OFFSET ?",
                (organization_id, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit,
                "offset": offset}

    def list_members_scoped(self, organization_id: str, *, limit: int = 50,
                            offset: int = 0) -> dict[str, Any]:
        limit, offset = self._window(limit, offset)
        with self._connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM memberships WHERE organization_id=?", (organization_id,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT u.external_subject,u.status,m.role,m.created_at FROM memberships m "
                "JOIN users u ON u.id=m.user_id WHERE m.organization_id=? "
                "ORDER BY m.role,u.external_subject LIMIT ? OFFSET ?",
                (organization_id, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit,
                "offset": offset}

    def list_audit_events_scoped(self, organization_id: str, *, limit: int = 50,
                                 offset: int = 0) -> dict[str, Any]:
        limit, offset = self._window(limit, offset)
        with self._connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE organization_id=?", (organization_id,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id,action,resource_type,resource_id,occurred_at,request_id,details_json "
                "FROM audit_events WHERE organization_id=? "
                "ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
                (organization_id, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except json.JSONDecodeError:
                item["details"] = {"unparseable": True}
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def list_governance_records_scoped(self, organization_id: str, *, record_type: str | None = None,
                                       limit: int = 50, offset: int = 0) -> dict[str, Any]:
        from .governance import RECORD_TYPES
        limit, offset = self._window(limit, offset)
        if record_type and record_type not in RECORD_TYPES:
            raise ValueError(f"Unsupported governance record type: {record_type}")
        query = (
            "SELECT id,record_type,title,owner_subject,priority,status,due_date,details_json,"
            "created_at,updated_at FROM governance_records WHERE organization_id=?"
        )
        count_query = "SELECT COUNT(*) FROM governance_records WHERE organization_id=?"
        params: list[Any] = [organization_id]
        if record_type:
            query += " AND record_type=?"
            count_query += " AND record_type=?"
            params.append(record_type)
        with self._connection() as conn:
            total = conn.execute(count_query, params).fetchone()[0]
            rows = conn.execute(
                query + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except json.JSONDecodeError:
                item["details"] = {"unparseable": True}
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def register_project_scoped(self, organization_id: str, name: str, provider: str,
                                repository_ref: str | None,
                                actor_label: str) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Project name is required.")
        repository_ref = self._validate_repository_ref(repository_ref)
        project_id = uuid.uuid4().hex
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT 1 FROM organizations WHERE id=? AND status='active'", (organization_id,)
            ).fetchone()
            if not active:
                raise ValueError("Organization not found or inactive.")
            if conn.execute(
                "SELECT 1 FROM projects WHERE organization_id=? AND name=?",
                (organization_id, name.strip()),
            ).fetchone():
                raise ValueError("A project with that name already exists in the organization.")
            conn.execute(
                "INSERT INTO projects(id,organization_id,name,provider,repository_ref,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (project_id, organization_id, name.strip(), provider, repository_ref, _now()),
            )
            conn.execute(
                "INSERT INTO audit_events(id,organization_id,actor_user_id,action,resource_type,"
                "resource_id,occurred_at,details_json) VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, organization_id, self._user_id(conn, actor_label),
                 "project.register", "project", project_id, _now(),
                 json.dumps({"name": name.strip(), "provider": provider,
                             "actor": actor_label}, sort_keys=True)),
            )
        return {"project_id": project_id, "organization_id": organization_id,
                "name": name.strip(), "provider": provider,
                "repository_ref": repository_ref}

    def create_governance_record_scoped(self, organization_id: str, record_type: str, title: str,
                                       owner_subject: str, priority: str, status: str,
                                       due_date: str | None, details: dict[str, Any],
                                       actor_label: str) -> dict[str, Any]:
        """Create a governance record for an already-authorized interface caller."""

        from .governance import RECORD_PRIORITIES, RECORD_STATUSES, RECORD_TYPES
        if record_type not in RECORD_TYPES:
            raise ValueError(f"Unsupported governance record type: {record_type}")
        if priority not in RECORD_PRIORITIES:
            raise ValueError("Unsupported governance priority.")
        if status not in RECORD_STATUSES:
            raise ValueError("Unsupported governance status.")
        if not title.strip() or not owner_subject.strip():
            raise ValueError("Title and accountable owner subject are required.")
        if due_date is not None and (len(due_date) != 10 or due_date[4] != "-" or due_date[7] != "-"):
            raise ValueError("due_date must use YYYY-MM-DD format.")
        if not isinstance(details, dict):
            raise ValueError("details must be a JSON object.")
        record_id = uuid.uuid4().hex
        now = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # A service identity is still an accountable actor; record it so the
            # NOT NULL creator column stays meaningful without inventing a
            # membership or an interactive login.
            conn.execute(
                "INSERT INTO users(id,external_subject,created_at) VALUES(?,?,?)"
                " ON CONFLICT DO NOTHING",
                (uuid.uuid4().hex, actor_label, now),
            )
            conn.execute(
                "INSERT INTO governance_records(id,organization_id,record_type,title,owner_subject,"
                "priority,status,due_date,details_json,created_by_user_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, organization_id, record_type, title.strip(), owner_subject.strip(),
                 priority, status, due_date, json.dumps(details, sort_keys=True),
                 self._user_id(conn, actor_label), now, now),
            )
            conn.execute(
                "INSERT INTO audit_events(id,organization_id,actor_user_id,action,resource_type,"
                "resource_id,occurred_at,details_json) VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, organization_id, self._user_id(conn, actor_label),
                 f"governance.{record_type}.create", "governance_record", record_id, now,
                 json.dumps({"title": title.strip(), "priority": priority, "actor": actor_label},
                            sort_keys=True)),
            )
        return {
            "record_id": record_id,
            "organization_id": organization_id,
            "record_type": record_type,
            "title": title.strip(),
            "owner_subject": owner_subject.strip(),
            "priority": priority,
            "status": status,
            "due_date": due_date,
        }
