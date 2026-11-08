from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from .mailer import build_approval_email, build_mailer
from .store import EnterpriseStore

ALLOWED_OPERATIONS = {"guardian.repair", "guardian.upgrade_fetch", "integration.apply", "integration.remove", "repo.clone", "repo.branch_create", "repo.push", "platform.pr"}
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _code_hash(salt: str, code: str) -> str:
    return hashlib.sha256((salt + code).encode("utf-8")).hexdigest()


def _approver_config() -> tuple[str, str]:
    email = os.environ.get("OGX_APPROVER_EMAIL", "").strip()
    subject = os.environ.get("OGX_APPROVER_SUBJECT", "").strip()
    if not email or not _EMAIL_RE.fullmatch(email):
        raise ValueError("Set OGX_APPROVER_EMAIL to the designated approval mailbox.")
    if not subject:
        raise ValueError("Set OGX_APPROVER_SUBJECT to the designated approver's external identity subject.")
    return email, subject


def validate_operation(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(f"Unsupported approval operation: {operation}")
    if operation == "repo.clone":
        url = payload.get("url")
        destination = payload.get("destination")
        if not isinstance(url, str) or not isinstance(destination, str):
            raise ValueError("Repository clone approval requires a URL and destination.")
        parsed = urlsplit(url.strip())
        if parsed.scheme not in {"https", "http", "ssh", "git"} or not parsed.hostname:
            raise ValueError("Clone URL must use a supported Git URL scheme and host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Clone URLs must not contain embedded credentials, query strings or fragments.")
        target = Path(destination).expanduser()
        if target.is_symlink():
            raise ValueError("Clone destination must not be a symlink.")
        parent = target.parent.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError("Clone destination parent must be an existing directory.")
        normalized = parent / target.name
        if normalized.exists() and (not normalized.is_dir() or any(normalized.iterdir())):
            raise ValueError("Clone destination must not exist or must be an empty directory.")
        return {"url": url.strip(), "destination": str(normalized)}

    if operation == "platform.pr":
        provider = payload.get("provider")
        url = payload.get("url")
        title = payload.get("title")
        body = payload.get("body", "Created by OMEGA GUARDIAN X after approval.")
        head = payload.get("head")
        base = payload.get("base")
        if provider not in {"github", "gitlab", "bitbucket", "gitea", "forgejo", "gitea-or-forgejo"}:
            raise ValueError("Unsupported Git hosting provider.")
        parsed_url = urlsplit(url) if isinstance(url, str) else None
        if (not parsed_url or parsed_url.scheme not in {"https", "http"} or not parsed_url.hostname
                or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment):
            raise ValueError("Repository URL must be HTTP(S), valid and free of embedded credentials/query/fragment.")
        if not isinstance(title, str) or not title.strip() or len(title) > 256:
            raise ValueError("Pull/Merge Request title must be 1-256 characters.")
        if not isinstance(body, str) or len(body) > 20000:
            raise ValueError("Pull/Merge Request body must not exceed 20,000 characters.")
        for label, branch in (("head", head), ("base", base)):
            if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch):
                raise ValueError(f"Invalid {label} branch name.")
        return {"provider": provider, "url": url, "title": title.strip(),
                "body": body, "head": head, "base": base}

    path = payload.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Approval payload requires a project path.")
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Approval project path must be an existing directory.")
    result: dict[str, Any] = {"path": str(root)}
    if operation == "guardian.repair":
        from ..guardian import plan_safe_fixes
        current_plan = plan_safe_fixes(root)
        prior_plan = payload.get("fix_plan")
        if prior_plan is not None and prior_plan != current_plan:
            raise PermissionError("The safe repair plan changed after the email approval request was created.")
        if not current_plan or any(item.get("status") == "blocked" for item in current_plan):
            raise ValueError("No safe repair can be approved, or the current repair plan is blocked.")
        result["fix_plan"] = current_plan
    elif operation == "repo.branch_create":
        from ..connectors.git import _run
        current = _run(["rev-parse", "HEAD"], cwd=root)
        if current.returncode != 0:
            raise ValueError("A valid Git repository with a commit is required for branch creation.")
        commit = current.stdout.strip()
        approved = payload.get("base_commit")
        if approved is not None and approved != commit:
            raise PermissionError("Git HEAD changed after the branch creation approval was requested.")
        result["name"] = payload.get("name")
        result["base_commit"] = commit
        name = result["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", name):
            raise ValueError("Invalid feature branch name.")
        if name in {"main", "master", "develop"} or name.startswith("-"):
            raise ValueError("Protected/default branch names cannot be created by this action.")
    elif operation == "repo.push":
        from ..connectors.git import _run
        branch = payload.get("branch")
        remote = payload.get("remote", "origin")
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch):
            raise ValueError("Invalid Git branch name.")
        if branch in {"main", "master"}:
            raise ValueError("OGX refuses direct pushes to main/master.")
        if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", remote):
            raise ValueError("Invalid Git remote name.")
        current = _run(["rev-parse", f"refs/heads/{branch}"], cwd=root)
        if current.returncode != 0:
            raise ValueError("The approved feature branch does not exist locally.")
        commit = current.stdout.strip()
        approved = payload.get("commit_sha")
        if approved is not None and approved != commit:
            raise PermissionError("The feature branch commit changed after email approval was requested.")
        result.update({"branch": branch, "remote": remote, "commit_sha": commit})
    elif operation == "guardian.upgrade_fetch":
        remote = payload.get("remote", "origin")
        if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", remote):
            raise ValueError("Git remote name contains unsupported characters.")
        result["remote"] = remote
    return result


class ApprovalManager:
    """One-time email approval ledger; email possession plus an external subject claim."""

    def __init__(self, store: EnterpriseStore | None = None, mailer=None):
        self.store = store or EnterpriseStore()
        self.mailer = mailer

    @contextmanager
    def _connection(self):
        conn = self.store._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _event(conn: sqlite3.Connection, request_id: str, actor: str, event: str,
               details: dict[str, Any] | None = None) -> None:
        conn.execute(
            "INSERT INTO approval_events(id,request_id,actor_subject,event_type,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, request_id, actor, event, _iso(_now()), json.dumps(details or {}, sort_keys=True)),
        )

    def create(self, *, operation: str, payload: dict[str, Any], requester_subject: str,
               summary: str, ttl_minutes: int | None = None) -> dict[str, Any]:
        if not requester_subject.strip():
            raise ValueError("Requester external identity subject is required.")
        approver_email, approver_subject = _approver_config()
        if requester_subject == approver_subject:
            raise PermissionError("The requester cannot be the configured approver (four-eyes rule).")
        if not summary.strip() or len(summary) > 2000:
            raise ValueError("Summary must contain 1-2000 characters.")
        safe_payload = validate_operation(operation, payload)
        ttl = ttl_minutes if ttl_minutes is not None else int(os.environ.get("OGX_APPROVAL_TTL_MINUTES", "30"))
        if not 5 <= ttl <= 1440:
            raise ValueError("Approval expiry must be between 5 and 1440 minutes.")
        request_id = uuid.uuid4().hex
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
        salt = secrets.token_hex(16)
        expires = _now() + timedelta(minutes=ttl)
        target_name = safe_payload.get("path") or safe_payload.get("destination") or safe_payload.get("url", "project")
        project = Path(target_name).name if safe_payload.get("path") or safe_payload.get("destination") else str(target_name)
        subject, text_body, html_body = build_approval_email(
            request_id=request_id, operation=operation, project=project,
            summary=summary, code=code, expires_at=_iso(expires), details=safe_payload,
        )
        store = self.store
        store.initialize()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO approval_requests(id,operation,payload_json,summary,requester_subject,approver_subject,approver_email,code_salt,code_hash,status,created_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, operation, json.dumps(safe_payload, sort_keys=True), summary.strip(),
                 requester_subject, approver_subject, approver_email, salt, _code_hash(salt, code),
                 "sending", _iso(_now()), _iso(expires)),
            )
            self._event(conn, request_id, requester_subject, "request.created", {"operation": operation})
        try:
            mailer = self.mailer or build_mailer()
            receipt = mailer.send(recipient=approver_email, subject=subject,
                                  text_body=text_body, html_body=html_body)
        except Exception as exc:
            with self._connection() as conn:
                conn.execute("UPDATE approval_requests SET status='delivery_failed', delivery_error=? WHERE id=? AND status='sending'",
                             (f"{type(exc).__name__}: {str(exc)[:400]}", request_id))
                self._event(conn, request_id, "system", "email.failed", {"error_type": type(exc).__name__})
            raise RuntimeError(f"Approval email was not confirmed as sent; request is disabled: {exc}") from exc
        with self._connection() as conn:
            conn.execute("UPDATE approval_requests SET status='pending', mail_provider=?, mail_message_id=? WHERE id=? AND status='sending'",
                         (receipt.provider, receipt.message_id, request_id))
            self._event(conn, request_id, "system", "email.sent", {"provider": receipt.provider, "message_id": receipt.message_id})
        return {"request_id": request_id, "status": "pending", "operation": operation,
                "project": project, "approver_email": approver_email, "expires_at": _iso(expires),
                "message_id": receipt.message_id}

    def list_requests(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.store.initialize()
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000.")
        with self._connection() as conn:
            conn.execute("UPDATE approval_requests SET status='expired' WHERE status='pending' AND expires_at<=?", (_iso(_now()),))
            rows = conn.execute(
                "SELECT id,operation,summary,requester_subject,approver_subject,status,created_at,expires_at,resolved_at,executed_at,mail_provider "
                "FROM approval_requests ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def decide(self, *, request_id: str, code: str, approver_subject: str, decision: str) -> dict[str, Any]:
        self.store.initialize()
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected.")
        if not code or len(code) != 12:
            raise ValueError("A valid 12-character one-time email code is required.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise ValueError("Approval request not found.")
            if row["status"] != "pending":
                raise ValueError(f"Request is not pending (current status: {row['status']}).")
            if row["locked_at"] is not None or row["failed_attempts"] >= 5:
                raise PermissionError("Approval request is locked after too many invalid code attempts.")
            if row["expires_at"] <= _iso(_now()):
                conn.execute("UPDATE approval_requests SET status='expired' WHERE id=? AND status='pending'", (request_id,))
                raise ValueError("Approval request has expired.")
            if approver_subject != row["approver_subject"]:
                raise PermissionError("Approver identity does not match the configured approver.")
            if approver_subject == row["requester_subject"]:
                raise PermissionError("Requester and approver must be different identities.")
            expected = row["code_hash"]
            if not hmac.compare_digest(_code_hash(row["code_salt"], code), expected):
                attempts = int(row["failed_attempts"]) + 1
                locked_at = _iso(_now()) if attempts >= 5 else None
                conn.execute("UPDATE approval_requests SET failed_attempts=?,locked_at=? WHERE id=? AND status='pending'",
                             (attempts, locked_at, request_id))
                self._event(conn, request_id, approver_subject, "request.invalid_code", {"attempt": attempts})
                conn.commit()
                raise PermissionError("Approval code is invalid." + (" Request locked." if locked_at else ""))
            cursor = conn.execute(
                "UPDATE approval_requests SET status=?,resolved_at=?,resolved_by=? WHERE id=? AND status='pending'",
                (decision, _iso(_now()), approver_subject, request_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Approval request was concurrently modified; reload its status.")
            self._event(conn, request_id, approver_subject, f"request.{decision}")
            return {"request_id": request_id, "status": decision, "resolved_by": approver_subject}

    def claim_execution(self, *, request_id: str, requester_subject: str) -> dict[str, Any]:
        self.store.initialize()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise ValueError("Approval request not found.")
            if row["status"] != "approved":
                raise PermissionError(f"Execution requires approved status; current status is {row['status']}.")
            if row["requester_subject"] != requester_subject:
                raise PermissionError("Only the original requester may execute this approved action.")
            payload = json.loads(row["payload_json"])
            validated = validate_operation(row["operation"], payload)
            if validated != payload:
                raise PermissionError("The approved action payload changed after the request was created.")
            cursor = conn.execute("UPDATE approval_requests SET status='executing' WHERE id=? AND status='approved'", (request_id,))
            if cursor.rowcount != 1:
                raise RuntimeError("Approval request was concurrently claimed; execution is single-use.")
            self._event(conn, request_id, requester_subject, "execution.started", {"operation": row["operation"]})
            return {"operation": row["operation"], "payload": payload,
                    "summary": row["summary"]}

    def finish_execution(self, request_id: str, *, success: bool, result_summary: str) -> None:
        self.store.initialize()
        status = "executed" if success else "execution_failed"
        with self._connection() as conn:
            conn.execute("UPDATE approval_requests SET status=?,executed_at=?,execution_summary=? WHERE id=? AND status='executing'",
                         (status, _iso(_now()), result_summary[:2000], request_id))
            self._event(conn, request_id, "system", f"execution.{status}", {"summary": result_summary[:500]})

