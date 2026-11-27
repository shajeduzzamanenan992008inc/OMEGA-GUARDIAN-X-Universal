"""Approval-gated write operations and the platform audit trail.

Reads are always allowed. Every state-changing remote operation must pass a
validated plan, an explicit approval decision, a capability/permission check and
an audit record. Destructive operations (delete repository, merge PR, force
push, permission changes) have no implementation at all.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import append_chained, verify_chain
from .base import ProviderAdapter
from .capabilities import Capability
from .errors import (
    ApprovalRequiredError,
    CapabilityUnavailableError,
    PermissionDeniedError,
)
from .models import FileChange, OperationResult, WritePlan
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_PROTECTED = {"main", "master", "develop", "trunk"}

#: Operations this engine refuses to implement at all.
FORBIDDEN_OPERATIONS = frozenset(
    {
        "repository.delete",
        "branch.delete",
        "pull_request.merge",
        "permission.update",
        "force_push",
        "deployment.production",
        "cloud.apply",
    }
)


def _validate_branch(label: str, name: str) -> str:
    if not isinstance(name, str) or not _BRANCH_RE.fullmatch(name):
        raise ValueError(f"Invalid {label} branch name.")
    if (
        ".." in name
        or "//" in name
        or name.endswith(("/", ".", ".lock"))
        or "@{" in name
    ):
        raise ValueError(f"Invalid {label} branch name.")
    if name.startswith("-"):
        raise ValueError(f"Invalid {label} branch name.")
    return name


def plan_pull_request(
    adapter: ProviderAdapter,
    repository: str,
    *,
    title: str,
    body: str,
    head: str,
    base: str,
) -> WritePlan:
    """Validate a pull/merge request plan without contacting the remote API."""

    state = adapter.capability_state(Capability.PULL_REQUEST_CREATE)
    if not state.is_available:
        raise CapabilityUnavailableError(
            f"{adapter.provider_id} cannot create pull/merge requests: {state.value}.",
            provider=adapter.provider_id,
            capability=Capability.PULL_REQUEST_CREATE.value,
        )
    if not isinstance(title, str) or not title.strip() or len(title) > 256:
        raise ValueError("Pull/Merge Request title must be 1-256 characters.")
    if not isinstance(body, str) or len(body) > 20000:
        raise ValueError("Pull/Merge Request body must not exceed 20,000 characters.")
    _validate_branch("head", head)
    _validate_branch("base", base)
    if head == base:
        raise ValueError("Head and base must differ.")
    if head in _PROTECTED:
        raise PermissionDeniedError(
            "Head must be a dedicated feature branch, never a protected/default branch.",
            provider=adapter.provider_id,
        )
    return WritePlan(
        operation=Capability.PULL_REQUEST_CREATE.value,
        provider_id=adapter.provider_id,
        repository=repository,
        summary=f"Create a pull/merge request '{title.strip()}' from '{head}' into '{base}'.",
        payload={"title": title.strip(), "body": body, "head": head, "base": base},
        required_permission="pull_requests:write",
        destructive=False,
    )


def execute_pull_request(
    plan: WritePlan,
    adapter: ProviderAdapter,
    *,
    approved: bool,
    actor_subject: str | None = None,
    audit_path: Path | None = None,
) -> OperationResult:
    """Execute an approved plan. Unapproved execution is always refused."""

    if plan.operation in FORBIDDEN_OPERATIONS:
        raise PermissionDeniedError(
            f"Operation '{plan.operation}' is never performed by OGX.",
            provider=plan.provider_id,
        )
    if not approved:
        raise ApprovalRequiredError(
            "Remote state changes require explicit user approval and a validated plan.",
            provider=plan.provider_id,
            capability=plan.operation,
        )
    if plan.provider_id != adapter.provider_id:
        raise PermissionDeniedError("Plan/provider mismatch; refusing execution.", provider=plan.provider_id)

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "platform.write.requested",
        "operation": plan.operation,
        "provider_id": plan.provider_id,
        "repository": plan.repository,
        "actor_subject": actor_subject or os.environ.get("OGX_ACTOR_SUBJECT", ""),
        "approved": True,
    }
    append_audit(entry, path=audit_path)

    try:
        result = adapter.create_pull_request(
            plan.repository,
            title=str(plan.payload["title"]),
            body=str(plan.payload["body"]),
            head=str(plan.payload["head"]),
            base=str(plan.payload["base"]),
        )
    except Exception as exc:
        append_audit(
            {
                **entry,
                "event": "platform.write.failed",
                "error_kind": getattr(exc, "kind", type(exc).__name__),
            },
            path=audit_path,
        )
        raise

    append_audit(
        {
            **entry,
            "event": "platform.write.completed",
            "pull_request_id": result.pull_request_id,
            "url": result.url,
        },
        path=audit_path,
    )
    return OperationResult(
        operation=plan.operation,
        provider_id=plan.provider_id,
        ok=True,
        correlation_id=adapter.transport.last_correlation_id,
        result=result.to_dict(),
    )


#: Bounds for an approval-gated file commit.
MAX_FILES_PER_COMMIT = 20
MAX_FILE_BYTES = 1024 * 1024
MAX_COMMIT_BYTES = 4 * 1024 * 1024
MAX_COMMIT_MESSAGE = 2000
_AUDIT_RESULT_KEYS = frozenset(
    {
        "commit_sha",
        "tree_sha",
        "parent_sha",
        "branch",
        "ref",
        "sha",
        "previous_sha",
        "blob_sha",
        "path",
        "pull_request_id",
        "number",
        "url",
    }
)


def validate_repository_path(path: str) -> str:
    """Validate one relative POSIX repository path used in a write plan.

    Rejects absolute/drive paths, backslashes, NUL bytes, empty/`.`/`..`
    segments and writes inside ``.git``. The adapter validates again; a plan
    must never be the only check.
    """

    if not isinstance(path, str) or not path or len(path) > 4096:
        raise ValueError("A repository path must be a non-empty string under 4096 characters.")
    if path.startswith(("/", "\\")) or "\\" in path or "\x00" in path:
        raise ValueError("Repository paths must be relative POSIX paths without NUL bytes.")
    if re.match(r"^[A-Za-z]:", path):
        raise ValueError("Repository paths must not begin with a drive letter.")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("Repository paths must not contain empty, '.' or '..' segments.")
    if segments[0].lower() == ".git":
        raise ValueError("Writes inside .git are refused.")
    return path


def _normalize_changes(files: Any) -> tuple[dict[str, Any], ...]:
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for item in files or ():
        if isinstance(item, FileChange):
            path, content, expected = item.path, item.content, item.expected_sha
        elif isinstance(item, dict):
            path = item.get("path")
            content = item.get("content")
            expected = item.get("expected_sha")
        else:
            raise ValueError("Each file change must be a FileChange or a mapping.")
        path = validate_repository_path(path)
        if path in seen:
            raise ValueError("A commit plan must not contain duplicate paths.")
        if not isinstance(content, str):
            raise ValueError("File content must be text.")
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ValueError(f"'{path}' exceeds the {MAX_FILE_BYTES}-byte per-file limit.")
        total += size
        if total > MAX_COMMIT_BYTES:
            raise ValueError(f"The commit payload exceeds the {MAX_COMMIT_BYTES}-byte total limit.")
        if expected is not None:
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", expected):
                raise ValueError("expected_sha must be a hexadecimal blob SHA.")
            expected = expected.lower()
        seen.add(path)
        changes.append({"path": path, "content": content, "expected_sha": expected})
    if not changes:
        raise ValueError("A commit plan needs at least one file change.")
    if len(changes) > MAX_FILES_PER_COMMIT:
        raise ValueError(f"A commit plan may contain at most {MAX_FILES_PER_COMMIT} files.")
    return tuple(changes)


def _require_capability(adapter: ProviderAdapter, capability: Capability) -> None:
    state = adapter.capability_state(capability)
    if not state.is_available:
        raise CapabilityUnavailableError(
            f"{adapter.provider_id} cannot perform '{capability.value}': {state.value}.",
            provider=adapter.provider_id,
            capability=capability.value,
        )


def _execute_plan(
    plan: WritePlan,
    adapter: ProviderAdapter,
    *,
    approved: bool,
    action,
    actor_subject: str | None = None,
    audit_path_value: Path | None = None,
    approved_fingerprint: str | None = None,
) -> OperationResult:
    """Shared approval + audit wrapper for state-changing operations."""

    if plan.operation in FORBIDDEN_OPERATIONS:
        raise PermissionDeniedError(
            f"Operation '{plan.operation}' is never performed by OGX.",
            provider=plan.provider_id,
        )
    if not approved:
        raise ApprovalRequiredError(
            "Remote state changes require explicit user approval and a validated plan.",
            provider=plan.provider_id,
            capability=plan.operation,
        )
    fingerprint = plan.fingerprint()
    if approved_fingerprint is not None and approved_fingerprint != fingerprint:
        raise PermissionDeniedError(
            "Approval does not match this plan; a modified plan needs a new approval.",
            provider=plan.provider_id,
        )
    if plan.provider_id != adapter.provider_id:
        raise PermissionDeniedError("Plan/provider mismatch; refusing execution.", provider=plan.provider_id)

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "platform.write.requested",
        "operation": plan.operation,
        "provider_id": plan.provider_id,
        "repository": plan.repository,
        "actor_subject": actor_subject or os.environ.get("OGX_ACTOR_SUBJECT", ""),
        "approved": True,
        "plan_fingerprint": fingerprint,
    }
    files = plan.payload.get("files")
    if isinstance(files, (list, tuple)):
        entry["paths"] = [str(item.get("path", "")) for item in files if isinstance(item, dict)]
    elif plan.payload.get("path"):
        entry["paths"] = [str(plan.payload.get("path", ""))]
    append_audit(entry, path=audit_path_value)

    try:
        result = action()
    except Exception as exc:
        append_audit(
            {
                **entry,
                "event": "platform.write.failed",
                "error_kind": getattr(exc, "kind", type(exc).__name__),
            },
            path=audit_path_value,
        )
        raise

    safe_result = {
        key: value for key, value in dict(result).items() if key in _AUDIT_RESULT_KEYS
    }
    append_audit(
        {**entry, "event": "platform.write.completed", **safe_result},
        path=audit_path_value,
    )
    return OperationResult(
        operation=plan.operation,
        provider_id=plan.provider_id,
        ok=True,
        correlation_id=adapter.transport.last_correlation_id,
        result=dict(result),
    )


def plan_branch_create(
    adapter: ProviderAdapter,
    repository: str,
    *,
    branch: str,
    from_branch: str,
    expected_base_sha: str | None = None,
) -> WritePlan:
    """Validate branch creation without contacting the remote API."""

    _require_capability(adapter, Capability.BRANCH_CREATE)
    _validate_branch("new", branch)
    _validate_branch("base", from_branch)
    if branch == from_branch:
        raise ValueError("The new branch must differ from its base branch.")
    if branch in _PROTECTED:
        raise PermissionDeniedError(
            "Refusing to create a branch with a protected/default name.",
            provider=adapter.provider_id,
        )
    if expected_base_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{7,64}", expected_base_sha):
        raise ValueError("expected_base_sha must be a hexadecimal commit SHA.")
    return WritePlan(
        operation=Capability.BRANCH_CREATE.value,
        provider_id=adapter.provider_id,
        repository=repository,
        summary=f"Create branch '{branch}' from '{from_branch}'.",
        payload={
            "branch": branch,
            "from_branch": from_branch,
            "expected_base_sha": expected_base_sha.lower() if expected_base_sha else None,
        },
        required_permission="repo:write",
        destructive=False,
    )


def execute_branch_create(
    plan: WritePlan,
    adapter: ProviderAdapter,
    *,
    approved: bool,
    actor_subject: str | None = None,
    audit_path: Path | None = None,
    approved_fingerprint: str | None = None,
) -> OperationResult:
    def action() -> dict:
        # Creating from the approved base SHA (when present) is deterministic:
        # a moved base branch cannot silently change what was approved.
        created = adapter.create_branch(
            plan.repository,
            branch=plan.payload["branch"],
            from_branch=plan.payload["from_branch"],
            from_sha=plan.payload.get("expected_base_sha"),
        )
        return created.to_dict()

    return _execute_plan(
        plan,
        adapter,
        approved=approved,
        action=action,
        actor_subject=actor_subject,
        audit_path_value=audit_path,
        approved_fingerprint=approved_fingerprint,
    )


def plan_file_commit(
    adapter: ProviderAdapter,
    repository: str,
    *,
    branch: str,
    message: str,
    files: Any,
    expected_parent_sha: str | None = None,
) -> WritePlan:
    """Validate a bounded multi-file commit plan without contacting the API."""

    _require_capability(adapter, Capability.COMMIT_CREATE)
    _require_capability(adapter, Capability.REF_UPDATE)
    _validate_branch("target", branch)
    if branch in _PROTECTED:
        raise PermissionDeniedError(
            "Refusing to commit directly to a protected/default branch; use a feature branch.",
            provider=adapter.provider_id,
        )
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_COMMIT_MESSAGE:
        raise ValueError(f"Commit messages must be 1-{MAX_COMMIT_MESSAGE} characters.")
    changes = _normalize_changes(files)
    if expected_parent_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{7,64}", expected_parent_sha):
        raise ValueError("expected_parent_sha must be a hexadecimal commit SHA.")
    paths = [change["path"] for change in changes]
    listed = ", ".join(paths[:5]) + (" …" if len(paths) > 5 else "")
    return WritePlan(
        operation=Capability.COMMIT_CREATE.value,
        provider_id=adapter.provider_id,
        repository=repository,
        summary=f"Commit {len(paths)} file(s) to '{branch}': {listed}",
        payload={
            "branch": branch,
            "message": message.strip(),
            "files": [dict(change) for change in changes],
            "expected_parent_sha": (
                expected_parent_sha.lower() if expected_parent_sha else None
            ),
        },
        required_permission="repo:write",
        destructive=False,
    )


def execute_file_commit(
    plan: WritePlan,
    adapter: ProviderAdapter,
    *,
    approved: bool,
    actor_subject: str | None = None,
    audit_path: Path | None = None,
    approved_fingerprint: str | None = None,
    expected_old_sha: str | None = None,
) -> OperationResult:
    """Create the commit, then fast-forward the branch to it."""

    def action() -> dict:
        branch = plan.payload["branch"]
        changes = tuple(
            FileChange(
                path=item["path"],
                content=item["content"],
                expected_sha=item.get("expected_sha"),
            )
            for item in plan.payload["files"]
        )
        commit = adapter.create_commit(
            plan.repository,
            branch=branch,
            message=plan.payload["message"],
            files=changes,
            expected_parent=plan.payload.get("expected_parent_sha"),
        )
        ref = adapter.update_reference(
            plan.repository,
            branch=branch,
            sha=commit.commit_sha,
            expected_old_sha=expected_old_sha or commit.parent_sha,
        )
        return {**commit.to_dict(), "ref": ref["ref"], "sha": ref["sha"], "previous_sha": ref.get("previous_sha")}

    return _execute_plan(
        plan,
        adapter,
        approved=approved,
        action=action,
        actor_subject=actor_subject,
        audit_path_value=audit_path,
        approved_fingerprint=approved_fingerprint,
    )


def plan_ref_update(
    adapter: ProviderAdapter,
    repository: str,
    *,
    branch: str,
    sha: str,
    expected_old_sha: str | None = None,
) -> WritePlan:
    """Validate a fast-forward reference update (recovery for a failed push)."""

    _require_capability(adapter, Capability.REF_UPDATE)
    _validate_branch("target", branch)
    if branch in _PROTECTED:
        raise PermissionDeniedError(
            "Refusing to update a protected/default branch.", provider=adapter.provider_id
        )
    for label, value in (("target", sha), ("expected old", expected_old_sha)):
        if value is not None and not re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
            raise ValueError(f"Invalid {label} commit SHA.")
    return WritePlan(
        operation=Capability.REF_UPDATE.value,
        provider_id=adapter.provider_id,
        repository=repository,
        summary=f"Fast-forward '{branch}' to {sha[:12]}.",
        payload={
            "branch": branch,
            "sha": sha.lower(),
            "expected_old_sha": expected_old_sha.lower() if expected_old_sha else None,
        },
        required_permission="repo:write",
        destructive=False,
    )


def execute_ref_update(
    plan: WritePlan,
    adapter: ProviderAdapter,
    *,
    approved: bool,
    actor_subject: str | None = None,
    audit_path: Path | None = None,
    approved_fingerprint: str | None = None,
) -> OperationResult:
    def action() -> dict:
        return adapter.update_reference(
            plan.repository,
            branch=plan.payload["branch"],
            sha=plan.payload["sha"],
            expected_old_sha=plan.payload.get("expected_old_sha"),
        )

    return _execute_plan(
        plan,
        adapter,
        approved=approved,
        action=action,
        actor_subject=actor_subject,
        audit_path_value=audit_path,
        approved_fingerprint=approved_fingerprint,
    )


def plan_file_update(
    adapter: ProviderAdapter,
    repository: str,
    *,
    path: str,
    content: str,
    message: str,
    branch: str,
    expected_sha: str | None = None,
) -> WritePlan:
    """Validate a single-file create/update plan (Contents API path)."""

    _require_capability(adapter, Capability.FILE_WRITE)
    _validate_branch("target", branch)
    if branch in _PROTECTED:
        raise PermissionDeniedError(
            "Refusing to commit directly to a protected/default branch; use a feature branch.",
            provider=adapter.provider_id,
        )
    clean = validate_repository_path(path)
    if not isinstance(content, str):
        raise ValueError("File content must be text.")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"The file exceeds the {MAX_FILE_BYTES}-byte limit.")
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_COMMIT_MESSAGE:
        raise ValueError(f"Commit messages must be 1-{MAX_COMMIT_MESSAGE} characters.")
    if expected_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{7,64}", expected_sha):
        raise ValueError("expected_sha must be a hexadecimal blob SHA.")
    return WritePlan(
        operation=Capability.FILE_WRITE.value,
        provider_id=adapter.provider_id,
        repository=repository,
        summary=f"Write '{clean}' on '{branch}' ({len(content.encode('utf-8'))} bytes).",
        payload={
            "path": clean,
            "content": content,
            "message": message.strip(),
            "branch": branch,
            "expected_sha": expected_sha.lower() if expected_sha else None,
        },
        required_permission="repo:write",
        destructive=False,
    )


def execute_file_update(
    plan: WritePlan,
    adapter: ProviderAdapter,
    *,
    approved: bool,
    actor_subject: str | None = None,
    audit_path: Path | None = None,
    approved_fingerprint: str | None = None,
) -> OperationResult:
    def action() -> dict:
        return adapter.write_file(
            plan.repository,
            path=plan.payload["path"],
            content=plan.payload["content"],
            message=plan.payload["message"],
            branch=plan.payload["branch"],
            expected_sha=plan.payload.get("expected_sha"),
        )

    return _execute_plan(
        plan,
        adapter,
        approved=approved,
        action=action,
        actor_subject=actor_subject,
        audit_path_value=audit_path,
        approved_fingerprint=approved_fingerprint,
    )


def audit_path() -> Path:
    from ..config import state_dir

    return state_dir() / "platform" / "audit.jsonl"


def append_audit(event: dict[str, Any], *, path: Path | None = None) -> Path:
    """Append one redacted, hash-chained audit record."""

    target = Path(path) if path is not None else audit_path()
    append_chained(target, event)
    return target


def verify_audit(*, path: Path | None = None) -> dict[str, Any]:
    """Verify the audit chain; see ``platform.audit.verify_chain`` for limits."""

    target = Path(path) if path is not None else audit_path()
    return verify_chain(target)


def read_audit(*, limit: int = 100, path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else audit_path()
    if not target.exists() or target.is_symlink():
        return []
    events: list[dict[str, Any]] = []
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events[-max(1, min(limit, 1000)):]
