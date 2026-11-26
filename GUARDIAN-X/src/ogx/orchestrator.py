"""Durable, resumable engineering workflow orchestration.

The workflow is a persisted state machine, not an in-memory pipeline:

``inspect -> plan -> edit -> test -> secure -> review -> evolve``

Every transition is written to SQLite (inside OGX state, never the project) and
mirrored to the hash-chained platform audit log. A process restart, a cancelled
run or a failed stage can be inspected, resumed and retried by a new process.

Design constraints
------------------
* A stage that changes the project or executes code requires an explicit
  approval record for that workflow *and* stage; approval is never implied by
  running the workflow.
* Stage handlers receive a bounded context and must return JSON-serializable
  data. Outputs are capped before storage.
* ``test`` runs only through the existing container sandbox with an immutable
  image reference. When Docker or the pinned image is unavailable the stage
  fails honestly; it never falls back to unsandboxed execution.
* No stage writes into the analysed project except ``edit``, which goes through
  :mod:`ogx.patching` with hash preconditions and an external backup.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import UUID, uuid4

from .config import load_config, state_dir

SCHEMA_VERSION = 2
DEFAULT_LEASE_SECONDS = 3600.0
DEFAULT_STAGES: tuple[str, ...] = ("inspect", "plan", "edit", "test", "secure", "review", "evolve")
APPROVAL_STAGES: frozenset[str] = frozenset({"edit", "test"})
MAX_STAGE_OUTPUT_CHARS = 60_000
MAX_EVENTS = 500

WORKFLOW_STATES = frozenset(
    {"created", "running", "awaiting_approval", "succeeded", "failed", "cancelled"}
)
STAGE_STATES = frozenset(
    {"pending", "running", "awaiting_approval", "succeeded", "failed", "skipped", "cancelled"}
)

_ALLOWED_WORKFLOW_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"running", "cancelled"}),
    "running": frozenset({"running", "awaiting_approval", "succeeded", "failed", "cancelled"}),
    "awaiting_approval": frozenset({"running", "failed", "cancelled"}),
    "failed": frozenset({"running", "cancelled"}),
    "succeeded": frozenset(),
    "cancelled": frozenset(),
}


class _ClosingConnection:
    """Close the SQLite handle on exit.

    ``sqlite3.Connection``'s own context manager commits but never closes the
    file, which leaves the database locked on Windows (found by the benchmark
    cleanup). Explicit COMMIT/ROLLBACK calls in the engine still apply.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                try:
                    self._connection.rollback()
                except sqlite3.Error:
                    pass  # the failing path may already have rolled back
        finally:
            self._connection.close()
        return False


class WorkflowError(RuntimeError):
    """Stable orchestrator error."""

    def __init__(self, message: str, *, code: str = "workflow_error") -> None:
        super().__init__(message)
        self.code = code


class WorkflowNotFound(WorkflowError):
    def __init__(self, workflow_id: str) -> None:
        super().__init__(f"Workflow not found: {workflow_id}", code="workflow_not_found")


class StageFailed(WorkflowError):
    def __init__(self, stage: str, message: str, *, code: str = "stage_failed") -> None:
        super().__init__(f"Stage '{stage}' failed: {message}", code=code)
        self.stage = stage


@dataclass(slots=True)
class StageContext:
    """Bounded inputs available to a stage handler."""

    workflow_id: str
    stage: str
    root: Path
    project_id: str | None
    metadata: dict[str, Any]
    outputs: dict[str, dict[str, Any]]
    state_home: Path
    correlation_id: str


StageHandler = Callable[[StageContext], Mapping[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    if len(text) > MAX_STAGE_OUTPUT_CHARS:
        return json.dumps(
            {"truncated": True, "preview": text[:MAX_STAGE_OUTPUT_CHARS]}, sort_keys=True
        )
    return text


def _capped(payload: Mapping[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, sort_keys=True, default=str)
    if len(text) <= MAX_STAGE_OUTPUT_CHARS:
        return dict(payload)
    return {
        "truncated": True,
        "original_chars": len(text),
        "preview": text[:MAX_STAGE_OUTPUT_CHARS],
    }


# --------------------------------------------------------------------------- handlers
def _finding_dict(finding: Any) -> dict[str, Any]:
    if isinstance(finding, Mapping):
        return dict(finding)
    return {
        "rule_id": getattr(finding, "rule_id", ""),
        "severity": getattr(finding, "severity", "unknown"),
        "category": getattr(finding, "category", ""),
        "path": getattr(finding, "path", None),
        "line": getattr(finding, "line", None),
        "title": getattr(finding, "title", ""),
    }


def _severity_counts(findings: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        severity = str(_finding_dict(finding).get("severity", "unknown")).lower()
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def stage_inspect(ctx: StageContext) -> Mapping[str, Any]:
    from .scanner import scan_repository

    config = load_config()
    report = scan_repository(ctx.root, config)
    findings = [_finding_dict(f) for f in report.findings]
    return {
        "scan_id": report.scan_id,
        "files_examined": report.files_examined,
        "files_skipped": report.files_skipped,
        "findings": len(findings),
        "severity": _severity_counts(findings),
        "languages": list(getattr(report.project, "languages", []) or []),
        "warnings": list(report.warnings)[:20],
        "capabilities": dict(report.capabilities),
    }


def stage_plan(ctx: StageContext) -> Mapping[str, Any]:
    from .guardian import plan_safe_fixes
    from .impact import analyze_impact

    proposals = plan_safe_fixes(ctx.root)
    inspected = (ctx.outputs.get("inspect") or {}).get("findings", 0)
    impact: dict[str, Any] = {}
    # Change-impact analysis is only meaningful with an explicit file.
    changed = ctx.metadata.get("changed_file")
    if isinstance(changed, str) and changed.strip():
        try:
            impact = analyze_impact(ctx.root, changed.strip(), load_config())
        except Exception as exc:  # noqa: BLE001 - impact is advisory
            impact = {"available": False, "error_kind": type(exc).__name__}
    return {
        "findings_seen": inspected,
        "safe_fix_proposals": len(proposals),
        "proposals": proposals[:20],
        "impact": impact,
        "patch_plan_id": ctx.metadata.get("patch_plan_id"),
    }


def stage_edit(ctx: StageContext) -> Mapping[str, Any]:
    from .patching import apply_patch

    plan_id = str(ctx.metadata.get("patch_plan_id") or "").strip()
    if not plan_id:
        raise StageFailed(
            "edit",
            "no patch plan is attached; create one with 'ogx patch plan' and pass --patch-plan",
            code="missing_patch_plan",
        )
    result = apply_patch(
        plan_id,
        approved=True,
        actor_subject=str(ctx.metadata.get("actor_subject") or "workflow"),
        state_home=ctx.state_home,
    )
    return {"patch_plan_id": plan_id, "applied": result["applied"], "backup_dir": result["backup_dir"]}


def stage_test(ctx: StageContext) -> Mapping[str, Any]:
    from .sandbox import run_in_sandbox, sandbox_plan

    command = ctx.metadata.get("test_command")
    image = str(ctx.metadata.get("sandbox_image") or "").strip()
    if not isinstance(command, list) or not command:
        raise StageFailed(
            "test",
            "no sandboxed test command configured; pass --test-command with an argument array",
            code="missing_test_command",
        )
    if not image:
        raise StageFailed(
            "test",
            "no pinned sandbox image configured; pass --sandbox-image repo@sha256:<digest>",
            code="missing_sandbox_image",
        )
    if not sandbox_plan().get("available"):
        raise StageFailed(
            "test",
            "Docker is unavailable; the workflow refuses to run project code unsandboxed",
            code="sandbox_unavailable",
        )
    result = run_in_sandbox(
        ctx.root,
        image,
        [str(item) for item in command],
        timeout=int(ctx.metadata.get("test_timeout", 300)),
    )
    return {
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "command": result["command"],
        "image": result["image"],
        "files_copied": (result.get("snapshot") or {}).get("files_copied"),
        "stdout_tail": str(result.get("stdout", ""))[-4000:],
        "stderr_tail": str(result.get("stderr", ""))[-4000:],
        "isolation": result.get("isolation"),
    }


def stage_secure(ctx: StageContext) -> Mapping[str, Any]:
    from .scanner import scan_repository
    from .platform.operations import verify_audit

    report = scan_repository(ctx.root, load_config())
    findings = [_finding_dict(f) for f in report.findings]
    security = [
        f
        for f in findings
        if str(f.get("category", "")).lower() in {"secrets", "security", "dependencies"}
        or str(f.get("severity", "")).lower() in {"critical", "high"}
    ]
    chain = verify_audit()
    return {
        "security_findings": len(security),
        "severity": _severity_counts(security),
        "top_rules": sorted({str(f.get("rule_id", "")) for f in security})[:25],
        "audit_chain": {
            "valid": bool(chain.get("valid")),
            "records": chain.get("records"),
            "chained": chain.get("chained"),
            "unchained": chain.get("unchained"),
            "errors": list(chain.get("errors") or [])[:10],
        },
    }


def stage_review(ctx: StageContext) -> Mapping[str, Any]:
    from .patching import load_plan
    from .platform.operations import verify_audit

    edit = ctx.outputs.get("edit") or {}
    plan_id = str(edit.get("patch_plan_id") or ctx.metadata.get("patch_plan_id") or "").strip()
    patch_state: dict[str, Any] = {"attached": bool(plan_id)}
    if plan_id:
        try:
            plan = load_plan(plan_id, state_home=ctx.state_home)
            patch_state.update(
                {
                    "state": plan.get("state"),
                    "files": (plan.get("summary") or {}).get("files"),
                    "paths": (plan.get("summary") or {}).get("paths", []),
                    "has_backup": bool(plan.get("state") == "applied"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - review reports, never hides
            patch_state.update({"state": "unreadable", "error_kind": type(exc).__name__})
    test = ctx.outputs.get("test") or {}
    audit = verify_audit()
    return {
        "patch": patch_state,
        "test": {
            "exit_code": test.get("exit_code"),
            "timed_out": test.get("timed_out"),
            "skipped_by_operator": bool((ctx.outputs.get("_skipped") or {}).get("test")),
        },
        "audit_chain_valid": bool(audit.get("valid")),
        "requires_human_review": True,
    }


def stage_evolve(ctx: StageContext) -> Mapping[str, Any]:
    from .platform.operations import append_audit

    directory = ctx.state_home / "workflows" / ctx.workflow_id
    directory.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "ogx-workflow-summary-1",
        "workflow_id": ctx.workflow_id,
        "project_id": ctx.project_id,
        "root": str(ctx.root),
        "generated_at": _now(),
        "stages": {name: {"status": (value or {}).get("status")} for name, value in ctx.outputs.items()},
        "outputs": ctx.outputs,
        "note": "Generated by the OGX lifecycle orchestrator; human review is still required.",
    }
    target = directory / "summary.json"
    target.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    append_audit(
        {
            "event": "workflow.evolve.summary_written",
            "workflow_id": ctx.workflow_id,
            "project_id": ctx.project_id,
            "path": str(target),
            "correlation_id": ctx.correlation_id,
        }
    )
    return {"summary_path": str(target), "change_set_versioned_by": ["git", "patch-plan"]}


DEFAULT_HANDLERS: dict[str, StageHandler] = {
    "inspect": stage_inspect,
    "plan": stage_plan,
    "edit": stage_edit,
    "test": stage_test,
    "secure": stage_secure,
    "review": stage_review,
    "evolve": stage_evolve,
}


# ---------------------------------------------------------------------------- engine
@dataclass(slots=True)
class WorkflowSpec:
    root: Path
    project_id: str | None = None
    stages: tuple[str, ...] = DEFAULT_STAGES
    skip_stages: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    """SQLite-backed workflow engine.

    One instance is safe for sequential CLI use; concurrent processes coordinate
    through SQLite transactions and a ``running`` claim, so two runs of the same
    workflow cannot execute stages simultaneously.
    """

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        handlers: Mapping[str, StageHandler] | None = None,
    ) -> None:
        self.state_home = Path(db_path).parent if db_path else state_dir() / "workflows"
        self.db_path = Path(db_path) if db_path else self.state_home / "orchestrator.db"
        self.handlers = dict(DEFAULT_HANDLERS)
        if handlers:
            self.handlers.update(handlers)
        self._initialize()

    # ------------------------------------------------------------------ storage
    def _connect(self) -> _ClosingConnection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return _ClosingConnection(connection)

    def _initialize(self) -> None:
        if self.db_path.is_symlink():
            raise WorkflowError("Workflow database must not be a symlink.", code="state_symlink")
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    project_id TEXT,
                    project_root TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_stage TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS stages (
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 2,
                    requires_approval INTEGER NOT NULL DEFAULT 0,
                    approved_by TEXT,
                    approved_at TEXT,
                    idempotency_key TEXT,
                    output TEXT,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    PRIMARY KEY (workflow_id, name)
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    stage TEXT,
                    detail TEXT NOT NULL DEFAULT '{}',
                    correlation_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_workflow ON events(workflow_id, seq);
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'version'", (str(SCHEMA_VERSION),)
            )
            # Schema v2 adds the run lease. Additive migration: an existing
            # database keeps its workflows and simply gains the two columns.
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(workflows)")
            }
            for column in ("run_token", "lease_expires_at"):
                if column not in columns:
                    connection.execute(f"ALTER TABLE workflows ADD COLUMN {column} TEXT")

    def _event(
        self,
        connection: sqlite3.Connection,
        workflow_id: str,
        event: str,
        *,
        stage: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO events(workflow_id, timestamp, event, stage, detail, correlation_id)"
            " VALUES(?,?,?,?,?,?)",
            (
                workflow_id,
                _now(),
                event,
                stage,
                json.dumps(detail or {}, sort_keys=True, default=str),
                workflow_id,
            ),
        )
        try:
            from .platform.operations import append_audit

            append_audit(
                {
                    "event": f"workflow.{event}",
                    "workflow_id": workflow_id,
                    "stage": stage,
                    "detail": dict(detail or {}),
                    "correlation_id": workflow_id,
                }
            )
        except Exception:
            # The workflow database is authoritative; a failed audit mirror is
            # reported by 'review' (chain verification) rather than aborting work.
            pass

    def _lease_deadline(self, seconds: float) -> str:
        return (
            datetime.now(timezone.utc) + timedelta(seconds=max(0.0, float(seconds)))
        ).isoformat()

    @staticmethod
    def _lease_expired(row: sqlite3.Row) -> bool:
        """A missing or unparsable lease means the previous owner is gone."""

        raw = row["lease_expires_at"]
        if not raw:
            return True
        try:
            deadline = datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= deadline

    def _get(self, connection: sqlite3.Connection, workflow_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            raise WorkflowNotFound(workflow_id)
        return row

    # ------------------------------------------------------------------ creation
    def create(
        self,
        root: Path | str,
        *,
        project_id: str | None = None,
        workflow_id: str | None = None,
        idempotency_key: str | None = None,
        stages: Sequence[str] = DEFAULT_STAGES,
        skip_stages: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_root = Path(root).expanduser()
        try:
            project_root = project_root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkflowError(f"Project root cannot be resolved: {root}", code="root_invalid") from exc
        if not project_root.is_dir():
            raise WorkflowError(f"Project root is not a directory: {root}", code="root_invalid")

        selected = tuple(stages)
        if not selected:
            raise WorkflowError("A workflow needs at least one stage.", code="empty_workflow")
        unknown = [name for name in selected if name not in self.handlers]
        if unknown:
            raise WorkflowError(
                f"No handler is registered for stage(s): {', '.join(unknown)}", code="unknown_stage"
            )
        skipped = tuple(dict.fromkeys(str(name) for name in skip_stages))
        unknown_skip = [name for name in skipped if name not in selected]
        if unknown_skip:
            raise WorkflowError(
                f"Cannot skip stage(s) outside the workflow: {', '.join(unknown_skip)}",
                code="invalid_skip",
            )

        wid = str(workflow_id or uuid4())
        try:
            UUID(wid)
        except (ValueError, TypeError) as exc:
            raise WorkflowError("workflow_id must be a UUID.", code="invalid_workflow_id") from exc

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM workflows WHERE workflow_id = ?", (wid,)
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    return {"created": False, "workflow": self._workflow_dict(connection, existing)}
                if idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM workflows WHERE idempotency_key = ?", (idempotency_key,)
                    ).fetchone()
                    if existing is not None:
                        connection.execute("COMMIT")
                        return {"created": False, "workflow": self._workflow_dict(connection, existing)}
                timestamp = _now()
                connection.execute(
                    "INSERT INTO workflows(workflow_id, idempotency_key, project_id, project_root, state,"
                    " current_stage, revision, metadata, created_at, updated_at)"
                    " VALUES(?,?,?,?,'created',?,0,?,?,?)",
                    (
                        wid,
                        idempotency_key,
                        project_id,
                        str(project_root),
                        selected[0],
                        json.dumps(dict(metadata or {}), sort_keys=True, default=str),
                        timestamp,
                        timestamp,
                    ),
                )
                for ordinal, name in enumerate(selected):
                    connection.execute(
                        "INSERT INTO stages(workflow_id, name, ordinal, state, requires_approval)"
                        " VALUES(?,?,?,?,?)",
                        (wid, name, ordinal, "skipped" if name in skipped else "pending",
                         int(name in APPROVAL_STAGES)),
                    )
                self._event(connection, wid, "created", detail={"stages": list(selected), "skipped": list(skipped)})
                row = self._get(connection, wid)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return {"created": True, "workflow": self._workflow_dict(connection, row)}

    # --------------------------------------------------------------- serialization
    def _stage_rows(self, connection: sqlite3.Connection, workflow_id: str) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                "SELECT * FROM stages WHERE workflow_id = ? ORDER BY ordinal", (workflow_id,)
            )
        )

    def _workflow_dict(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        stages = []
        for stage in self._stage_rows(connection, row["workflow_id"]):
            stages.append(
                {
                    "name": stage["name"],
                    "state": stage["state"],
                    "ordinal": stage["ordinal"],
                    "attempts": stage["attempts"],
                    "max_attempts": stage["max_attempts"],
                    "requires_approval": bool(stage["requires_approval"]),
                    "approved_by": stage["approved_by"],
                    "approved_at": stage["approved_at"],
                    "error": stage["error"],
                    "started_at": stage["started_at"],
                    "finished_at": stage["finished_at"],
                    "output": json.loads(stage["output"]) if stage["output"] else None,
                }
            )
        return {
            "workflow_id": row["workflow_id"],
            "idempotency_key": row["idempotency_key"],
            "project_id": row["project_id"],
            "project_root": row["project_root"],
            "state": row["state"],
            "current_stage": row["current_stage"],
            "revision": row["revision"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "finished_at": row["finished_at"],
            "stages": stages,
        }

    def status(self, workflow_id: str, *, events: int = 20) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._get(connection, workflow_id)
            payload = self._workflow_dict(connection, row)
            payload["events"] = self.events(workflow_id, limit=events)
            return payload

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT * FROM workflows ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(int(limit), 500)),),
                )
            )
            return [
                {
                    "workflow_id": row["workflow_id"],
                    "project_id": row["project_id"],
                    "project_root": row["project_root"],
                    "state": row["state"],
                    "current_stage": row["current_stage"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]

    def events(self, workflow_id: str, *, limit: int = 100, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._get(connection, workflow_id)
            rows = list(
                connection.execute(
                    "SELECT * FROM events WHERE workflow_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                    (workflow_id, int(after), max(1, min(int(limit), MAX_EVENTS))),
                )
            )
            return [
                {
                    "seq": row["seq"],
                    "timestamp": row["timestamp"],
                    "event": row["event"],
                    "stage": row["stage"],
                    "detail": json.loads(row["detail"] or "{}"),
                    "correlation_id": row["correlation_id"],
                }
                for row in rows
            ]

    # -------------------------------------------------------------------- control
    def approve(self, workflow_id: str, stage: str, *, actor: str, note: str = "") -> dict[str, Any]:
        if not str(actor or "").strip():
            raise WorkflowError("An approver identity is required.", code="missing_actor")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._get(connection, workflow_id)
                row = connection.execute(
                    "SELECT * FROM stages WHERE workflow_id = ? AND name = ?", (workflow_id, stage)
                ).fetchone()
                if row is None:
                    raise WorkflowError(f"Unknown stage: {stage}", code="unknown_stage")
                if not row["requires_approval"]:
                    raise WorkflowError(f"Stage '{stage}' does not require approval.", code="approval_not_required")
                if row["state"] not in {"pending", "awaiting_approval", "failed"}:
                    raise WorkflowError(
                        f"Stage '{stage}' is {row['state']}; it cannot be approved now.",
                        code="invalid_stage_state",
                    )
                timestamp = _now()
                connection.execute(
                    "UPDATE stages SET state = 'pending', approved_by = ?, approved_at = ?,"
                    " attempts = CASE WHEN state = 'failed' THEN 0 ELSE attempts END"
                    " WHERE workflow_id = ? AND name = ?",
                    (str(actor).strip(), timestamp, workflow_id, stage),
                )
                self._event(
                    connection,
                    workflow_id,
                    "stage.approved",
                    stage=stage,
                    detail={"actor": str(actor).strip(), "note": str(note)[:500]},
                )
                payload = self._workflow_dict(connection, self._get(connection, workflow_id))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return payload

    def cancel(self, workflow_id: str, *, actor: str, reason: str = "") -> dict[str, Any]:
        if not str(actor or "").strip():
            raise WorkflowError("An actor identity is required.", code="missing_actor")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._get(connection, workflow_id)
                if row["state"] in {"succeeded", "cancelled"}:
                    raise WorkflowError(
                        f"Workflow is already {row['state']}.", code="invalid_workflow_state"
                    )
                timestamp = _now()
                connection.execute(
                    "UPDATE workflows SET state = 'cancelled', finished_at = ?, updated_at = ?,"
                    " run_token = NULL, lease_expires_at = NULL,"
                    " revision = revision + 1 WHERE workflow_id = ?",
                    (timestamp, timestamp, workflow_id),
                )
                connection.execute(
                    "UPDATE stages SET state = 'cancelled'"
                    " WHERE workflow_id = ? AND state IN ('pending','awaiting_approval','running')",
                    (workflow_id,),
                )
                self._event(
                    connection,
                    workflow_id,
                    "cancelled",
                    detail={"actor": str(actor).strip(), "reason": str(reason)[:500]},
                )
                payload = self._workflow_dict(connection, self._get(connection, workflow_id))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return payload

    def retry(self, workflow_id: str, *, stage: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._get(connection, workflow_id)
                if row["state"] == "cancelled":
                    raise WorkflowError("A cancelled workflow cannot be retried.", code="invalid_workflow_state")
                target = stage or row["current_stage"]
                if not target:
                    raise WorkflowError("No failed stage to retry.", code="nothing_to_retry")
                stage_row = connection.execute(
                    "SELECT * FROM stages WHERE workflow_id = ? AND name = ?", (workflow_id, target)
                ).fetchone()
                if stage_row is None:
                    raise WorkflowError(f"Unknown stage: {target}", code="unknown_stage")
                if stage_row["state"] != "failed":
                    raise WorkflowError(
                        f"Stage '{target}' is {stage_row['state']}; only a failed stage can be retried.",
                        code="invalid_stage_state",
                    )
                if stage_row["attempts"] >= stage_row["max_attempts"]:
                    raise WorkflowError(
                        f"Stage '{target}' reached its attempt limit ({stage_row['max_attempts']});"
                        " approve a new attempt explicitly by recreating the workflow.",
                        code="attempts_exhausted",
                    )
                connection.execute(
                    "UPDATE stages SET state = 'pending', error = NULL, finished_at = NULL"
                    " WHERE workflow_id = ? AND name = ?",
                    (workflow_id, target),
                )
                connection.execute(
                    "UPDATE workflows SET state = 'created', finished_at = NULL, updated_at = ?,"
                    " run_token = NULL, lease_expires_at = NULL,"
                    " revision = revision + 1 WHERE workflow_id = ?",
                    (_now(), workflow_id),
                )
                self._event(connection, workflow_id, "retry", stage=target)
                payload = self._workflow_dict(connection, self._get(connection, workflow_id))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return payload

    # ------------------------------------------------------------------------ run
    def run(
        self,
        workflow_id: str,
        *,
        max_stages: int | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Execute pending stages until completion, failure or an approval gate.

        A run holds a lease. A second runner is refused while the lease is
        valid; once it expires (the owner crashed), the stale ``running`` stage
        is reset and the workflow resumes instead of being stuck forever.
        """

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._get(connection, workflow_id)
                if row["state"] in {"succeeded", "cancelled"}:
                    connection.execute("COMMIT")
                    return {"workflow": self._workflow_dict(connection, row), "executed": []}
                reclaimed = False
                if row["state"] == "running":
                    if not self._lease_expired(row):
                        raise WorkflowError(
                            "Workflow is already running elsewhere and its lease has not expired.",
                            code="concurrent_run",
                        )
                    connection.execute(
                        "UPDATE stages SET state = 'pending', started_at = NULL"
                        " WHERE workflow_id = ? AND state = 'running'",
                        (workflow_id,),
                    )
                    reclaimed = True
                connection.execute(
                    "UPDATE workflows SET state = 'running', run_token = ?, lease_expires_at = ?,"
                    " updated_at = ?, revision = revision + 1 WHERE workflow_id = ?",
                    (uuid4().hex, self._lease_deadline(lease_seconds), _now(), workflow_id),
                )
                if reclaimed:
                    self._event(
                        connection,
                        workflow_id,
                        "run.reclaimed",
                        detail={"stale_running_stage_reset": True},
                    )
                self._event(
                    connection, workflow_id, "run.started", detail={"reclaimed": reclaimed}
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        executed: list[str] = []
        limit = max_stages if max_stages is None else max(1, int(max_stages))
        while limit is None or len(executed) < limit:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = self._get(connection, workflow_id)
                    if row["state"] in {"succeeded", "cancelled", "failed"}:
                        connection.execute("COMMIT")
                        break
                    stage_row = connection.execute(
                        "SELECT * FROM stages WHERE workflow_id = ? AND state = 'pending'"
                        " ORDER BY ordinal LIMIT 1",
                        (workflow_id,),
                    ).fetchone()
                    if stage_row is None:
                        awaiting = connection.execute(
                            "SELECT * FROM stages WHERE workflow_id = ? AND state = 'awaiting_approval'"
                            " ORDER BY ordinal LIMIT 1",
                            (workflow_id,),
                        ).fetchone()
                        timestamp = _now()
                        if awaiting is not None:
                            connection.execute(
                                "UPDATE workflows SET state = 'awaiting_approval', current_stage = ?,"
                                " updated_at = ?, run_token = NULL, lease_expires_at = NULL"
                                " WHERE workflow_id = ?",
                                (awaiting["name"], timestamp, workflow_id),
                            )
                            self._event(
                                connection,
                                workflow_id,
                                "workflow.awaiting_approval",
                                stage=awaiting["name"],
                            )
                        else:
                            connection.execute(
                                "UPDATE workflows SET state = 'succeeded', finished_at = ?,"
                                " updated_at = ?, run_token = NULL, lease_expires_at = NULL,"
                                " revision = revision + 1 WHERE workflow_id = ?",
                                (timestamp, timestamp, workflow_id),
                            )
                            self._event(connection, workflow_id, "succeeded")
                        payload = self._workflow_dict(connection, self._get(connection, workflow_id))
                        connection.execute("COMMIT")
                        break

                    name = stage_row["name"]
                    if stage_row["requires_approval"] and not stage_row["approved_by"]:
                        connection.execute(
                            "UPDATE stages SET state = 'awaiting_approval' WHERE workflow_id = ? AND name = ?",
                            (workflow_id, name),
                        )
                        connection.execute(
                            "UPDATE workflows SET state = 'awaiting_approval', current_stage = ?,"
                            " updated_at = ?, run_token = NULL, lease_expires_at = NULL"
                            " WHERE workflow_id = ?",
                            (name, _now(), workflow_id),
                        )
                        self._event(connection, workflow_id, "stage.awaiting_approval", stage=name)
                        payload = self._workflow_dict(connection, self._get(connection, workflow_id))
                        connection.execute("COMMIT")
                        break

                    connection.execute(
                        "UPDATE stages SET state = 'running', attempts = attempts + 1, started_at = ?"
                        " WHERE workflow_id = ? AND name = ?",
                        (_now(), workflow_id, name),
                    )
                    connection.execute(
                        "UPDATE workflows SET current_stage = ?, updated_at = ?,"
                        " lease_expires_at = ? WHERE workflow_id = ?",
                        (name, _now(), self._lease_deadline(lease_seconds), workflow_id),
                    )
                    self._event(connection, workflow_id, "stage.started", stage=name)
                    metadata = json.loads(row["metadata"] or "{}")
                    outputs = self._stage_outputs(connection, workflow_id)
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise

            executed.append(name)
            handler = self.handlers[name]
            context = StageContext(
                workflow_id=workflow_id,
                stage=name,
                root=Path(row["project_root"]),
                project_id=row["project_id"],
                metadata=metadata,
                outputs=outputs,
                state_home=self.state_home,
                correlation_id=workflow_id,
            )
            try:
                result = handler(context)
                if not isinstance(result, Mapping):
                    raise StageFailed(name, "handler did not return a mapping", code="invalid_output")
            except Exception as exc:  # noqa: BLE001 - a stage failure is data
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(
                            "UPDATE stages SET state = 'failed', error = ?, finished_at = ?"
                            " WHERE workflow_id = ? AND name = ?",
                            (f"{getattr(exc, 'code', type(exc).__name__)}: {str(exc)[:1500]}",
                             _now(), workflow_id, name),
                        )
                        connection.execute(
                            "UPDATE workflows SET state = 'failed', current_stage = ?, updated_at = ?,"
                            " run_token = NULL, lease_expires_at = NULL,"
                            " revision = revision + 1 WHERE workflow_id = ?",
                            (name, _now(), workflow_id),
                        )
                        self._event(
                            connection,
                            workflow_id,
                            "stage.failed",
                            stage=name,
                            detail={
                                "code": getattr(exc, "code", type(exc).__name__),
                                "error": str(exc)[:1500],
                            },
                        )
                        connection.execute("COMMIT")
                    except Exception:
                        connection.execute("ROLLBACK")
                        raise
                break

            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        "UPDATE stages SET state = 'succeeded', output = ?, finished_at = ?"
                        " WHERE workflow_id = ? AND name = ?",
                        (_json(_capped(dict(result))), _now(), workflow_id, name),
                    )
                    self._event(
                        connection,
                        workflow_id,
                        "stage.succeeded",
                        stage=name,
                        detail={"keys": sorted(result)},
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise

        if limit is not None and len(executed) >= limit:
            # A bounded call must not leave a live lease behind: release it so
            # the next invocation can continue immediately.
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = self._get(connection, workflow_id)
                    if row["state"] == "running":
                        connection.execute(
                            "UPDATE workflows SET state = 'created', updated_at = ?,"
                            " run_token = NULL, lease_expires_at = NULL WHERE workflow_id = ?",
                            (_now(), workflow_id),
                        )
                        self._event(
                            connection, workflow_id, "run.paused", detail={"max_stages": limit}
                        )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise

        with self._connect() as connection:
            return {
                "workflow": self._workflow_dict(connection, self._get(connection, workflow_id)),
                "executed": executed,
            }

    def _stage_outputs(self, connection: sqlite3.Connection, workflow_id: str) -> dict[str, dict[str, Any]]:
        outputs: dict[str, dict[str, Any]] = {}
        for row in self._stage_rows(connection, workflow_id):
            if row["output"]:
                try:
                    outputs[row["name"]] = json.loads(row["output"])
                except json.JSONDecodeError:
                    outputs[row["name"]] = {"unreadable": True}
        skipped = {row["name"]: True for row in self._stage_rows(connection, workflow_id) if row["state"] == "skipped"}
        if skipped:
            outputs["_skipped"] = skipped
        return outputs
