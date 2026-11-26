"""Crash-safe project relocation: plan, journal, apply, verify, recover.

Guarantees:

* The project identity file is the only identity evidence. A relocation never
  changes ``project_id`` or ``identity_hash``.
* The source is retained by default. Deleting the source requires an explicit
  ``confirm_delete_source=True`` and a ``move`` operation.
* Every state-changing step is journaled before it runs, so an interrupted
  relocation can be resumed instead of guessed at.
* Symbolic links and special files are never followed or copied; they are
  reported as warnings.
* The destination must not already exist, so two unrelated trees can never be
  merged silently.

This module is standard-library only and platform independent. Real
cross-drive/USB verification still requires actual media; tests exercise the
same code paths on temporary directories.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import NAMESPACE_OID, uuid5

from .pap import read_identity
from .pap.exceptions import IdentityError

RELOCATION_OPERATIONS = ("move", "copy", "clone")
JOURNAL_SCHEMA_VERSION = "1.0"
MAX_FILES = 200_000
_JOURNAL_NAMESPACE = uuid5(NAMESPACE_OID, "omega-guardian-x/relocation/v1")

STEP_ORDER = ("preflight", "copy", "verify", "rebind_workspace", "delete_source", "finalize")

FaultInjector = Callable[[str, str], None]


class RelocationError(Exception):
    """Base error for relocation failures."""

    def __init__(self, message: str, *, reason: str = "error", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = details or {}


class RelocationRefused(RelocationError):
    """A safety check refused the relocation before any write."""


class SimulatedInterruption(Exception):
    """Raised by a fault injector to simulate a crash.

    Unlike an ordinary failure, an interruption must not roll back: the journal
    is left exactly as a crashed process would leave it so that
    :func:`recover_relocation` can prove resumability.
    """


@dataclass(frozen=True, slots=True)
class RelocationPlan:
    plan_id: str
    operation: str
    source: str
    destination: str
    project_id: str
    project_name: str
    identity_hash: str
    file_count: int
    total_bytes: int
    warnings: tuple[str, ...] = ()
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "plan_id": self.plan_id,
            "operation": self.operation,
            "source": self.source,
            "destination": self.destination,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "identity_hash": self.identity_hash,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "warnings": list(self.warnings),
            "created_at": self.created_at,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelocationPlan":
        return cls(
            plan_id=str(data["plan_id"]),
            operation=str(data["operation"]),
            source=str(data["source"]),
            destination=str(data["destination"]),
            project_id=str(data["project_id"]),
            project_name=str(data.get("project_name", "")),
            identity_hash=str(data.get("identity_hash", "")),
            file_count=int(data.get("file_count", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            warnings=tuple(data.get("warnings") or ()),
            created_at=str(data.get("created_at", "")),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_tree(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Enumerate regular files without following links.

    Returns ``(entries, warnings)`` where each entry is
    ``{"path": relative_posix, "size": int, "sha256": str}``.
    """

    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    root = Path(root)
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirnames):
            candidate = current_path / name
            if candidate.is_symlink():
                warnings.append(f"skipped symlinked directory: {candidate.relative_to(root).as_posix()}")
                dirnames.remove(name)
        for name in filenames:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                warnings.append(f"skipped symlink: {relative}")
                continue
            try:
                stat_result = candidate.stat()
            except OSError as exc:
                warnings.append(f"skipped unreadable file: {relative} ({exc})")
                continue
            if not candidate.is_file():
                warnings.append(f"skipped non-regular file: {relative}")
                continue
            entries.append(
                {"path": relative, "size": stat_result.st_size, "sha256": _digest_file(candidate)}
            )
            if len(entries) > MAX_FILES:
                raise RelocationRefused(
                    f"Project has more than {MAX_FILES} files; relocation is bounded.",
                    reason="too_many_files",
                )
    entries.sort(key=lambda item: item["path"])
    return entries, warnings


def copy_project_tree(
    source: Path,
    destination: Path,
    *,
    entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Copy a project tree safely and verify every file.

    Idempotent: files already present at the destination with the same size and
    digest are skipped, which makes resuming after an interruption safe.
    """

    source = Path(source)
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise RelocationRefused(
            f"Destination is not empty: {destination}", reason="destination_exists"
        )
    destination.mkdir(parents=True, exist_ok=True)

    if entries is None:
        entries, warnings = scan_tree(source)
    else:
        warnings = []

    copied = 0
    reused = 0
    for entry in entries:
        relative = entry["path"]
        target = destination / Path(relative)
        if target.parent.is_symlink():
            raise RelocationRefused(
                f"Refusing to write through a symlinked directory: {target.parent}",
                reason="symlink_escape",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_file() and not target.is_symlink():
            if target.stat().st_size == entry["size"] and _digest_file(target) == entry["sha256"]:
                reused += 1
                continue
        shutil.copy2(source / Path(relative), target)
        if _digest_file(target) != entry["sha256"]:
            raise RelocationError(
                f"Copy verification failed for {relative}", reason="hash_mismatch"
            )
        copied += 1

    return {
        "files": len(entries),
        "copied": copied,
        "reused": reused,
        "bytes": sum(entry["size"] for entry in entries),
        "warnings": warnings,
    }


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def plan_relocation(
    source: Path | str,
    destination: Path | str,
    *,
    operation: str = "move",
) -> RelocationPlan:
    """Validate a relocation and return a reviewable plan. Writes nothing."""

    if operation not in RELOCATION_OPERATIONS:
        raise RelocationRefused(
            f"Unknown relocation operation: {operation}", reason="invalid_operation"
        )

    source_path = Path(source).expanduser()
    destination_path = Path(destination).expanduser()

    if not source_path.exists():
        raise RelocationRefused(f"Source does not exist: {source_path}", reason="source_missing")
    if source_path.is_symlink():
        raise RelocationRefused(
            f"Source must not be a symlink: {source_path}", reason="source_symlink"
        )
    if not source_path.is_dir():
        raise RelocationRefused(f"Source is not a directory: {source_path}", reason="source_not_directory")

    source_resolved = source_path.resolve()
    destination_resolved = destination_path.resolve()

    if destination_path.exists():
        raise RelocationRefused(
            f"Destination already exists: {destination_path}", reason="destination_exists"
        )
    if destination_resolved == source_resolved:
        raise RelocationRefused("Source and destination are the same path.", reason="same_path")
    if _is_within(destination_resolved, source_resolved):
        raise RelocationRefused(
            "Destination is inside the source tree.", reason="destination_inside_source"
        )
    if _is_within(source_resolved, destination_resolved):
        raise RelocationRefused(
            "Source is inside the destination tree.", reason="source_inside_destination"
        )

    try:
        identity = read_identity(source_resolved)
    except IdentityError as exc:
        raise RelocationRefused(
            f"Project identity is missing or invalid at {source_resolved}: {exc}",
            reason="identity_invalid",
        ) from exc

    entries, warnings = scan_tree(source_resolved)

    # Read-only media detection is advisory: on Windows os.access(W_OK) can be
    # optimistic, so a move reports the risk instead of claiming certainty.
    if operation == "move" and not os.access(source_resolved, os.W_OK):
        warnings.append(
            "source appears read-only; deleting the source after the move will fail"
        )

    parent = destination_resolved.parent
    probe = parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        raise RelocationRefused(
            f"Destination parent is not writable: {probe}", reason="destination_not_writable"
        )

    plan_id = str(
        uuid5(
            _JOURNAL_NAMESPACE,
            f"{operation}|{source_resolved}|{destination_resolved}|"
            f"{identity.project_id}|{identity.identity_hash}",
        )
    )

    return RelocationPlan(
        plan_id=plan_id,
        operation=operation,
        source=str(source_resolved),
        destination=str(destination_resolved),
        project_id=identity.project_id,
        project_name=identity.project_name,
        identity_hash=identity.identity_hash,
        file_count=len(entries),
        total_bytes=sum(entry["size"] for entry in entries),
        warnings=tuple(warnings),
        created_at=_now(),
    )


def _journal_dir() -> Path:
    from .config import state_dir

    return state_dir() / "relocation"


def _journal_path(plan: RelocationPlan, journal_dir: Path | str | None) -> Path:
    directory = Path(journal_dir) if journal_dir is not None else _journal_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{plan.plan_id}.json"


def _write_journal(path: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = _now()
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".journal-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(journal, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        tmp_path = Path(handle.name)
    try:
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _mark(
    journal: dict[str, Any],
    journal_path: Path,
    step: str,
    status: str,
    fault_inject: FaultInjector | None,
) -> None:
    journal["steps"][step] = {"status": status, "at": _now()}
    _write_journal(journal_path, journal)
    if fault_inject is not None:
        # Raising here simulates a crash after the step's state was persisted.
        fault_inject(step, status)


def _step_status(journal: dict[str, Any], step: str) -> str | None:
    entry = journal.get("steps", {}).get(step)
    return entry.get("status") if entry else None


def _new_journal(plan: RelocationPlan, *, confirm_delete_source: bool) -> dict[str, Any]:
    return {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "plan": plan.to_dict(),
        "steps": {},
        "confirm_delete_source": confirm_delete_source,
        "destination_created": False,
        "rollback": None,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _rebind_workspace(plan: RelocationPlan, registry: Any) -> dict[str, Any]:
    if registry is None:
        return {"status": "skipped", "reason": "no_registry"}
    workspace = registry.find_workspace_by_normalized_location(plan.source)
    if workspace is None:
        return {"status": "skipped", "reason": "workspace_not_registered"}
    updated = registry.update_workspace_location(
        workspace.workspace_id,
        location_reference=plan.destination,
        normalized_location=plan.destination,
    )
    return {"status": "done", "workspace_id": str(updated.workspace_id)}


def _execute(
    plan: RelocationPlan,
    journal: dict[str, Any],
    journal_path: Path,
    *,
    registry: Any,
    confirm_delete_source: bool,
    fault_inject: FaultInjector | None,
    resume: bool,
) -> dict[str, Any]:
    source = Path(plan.source)
    destination = Path(plan.destination)

    if _step_status(journal, "finalize") == "done":
        return {"state": "complete", "resumed": resume, "plan": plan.to_dict()}

    try:
        if _step_status(journal, "preflight") != "done":
            _mark(journal, journal_path, "preflight", "started", fault_inject)
            if not source.exists():
                raise RelocationError(
                    f"Source disappeared: {source}", reason="source_missing"
                )
            identity = read_identity(source)
            if identity.project_id != plan.project_id or identity.identity_hash != plan.identity_hash:
                raise RelocationError(
                    "Project identity changed since the plan was created.",
                    reason="identity_changed",
                )
            if not resume and destination.exists():
                raise RelocationRefused(
                    f"Destination appeared after planning: {destination}",
                    reason="destination_exists",
                )
            _mark(journal, journal_path, "preflight", "done", fault_inject)

        entries, warnings = scan_tree(source)

        if _step_status(journal, "copy") != "done":
            _mark(journal, journal_path, "copy", "started", fault_inject)
            journal["destination_created"] = True
            _write_journal(journal_path, journal)
            report = copy_project_tree(source, destination, entries=entries)
            journal["copy_report"] = report
            _mark(journal, journal_path, "copy", "done", fault_inject)

        if _step_status(journal, "verify") != "done":
            _mark(journal, journal_path, "verify", "started", fault_inject)
            for entry in entries:
                target = destination / Path(entry["path"])
                if not target.is_file() or target.is_symlink():
                    raise RelocationError(
                        f"Missing or unsafe file after copy: {entry['path']}",
                        reason="verify_failed",
                    )
                if _digest_file(target) != entry["sha256"]:
                    raise RelocationError(
                        f"Digest mismatch after copy: {entry['path']}",
                        reason="verify_failed",
                    )
            destination_identity = read_identity(destination)
            if (
                destination_identity.project_id != plan.project_id
                or destination_identity.identity_hash != plan.identity_hash
            ):
                raise RelocationError(
                    "Destination identity does not match the source identity.",
                    reason="identity_mismatch",
                )
            _mark(journal, journal_path, "verify", "done", fault_inject)

        if _step_status(journal, "rebind_workspace") != "done":
            _mark(journal, journal_path, "rebind_workspace", "started", fault_inject)
            journal["rebind"] = _rebind_workspace(plan, registry)
            _mark(journal, journal_path, "rebind_workspace", "done", fault_inject)

        if _step_status(journal, "delete_source") != "done":
            _mark(journal, journal_path, "delete_source", "started", fault_inject)
            if plan.operation != "move":
                journal["source_deleted"] = False
                journal["delete_reason"] = "source_retained_for_copy_or_clone"
            elif not confirm_delete_source:
                journal["source_deleted"] = False
                journal["delete_reason"] = "explicit_confirmation_required"
            else:
                if len(source.parts) <= 1:
                    raise RelocationRefused(
                        f"Refusing to delete a drive/root path: {source}",
                        reason="unsafe_source_root",
                    )
                identity = read_identity(source)
                if identity.project_id != plan.project_id:
                    raise RelocationError(
                        "Source identity changed before deletion.", reason="identity_changed"
                    )
                shutil.rmtree(source)
                journal["source_deleted"] = True
                journal["delete_reason"] = "confirmed_move"
            _mark(journal, journal_path, "delete_source", "done", fault_inject)

        if _step_status(journal, "finalize") != "done":
            _mark(journal, journal_path, "finalize", "started", fault_inject)
            _mark(journal, journal_path, "finalize", "done", fault_inject)

    except Exception as exc:
        journal["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "reason": getattr(exc, "reason", "error"),
            "at": _now(),
        }
        if isinstance(exc, SimulatedInterruption):
            # Model a crash: persist and stop, never clean up.
            _write_journal(journal_path, journal)
            raise
        # Roll back only when this run created the destination, nothing was
        # deleted, and we are not resuming a previously journaled operation.
        if (
            not resume
            and journal.get("destination_created")
            and not journal.get("source_deleted")
            and destination.exists()
        ):
            try:
                shutil.rmtree(destination)
                journal["rollback"] = {"status": "done", "at": _now()}
            except OSError as rollback_exc:  # pragma: no cover - platform dependent
                journal["rollback"] = {
                    "status": "failed",
                    "error": str(rollback_exc),
                    "at": _now(),
                }
        elif not resume:
            journal["rollback"] = {
                "status": "skipped",
                "reason": "destination_not_created",
                "at": _now(),
            }
        _write_journal(journal_path, journal)
        raise

    _write_journal(journal_path, journal)
    return {
        "state": "complete",
        "resumed": resume,
        "plan": plan.to_dict(),
        "source_deleted": journal.get("source_deleted", False),
        "delete_reason": journal.get("delete_reason"),
        "rebind": journal.get("rebind"),
        "copy_report": journal.get("copy_report"),
        "warnings": list(plan.warnings),
        "journal": str(journal_path),
    }


def apply_relocation(
    plan: RelocationPlan,
    *,
    journal_dir: Path | str | None = None,
    registry: Any = None,
    confirm_delete_source: bool = False,
    fault_inject: FaultInjector | None = None,
) -> dict[str, Any]:
    """Execute a plan. The source is retained unless explicitly confirmed."""

    if confirm_delete_source and plan.operation != "move":
        raise RelocationRefused(
            "Only a move can delete its source; copy/clone always retain it.",
            reason="invalid_delete_request",
        )
    journal_path = _journal_path(plan, journal_dir)
    journal = _new_journal(plan, confirm_delete_source=confirm_delete_source)
    _write_journal(journal_path, journal)
    return _execute(
        plan,
        journal,
        journal_path,
        registry=registry,
        confirm_delete_source=confirm_delete_source,
        fault_inject=fault_inject,
        resume=False,
    )


def recover_relocation(
    journal_path: Path | str,
    *,
    registry: Any = None,
    confirm_delete_source: bool | None = None,
    fault_inject: FaultInjector | None = None,
) -> dict[str, Any]:
    """Resume an interrupted relocation from its journal.

    Already-completed steps are skipped; a partially copied tree is completed
    file by file because copying is idempotent and hash-verified.
    """

    path = Path(journal_path)
    if not path.exists():
        raise RelocationError(f"Journal not found: {path}", reason="journal_missing")
    journal = json.loads(path.read_text(encoding="utf-8"))
    if journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise RelocationError(
            f"Unsupported journal schema: {journal.get('schema_version')!r}",
            reason="journal_schema",
        )
    plan = RelocationPlan.from_dict(journal["plan"])
    if confirm_delete_source is None:
        confirmed = bool(journal.get("confirm_delete_source", False))
    else:
        confirmed = confirm_delete_source
        journal["confirm_delete_source"] = confirmed
    return _execute(
        plan,
        journal,
        path,
        registry=registry,
        confirm_delete_source=confirmed,
        fault_inject=fault_inject,
        resume=True,
    )


def verify_relocation(source: Path | str, destination: Path | str) -> dict[str, Any]:
    """Verify that two trees hold the same project identity and content."""

    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    report: dict[str, Any] = {
        "source": str(source_path),
        "destination": str(destination_path),
        "identity_preserved": False,
        "content_matches": False,
        "differences": [],
    }

    source_identity = read_identity(source_path)
    destination_identity = read_identity(destination_path)
    report["source_project_id"] = source_identity.project_id
    report["destination_project_id"] = destination_identity.project_id
    report["identity_preserved"] = (
        source_identity.project_id == destination_identity.project_id
        and source_identity.identity_hash == destination_identity.identity_hash
    )

    source_entries, source_warnings = scan_tree(source_path)
    destination_entries, destination_warnings = scan_tree(destination_path)
    source_map = {entry["path"]: entry["sha256"] for entry in source_entries}
    destination_map = {entry["path"]: entry["sha256"] for entry in destination_entries}

    for relative, digest in source_map.items():
        if relative not in destination_map:
            report["differences"].append(f"missing at destination: {relative}")
        elif destination_map[relative] != digest:
            report["differences"].append(f"content differs: {relative}")
    for relative in destination_map:
        if relative not in source_map:
            report["differences"].append(f"extra at destination: {relative}")

    report["content_matches"] = not report["differences"]
    report["warnings"] = source_warnings + destination_warnings
    report["source_files"] = len(source_entries)
    report["destination_files"] = len(destination_entries)
    return report
