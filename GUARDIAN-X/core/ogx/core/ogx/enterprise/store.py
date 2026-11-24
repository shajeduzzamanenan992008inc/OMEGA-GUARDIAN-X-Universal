from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import state_dir

ALLOWED_ROLES = {"owner", "admin", "developer", "auditor", "viewer"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EnterpriseStore:
    """Local single-node tenant data model. Not a production SaaS auth/API server."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (state_dir() / "enterprise" / "ogx-enterprise.sqlite3")

    def _connect(self) -> sqlite3.Connection:
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

    def initialize(self) -> Path:
        schema = files("ogx.enterprise").joinpath("schema.sql").read_text(encoding="utf-8")
        with self._connection() as conn:
            conn.executescript(schema)
        return self.db_path

    def create_organization(self, slug: str, display_name: str, owner_subject: str) -> dict[str, Any]:
        if not slug or len(slug) > 64 or not all(c.islower() or c.isdigit() or c in "-_" for c in slug):
            raise ValueError("Organization slug must be lowercase letters, digits, hyphens or underscores (max 64).")
        if not display_name.strip() or not owner_subject.strip():
            raise ValueError("Organization name and external owner subject are required.")
        org_id, user_id = uuid.uuid4().hex, uuid.uuid4().hex
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO users(id, external_subject, created_at) VALUES(?,?,?)",
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
            conn.execute("INSERT OR IGNORE INTO users(id,external_subject,created_at) VALUES(?,?,?)", (uuid.uuid4().hex, external_subject, _now()))
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
