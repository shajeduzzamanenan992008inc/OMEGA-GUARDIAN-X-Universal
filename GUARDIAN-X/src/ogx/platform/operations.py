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
from .models import OperationResult, WritePlan
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
    if ".." in name or "//" in name or name.endswith(("/", ".")) or "@{" in name:
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
