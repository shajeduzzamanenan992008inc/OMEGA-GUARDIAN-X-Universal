"""Controlled source patching with preconditions, backups and rollback.

The engine never edits a file unless *all* of the following hold:

* the target is inside the project root, with no symlinked path component;
* the file is a regular, UTF-8 text file below the size bound;
* the exact bytes to replace occur exactly once;
* the file's SHA-256 still matches the value recorded at planning time;
* the caller explicitly approved the apply/rollback operation.

Original bytes are copied to ``<state>/patches/<plan_id>/backup`` *outside* the
project before the first write. Writes are atomic (temporary file + replace) and
preserve the file mode. A rollback refuses to run when the file changed after
the patch, so it can never silently discard someone else's edit.

This module does not execute project code, resolve dependencies or contact the
network. It is deliberately narrow: no binary patches, no renames, no deletions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .config import state_dir

SCHEMA = "ogx-patch-plan-1"
MAX_CHANGES = 100
MAX_FILE_BYTES = 2_000_000
MAX_NEW_BYTES = 2_000_000
_DRIVE = re.compile(r"^[A-Za-z]:")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PatchError(RuntimeError):
    """Base patch failure with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str = "patch_error") -> None:
        super().__init__(message)
        self.code = code


class PatchRefused(PatchError):
    """Planning or applying was refused by a safety precondition."""

    def __init__(self, message: str, *, code: str = "refused", details: Iterable[str] = ()) -> None:
        super().__init__(message, code=code)
        self.details = tuple(details)


class PatchIntegrityError(PatchError):
    """The file changed after planning (or after applying, for rollback)."""

    def __init__(self, message: str, *, code: str = "integrity_mismatch", details: Iterable[str] = ()) -> None:
        super().__init__(message, code=code)
        self.details = tuple(details)


class PatchApprovalRequired(PatchError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="approval_required")


@dataclass(frozen=True, slots=True)
class ChangeSpec:
    """One exact-text replacement request."""

    path: str
    old: str
    new: str
    expected_sha256: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _state_home(state_home: Path | str | None) -> Path:
    home = Path(state_home) if state_home is not None else state_dir()
    home = home.expanduser().absolute()
    if home.is_symlink():
        raise PatchError("The patch state directory must not be a symlink.", code="state_symlink")
    return home


def _patches_dir(state_home: Path) -> Path:
    return state_home / "patches"


def _plan_path(plan_id: str, state_home: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", str(plan_id)):
        raise PatchError("Invalid patch plan id.", code="invalid_plan_id")
    return _patches_dir(state_home) / f"{plan_id}.json"


def _safe_relative(root: Path, value: str) -> Path:
    raw = str(value or "").strip()
    if not raw or "\x00" in raw:
        raise PatchRefused("A change path is empty or invalid.", code="path_rejected")
    if raw.startswith(("-", "/", "\\")) or _DRIVE.match(raw):
        raise PatchRefused(
            f"Change paths must be relative to the project root: {raw!r}", code="path_rejected"
        )
    parts = [part for part in raw.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise PatchRefused(f"Change path traversal is not accepted: {raw!r}", code="path_traversal")
    candidate = root.joinpath(*parts)
    # Check the *unresolved* path first: resolving a symlink would erase the
    # symlink component and make the escape invisible (found by Linux CI).
    current = root
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                raise PatchRefused(
                    f"Symlinked change paths are not accepted: {raw!r}", code="symlink_rejected"
                )
        except OSError as exc:  # pragma: no cover - platform dependent
            raise PatchRefused(f"Cannot inspect change target: {raw!r}", code="path_rejected") from exc
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PatchRefused(f"Change target does not exist: {raw!r}", code="missing_file") from exc
    if not resolved.is_relative_to(root):
        raise PatchRefused(f"Change target escapes the project root: {raw!r}", code="path_escape")
    if not resolved.is_file():
        raise PatchRefused(f"Change target is not a regular file: {raw!r}", code="not_a_file")
    return resolved


def _read_change_file(path: Path) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PatchRefused(f"Cannot stat change target: {path.name}", code="unreadable") from exc
    if size > MAX_FILE_BYTES:
        raise PatchRefused(
            f"Change target exceeds {MAX_FILE_BYTES} bytes: {path.name}", code="file_too_large"
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PatchRefused(f"Cannot read change target: {path.name}", code="unreadable") from exc
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchRefused(
            f"Only UTF-8 text files are supported; {path.name} is not valid UTF-8.",
            code="binary_rejected",
        ) from exc
    return data


def _coerce_changes(changes: Iterable[ChangeSpec | Mapping[str, Any]]) -> list[ChangeSpec]:
    specs: list[ChangeSpec] = []
    for item in changes:
        if isinstance(item, ChangeSpec):
            specs.append(item)
            continue
        if not isinstance(item, Mapping):
            raise PatchRefused("Each change must be an object with path/old/new.", code="invalid_change")
        unknown = set(item) - {"path", "old", "new", "expected_sha256"}
        if unknown:
            raise PatchRefused(
                f"Unsupported change field(s): {', '.join(sorted(unknown))}", code="invalid_change"
            )
        specs.append(
            ChangeSpec(
                path=str(item.get("path", "")),
                old=item.get("old"),
                new=item.get("new"),
                expected_sha256=item.get("expected_sha256"),
            )
        )
    return specs


def plan_patch(
    root: Path | str,
    changes: Iterable[ChangeSpec | Mapping[str, Any]],
    *,
    project_id: str | None = None,
    actor_subject: str | None = None,
    state_home: Path | str | None = None,
) -> dict[str, Any]:
    """Validate a change set and persist an immutable patch plan.

    Nothing in the project is written. The plan stores the exact old/new bytes,
    the pre-change hash and the post-change hash for every file.
    """

    project_root = Path(root).expanduser()
    try:
        project_root = project_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PatchRefused(f"Project root cannot be resolved: {root}", code="root_invalid") from exc
    if not project_root.is_dir():
        raise PatchRefused(f"Project root is not a directory: {root}", code="root_invalid")

    home = _state_home(state_home)
    if home.is_relative_to(project_root):
        raise PatchRefused(
            "Patch state (plans and backups) must live outside the project root.",
            code="state_inside_project",
        )
    if home.is_dir() and not os.access(home, os.W_OK):
        raise PatchRefused(f"Patch state directory is not writable: {home}", code="state_unwritable")

    specs = _coerce_changes(changes)
    if not specs:
        raise PatchRefused("A patch plan needs at least one change.", code="empty_plan")
    if len(specs) > MAX_CHANGES:
        raise PatchRefused(f"A patch plan is limited to {MAX_CHANGES} changes.", code="too_many_changes")

    seen: set[Path] = set()
    recorded: list[dict[str, Any]] = []
    for spec in specs:
        target = _safe_relative(project_root, spec.path)
        if target in seen:
            raise PatchRefused(
                f"A change set may touch a file only once: {spec.path}", code="duplicate_path"
            )
        seen.add(target)
        if not isinstance(spec.old, str) or not isinstance(spec.new, str):
            raise PatchRefused("Change 'old' and 'new' must be strings.", code="invalid_change")
        if not spec.old:
            raise PatchRefused("Change 'old' must not be empty.", code="invalid_change")
        if spec.old == spec.new:
            raise PatchRefused("Change 'old' and 'new' are identical.", code="no_op")
        before = _read_change_file(target)
        old_bytes = spec.old.encode("utf-8")
        occurrences = before.count(old_bytes)
        if occurrences != 1:
            raise PatchRefused(
                f"Expected exactly one occurrence in {spec.path}, found {occurrences}.",
                code="ambiguous_match",
            )
        before_hash = _sha256(before)
        if spec.expected_sha256 is not None:
            expected = str(spec.expected_sha256).strip().lower()
            if not _HEX64.fullmatch(expected):
                raise PatchRefused("expected_sha256 must be 64 lowercase hex characters.", code="invalid_hash")
            if expected != before_hash:
                raise PatchIntegrityError(
                    f"{spec.path} does not match the expected hash.", code="precondition_failed"
                )
        after = before.replace(old_bytes, spec.new.encode("utf-8"), 1)
        if len(after) > MAX_NEW_BYTES:
            raise PatchRefused(f"Patched file would exceed {MAX_NEW_BYTES} bytes.", code="file_too_large")
        mode = stat.S_IMODE(target.stat().st_mode)
        recorded.append(
            {
                "path": target.relative_to(project_root).as_posix(),
                "old": spec.old,
                "new": spec.new,
                "before_sha256": before_hash,
                "after_sha256": _sha256(after),
                "size_before": len(before),
                "size_after": len(after),
                "mode": mode,
            }
        )

    plan = {
        "schema": SCHEMA,
        "plan_id": uuid4().hex,
        "state": "planned",
        "created_at": _now(),
        "project_id": project_id,
        "project_root": str(project_root),
        "actor_subject": actor_subject or os.environ.get("OGX_ACTOR_SUBJECT", ""),
        "changes": recorded,
        "summary": {
            "files": len(recorded),
            "bytes_added": sum(c["size_after"] - c["size_before"] for c in recorded),
            "paths": [c["path"] for c in recorded],
        },
    }
    patches = _patches_dir(home)
    patches.mkdir(parents=True, exist_ok=True)
    _write_private_json(_plan_path(plan["plan_id"], home), plan)
    _audit(
        {
            "event": "patch.plan.created",
            "plan_id": plan["plan_id"],
            "project_id": project_id,
            "files": plan["summary"]["files"],
            "paths": plan["summary"]["paths"],
            "actor_subject": plan["actor_subject"],
            "correlation_id": plan["plan_id"],
        }
    )
    return plan


def load_plan(plan_id: str, *, state_home: Path | str | None = None) -> dict[str, Any]:
    home = _state_home(state_home)
    path = _plan_path(plan_id, home)
    if path.is_symlink() or not path.is_file():
        raise PatchError(f"Patch plan not found: {plan_id}", code="plan_not_found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PatchError(f"Patch plan is unreadable: {plan_id}", code="plan_corrupt") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise PatchError(f"Patch plan has an unsupported schema: {plan_id}", code="plan_corrupt")
    return data


def list_plans(*, state_home: Path | str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    home = _state_home(state_home)
    directory = _patches_dir(home)
    if not directory.is_dir():
        return []
    plans: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            plans.append(
                {
                    "plan_id": data.get("plan_id"),
                    "state": data.get("state"),
                    "created_at": data.get("created_at"),
                    "project_root": data.get("project_root"),
                    "project_id": data.get("project_id"),
                    "files": (data.get("summary") or {}).get("files"),
                    "paths": (data.get("summary") or {}).get("paths", []),
                }
            )
        if len(plans) >= max(1, min(limit, 500)):
            break
    return plans


def _verify_plan_root(plan: Mapping[str, Any]) -> Path:
    value = str(plan.get("project_root") or "")
    root = Path(value).expanduser()
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PatchRefused("The recorded project root no longer exists.", code="root_missing") from exc
    if not root.is_dir():
        raise PatchRefused("The recorded project root is not a directory.", code="root_invalid")
    return root


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    descriptor = None
    temp_name = None
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=".ogx-patch-", dir=str(path.parent))
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def apply_patch(
    plan_id: str,
    *,
    approved: bool,
    actor_subject: str | None = None,
    state_home: Path | str | None = None,
) -> dict[str, Any]:
    """Apply a stored plan after re-verifying every precondition."""

    if not approved:
        raise PatchApprovalRequired("Applying a patch requires explicit approval (--confirm).")
    home = _state_home(state_home)
    plan = load_plan(plan_id, state_home=home)
    if plan.get("state") != "planned":
        raise PatchRefused(
            f"Patch plan is in state '{plan.get('state')}'; only a planned patch can be applied.",
            code="invalid_plan_state",
        )
    root = _verify_plan_root(plan)
    changes = plan.get("changes") or []
    if not changes:
        raise PatchError("Patch plan contains no changes.", code="plan_corrupt")

    prepared: list[tuple[Path, bytes, bytes, dict[str, Any]]] = []
    mismatches: list[str] = []
    for change in changes:
        target = _safe_relative(root, str(change.get("path", "")))
        current = _read_change_file(target)
        current_hash = _sha256(current)
        if current_hash != change.get("before_sha256"):
            mismatches.append(str(change.get("path")))
            continue
        old_bytes = str(change.get("old", "")).encode("utf-8")
        if current.count(old_bytes) != 1:
            mismatches.append(str(change.get("path")))
            continue
        after = current.replace(old_bytes, str(change.get("new", "")).encode("utf-8"), 1)
        if _sha256(after) != change.get("after_sha256"):
            mismatches.append(str(change.get("path")))
            continue
        prepared.append((target, current, after, dict(change)))
    if mismatches:
        raise PatchIntegrityError(
            "Files changed after the patch plan was created; re-plan before applying.",
            code="stale_plan",
            details=mismatches,
        )

    backup_root = _patches_dir(home) / plan_id / "backup"
    if backup_root.exists():
        raise PatchRefused("A backup for this plan already exists; refusing to overwrite it.", code="backup_exists")
    backup_root.mkdir(parents=True, exist_ok=False)
    manifest: list[dict[str, Any]] = []
    for index, (target, before, _after, change) in enumerate(prepared):
        backup = backup_root / f"{index:03d}.bin"
        backup.write_bytes(before)
        try:
            os.chmod(backup, 0o600)
        except OSError:
            pass
        manifest.append(
            {
                "path": change["path"],
                "backup": backup.name,
                "before_sha256": change["before_sha256"],
                "after_sha256": change["after_sha256"],
                "mode": int(change.get("mode", 0o644)),
            }
        )
    _write_private_json(backup_root.parent / "backup.json", {"plan_id": plan_id, "files": manifest})

    actor = actor_subject or os.environ.get("OGX_ACTOR_SUBJECT", "")
    _audit(
        {
            "event": "patch.apply.requested",
            "plan_id": plan_id,
            "project_id": plan.get("project_id"),
            "approved": True,
            "actor_subject": actor,
            "correlation_id": plan_id,
        }
    )

    applied: list[str] = []
    try:
        for target, _before, after, change in prepared:
            _atomic_write(target, after, int(change.get("mode", 0o644)))
            applied.append(str(change["path"]))
    except Exception as exc:
        plan["state"] = "apply_failed"
        plan["failure"] = {"path": applied[-1] if applied else None, "error": type(exc).__name__}
        plan["updated_at"] = _now()
        _write_private_json(_plan_path(plan_id, home), plan)
        _audit(
            {
                "event": "patch.apply.failed",
                "plan_id": plan_id,
                "applied": applied,
                "error_kind": type(exc).__name__,
                "correlation_id": plan_id,
            }
        )
        raise PatchError(
            "Patch application failed part-way; use 'ogx patch rollback' to restore the backup.",
            code="apply_failed",
        ) from exc

    plan["state"] = "applied"
    plan["applied_at"] = _now()
    plan["applied_by"] = actor
    plan["updated_at"] = plan["applied_at"]
    _write_private_json(_plan_path(plan_id, home), plan)
    _audit(
        {
            "event": "patch.apply.completed",
            "plan_id": plan_id,
            "project_id": plan.get("project_id"),
            "paths": applied,
            "actor_subject": actor,
            "correlation_id": plan_id,
        }
    )
    return {
        "plan_id": plan_id,
        "state": "applied",
        "applied": applied,
        "backup_dir": str(backup_root),
        "actor_subject": actor,
    }


def rollback_patch(
    plan_id: str,
    *,
    approved: bool,
    actor_subject: str | None = None,
    state_home: Path | str | None = None,
) -> dict[str, Any]:
    """Restore the recorded originals when nothing changed after the patch."""

    if not approved:
        raise PatchApprovalRequired("Rolling back a patch requires explicit approval (--confirm).")
    home = _state_home(state_home)
    plan = load_plan(plan_id, state_home=home)
    if plan.get("state") not in {"applied", "apply_failed"}:
        raise PatchRefused(
            f"Patch plan is in state '{plan.get('state')}'; nothing to roll back.",
            code="invalid_plan_state",
        )
    root = _verify_plan_root(plan)
    backup_dir = _patches_dir(home) / plan_id / "backup"
    manifest_path = backup_dir.parent / "backup.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PatchError("Patch backup manifest is missing.", code="backup_missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files") or []

    restored: list[str] = []
    drift: list[str] = []
    for entry in entries:
        target = _safe_relative(root, str(entry.get("path", "")))
        backup = backup_dir / str(entry.get("backup", ""))
        if backup.is_symlink() or not backup.is_file():
            raise PatchError(f"Backup file is missing: {backup.name}", code="backup_missing")
        current = _read_change_file(target)
        expected_after = str(entry.get("after_sha256", ""))
        if _sha256(current) != expected_after:
            # A partially applied patch leaves the original, which is safe to keep.
            if _sha256(current) == str(entry.get("before_sha256", "")):
                continue
            drift.append(str(entry.get("path")))
            continue
        original = backup.read_bytes()
        if _sha256(original) != str(entry.get("before_sha256", "")):
            raise PatchIntegrityError(
                f"Backup integrity check failed: {entry.get('path')}", code="backup_corrupt"
            )
        restored.append(str(entry.get("path")))
        _atomic_write(target, original, int(entry.get("mode", 0o644)))
    if drift:
        raise PatchIntegrityError(
            "Files changed after the patch was applied; refusing to overwrite them.",
            code="rollback_drift",
            details=drift,
        )

    actor = actor_subject or os.environ.get("OGX_ACTOR_SUBJECT", "")
    plan["state"] = "rolled_back"
    plan["rolled_back_at"] = _now()
    plan["rolled_back_by"] = actor
    plan["updated_at"] = plan["rolled_back_at"]
    _write_private_json(_plan_path(plan_id, home), plan)
    _audit(
        {
            "event": "patch.rollback.completed",
            "plan_id": plan_id,
            "project_id": plan.get("project_id"),
            "paths": restored,
            "actor_subject": actor,
            "correlation_id": plan_id,
        }
    )
    return {"plan_id": plan_id, "state": "rolled_back", "restored": restored, "actor_subject": actor}


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists():
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(text, encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
    else:
        path.write_text(text, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _audit(event: Mapping[str, Any]) -> None:
    from .platform.operations import append_audit

    append_audit(dict(event))


def remove_plan_files(plan_id: str, *, state_home: Path | str | None = None) -> None:
    """Delete a plan and its backups. Not exposed on the CLI; used by tests."""

    home = _state_home(state_home)
    plan = _plan_path(plan_id, home)
    if plan.exists():
        plan.unlink()
    shutil.rmtree(_patches_dir(home) / plan_id, ignore_errors=True)
