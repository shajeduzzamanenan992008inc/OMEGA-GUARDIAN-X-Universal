"""Shared CLI helpers: project resolution, external report directories, approval queueing."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ogx.governance.approvals import ApprovalManager
from ogx.kernel.config import state_dir


BANNER = "OMEGA GUARDIAN X — Universal Repository Intelligence"

GLOBAL_CONFIG = """# OMEGA GUARDIAN X global configuration
# Stored outside scanned repositories.
[scan]
exclude = [".git", "node_modules", ".venv", "venv", "dist", "build", "vendor"]
max_files = 12000
max_file_bytes = 2000000
"""


def _root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _project_registry_path() -> Path:
    return state_dir() / "projects.json"


def _load_projects() -> dict[str, str]:
    path = _project_registry_path()
    if path.is_symlink():
        raise ValueError("OGX project registry must not be a symlink.")
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError("Invalid OGX project registry.")
    return data


def _save_projects(projects: dict[str, str]) -> None:
    path = _project_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(projects, indent=2), encoding="utf-8")
    temp.replace(path)


def _resolve_project(args: argparse.Namespace) -> Path:
    if getattr(args, "project", None):
        projects = _load_projects()
        if args.project not in projects:
            raise ValueError(f"Unknown registered project: {args.project}. Run: ogx projects list")
        return _root(projects[args.project])
    if getattr(args, "path", None):
        return _root(args.path)
    raise ValueError("Provide a repository path or --project NAME.")


def _external_report_dir(root: Path) -> Path:
    canonical = str(root.resolve()).casefold()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", root.name).strip(".-") or "project"
    return state_dir() / "projects" / f"{safe_name}-{digest}" / "reports"


def _prepare_report_dir(root: Path) -> Path:
    """Create OGX external report directories while refusing symlinked components."""
    target = _external_report_dir(root)
    base = state_dir()
    if base.resolve(strict=False).is_relative_to(root.resolve()):
        raise ValueError("OGX_HOME must resolve outside the target project so reports remain external.")
    directories = [base, base / "projects", target.parent, target]
    for directory in directories:
        if directory.is_symlink():
            raise OSError(f"OGX report directory must not be a symlink: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OSError(f"OGX report directory became a symlink: {directory}")
    return target


def _engine_ids(values: list[str] | None) -> list[str] | None:
    """Accept repeated values and comma-separated engine IDs."""
    if not values:
        return None
    selected: list[str] = []
    for value in values:
        selected.extend(part.strip() for part in value.split(",") if part.strip())
    return selected or None


def _write_report_bundle(root: Path, prefix: str, report: dict, report_format: str,
                         renderer) -> list[Path]:
    """Write reports to OGX external state only, refusing symlinked destinations."""
    report_dir = _prepare_report_dir(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = f"{prefix}-{stamp}"
    outputs: list[Path] = []
    if report_format in ("json", "both"):
        destination = report_dir / f"{base}.json"
        if destination.is_symlink():
            raise OSError(f"External report must not be a symlink: {destination}")
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        outputs.append(destination)
    if report_format in ("markdown", "both"):
        destination = report_dir / f"{base}.md"
        if destination.is_symlink():
            raise OSError(f"External report must not be a symlink: {destination}")
        destination.write_text(renderer(report), encoding="utf-8")
        outputs.append(destination)
    return outputs


def _requires_email_approval() -> bool:
    """Mutating commands are email-gated by default; explicit local break-glass is opt-in."""
    return os.environ.get("OGX_ALLOW_UNAPPROVED_WRITES", "0").strip().lower() not in {"1", "true", "yes"}


def _queue_action_approval(operation: str, payload: dict[str, str], summary: str,
                           requester_subject: str | None = None) -> dict[str, object]:
    actor = (requester_subject or os.environ.get("OGX_ACTOR_SUBJECT", "")).strip()
    if not actor:
        raise ValueError("Set OGX_ACTOR_SUBJECT to the requesting user's external identity before requesting a write.")
    return ApprovalManager().create(operation=operation, payload=payload,
                                    requester_subject=actor, summary=summary)


def _queue_email_approval(operation: str, root: Path, summary: str, remote: str | None = None,
                          requester_subject: str | None = None,
                          groups: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, str] = {"path": str(root.resolve())}
    if groups:
        payload["groups"] = ",".join(groups)
    if remote is not None:
        payload["remote"] = remote
    return _queue_action_approval(operation, payload, summary, requester_subject)


def _print_approval_queued(result: dict[str, object]) -> None:
    print("Email approval request sent; no project changes have been made.")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("After approval, the original requester runs: ogx governance execute " + str(result["request_id"]))


def _json_print(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))
