"""Versioned evolution and migration engine (AI-08).

A migration is an immutable, ordered manifest. Applying one:

1. refuses a downgrade (``to_version`` must be strictly greater than
   ``from_version``) and refuses a manifest that does not start from the
   project's currently recorded version;
2. checkpoints every affected file outside the project before touching it;
3. journals each completed step so an interrupted run is resumable;
4. applies steps through validated patch plans or contained atomic writes;
5. rolls back to the checkpoint on failure, and exposes an explicit
   ``recover`` for a crash or a failed step.

Nothing is applied without explicit approval. Owner decisions D1–D12 (state
layout, schema versions, conflict policy) are never changed by this engine.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..patching import PatchError, apply_patch, load_plan

SCHEMA_VERSION = 1
_STATE_SCHEMA = 1
_MANIFEST_KEYS = frozenset(
    {"schema_version", "name", "from_version", "to_version", "requires_schema", "steps", "description"}
)


class MigrationError(RuntimeError):
    """Raised when a migration is invalid, unsafe or cannot proceed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = str(value).split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise MigrationError(f"Invalid version: {value!r}.")
    major, minor, patch = (int(part) for part in parts)
    return major, minor, patch


def _write_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(temporary, mode)
    except OSError:
        pass
    os.replace(temporary, path)


def _safe_relative(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 300:
        raise MigrationError("Migration path is missing or too long.")
    if "\\" in value or value.startswith("/") or ".." in value.split("/"):
        raise MigrationError(f"Migration path is unsafe: {value!r}.")
    target = (root / value).resolve()
    resolved_root = root.resolve()
    if not str(target).startswith(str(resolved_root) + os.sep):
        raise MigrationError(f"Migration path escapes the project root: {value!r}.")
    return target


@dataclass(frozen=True, slots=True)
class MigrationStep:
    id: str
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Migration:
    name: str
    from_version: str
    to_version: str
    requires_schema: int
    steps: tuple[MigrationStep, ...]
    description: str = ""
    schema_version: int = SCHEMA_VERSION

    @staticmethod
    def _step_body(step: MigrationStep) -> dict[str, Any]:
        body = {"id": step.id, "kind": step.kind}
        if step.kind == "write_file":
            # Persist text form so a registered manifest round-trips through
            # parse_migration; the base64/ digest fields stay internal.
            body["path"] = step.payload["path"]
            body["content"] = base64.b64decode(
                str(step.payload["content_b64"])
            ).decode("utf-8")
            if step.payload.get("expected_sha256") is not None:
                body["expected_sha256"] = step.payload["expected_sha256"]
        else:
            body.update(dict(step.payload))
        return body

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "requires_schema": self.requires_schema,
            "steps": [self._step_body(step) for step in self.steps],
        }


def parse_migration(payload: Mapping[str, Any]) -> Migration:
    if not isinstance(payload, Mapping):
        raise MigrationError("Migration manifest must be an object.")
    if set(payload) - _MANIFEST_KEYS:
        raise MigrationError("Migration manifest contains unknown fields.")
    required = {"schema_version", "name", "from_version", "to_version", "steps"}
    if not required <= set(payload):
        raise MigrationError("Migration manifest is missing required fields.")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise MigrationError("Unsupported migration manifest schema.")
    name = str(payload["name"])
    if not name or len(name) > 80 or not all(c.isalnum() or c in "._-" for c in name):
        raise MigrationError("Migration name must be a short slug.")
    from_version = str(payload["from_version"])
    to_version = str(payload["to_version"])
    if _version_tuple(to_version) <= _version_tuple(from_version):
        raise MigrationError("A migration must move to a strictly greater version (no downgrade).")
    requires_schema = payload.get("requires_schema", 1)
    if not isinstance(requires_schema, int) or requires_schema < 1:
        raise MigrationError("requires_schema must be a positive integer.")

    raw_steps = payload["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise MigrationError("A migration needs at least one step.")
    if len(raw_steps) > 64:
        raise MigrationError("A migration is limited to 64 steps.")
    steps: list[MigrationStep] = []
    seen: set[str] = set()
    for item in raw_steps:
        if not isinstance(item, Mapping):
            raise MigrationError("Each migration step must be an object.")
        step_id = item.get("id")
        kind = item.get("kind")
        if not isinstance(step_id, str) or not step_id or step_id in seen:
            raise MigrationError("Each migration step needs a unique id.")
        seen.add(step_id)
        if kind == "patch_plan":
            if not isinstance(item.get("plan_id"), str) or not isinstance(
                item.get("plan_digest"), str
            ):
                raise MigrationError("A patch_plan step needs plan_id and plan_digest.")
            if len(str(item["plan_digest"])) != 64:
                raise MigrationError("plan_digest must be a SHA-256 hex digest.")
            payload_step = {"plan_id": item["plan_id"], "plan_digest": item["plan_digest"]}
        elif kind == "write_file":
            path = item.get("path")
            content = item.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise MigrationError("A write_file step needs a path and text content.")
            encoded = content.encode("utf-8")
            if len(encoded) > 1_000_000:
                raise MigrationError("write_file content exceeds the size limit.")
            payload_step = {
                "path": path,
                "content_b64": base64.b64encode(encoded).decode("ascii"),
                "content_sha256": _sha256(encoded),
                "expected_sha256": item.get("expected_sha256"),
            }
        elif kind == "check":
            minimum = item.get("min_files")
            if minimum is None or not isinstance(minimum, int) or minimum < 0:
                raise MigrationError("A check step needs a non-negative min_files.")
            payload_step = {"min_files": minimum}
        else:
            raise MigrationError(f"Unsupported migration step kind: {kind!r}.")
        steps.append(MigrationStep(step_id, str(kind), payload_step))
    return Migration(
        name=name,
        from_version=from_version,
        to_version=to_version,
        requires_schema=requires_schema,
        steps=tuple(steps),
        description=str(payload.get("description", ""))[:300],
    )


class EvolutionEngine:
    def __init__(self, state_home: Path | str) -> None:
        self.home = Path(state_home)
        self.root = self.home / "evolution"
        self.migrations_dir = self.root / "migrations"
        self.journal_dir = self.root / "journal"
        self.checkpoint_dir = self.root / "checkpoints"
        self.state_path = self.root / "state.json"

    # ---------------------------------------------------------------- state --
    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": _STATE_SCHEMA, "project_version": "0.0.0", "applied": []}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MigrationError("Evolution state is unreadable.") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != _STATE_SCHEMA:
            raise MigrationError("Unsupported evolution state schema.")
        return payload

    def current_version(self) -> str:
        return str(self._state()["project_version"])

    def set_baseline(self, version: str) -> None:
        major, minor, patch = _version_tuple(version)
        state = self._state()
        state["project_version"] = f"{major}.{minor}.{patch}"
        _write_json(self.state_path, state)

    # ----------------------------------------------------------- submission --
    def register(self, payload: Mapping[str, Any]) -> Migration:
        migration = parse_migration(payload)
        path = self.migrations_dir / f"{migration.name}.json"
        if path.exists():
            existing = parse_migration(json.loads(path.read_text(encoding="utf-8")))
            if existing.to_dict() != migration.to_dict():
                raise MigrationError(
                    "A different migration with this name is already registered; names are immutable."
                )
            return existing
        _write_json(path, migration.to_dict())
        return migration

    def _load(self, name: str) -> Migration:
        path = self.migrations_dir / f"{name}.json"
        if path.is_symlink() or not path.is_file():
            raise MigrationError(f"Migration {name!r} is not registered.")
        return parse_migration(json.loads(path.read_text(encoding="utf-8")))

    def _journal_path(self, name: str) -> Path:
        return self.journal_dir / f"{name}.json"

    def _read_journal(self, name: str) -> dict[str, Any] | None:
        path = self._journal_path(name)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MigrationError("Migration journal is unreadable.") from exc

    # ------------------------------------------------------------ execution --
    def _affected_paths(
        self, migration: Migration, state_home: Path, project_root: Path
    ) -> set[Path]:
        paths: set[Path] = set()
        for step in migration.steps:
            if step.kind == "write_file":
                paths.add(_safe_relative(project_root, str(step.payload["path"])))
            elif step.kind == "patch_plan":
                plan = load_plan(str(step.payload["plan_id"]), state_home=state_home)
                root = Path(str(plan["project_root"]))
                for change in plan.get("changes", []):
                    paths.add(_safe_relative(root, str(change["path"])))
        return paths

    def _checkpoint(
        self, migration: Migration, run_id: str, root: Path, state_home: Path
    ) -> dict[str, Any]:
        directory = self.checkpoint_dir / migration.name / run_id
        directory.mkdir(parents=True, exist_ok=False)
        files: list[dict[str, Any]] = []
        for target in sorted(self._affected_paths(migration, state_home, root)):
            entry: dict[str, Any] = {"path": str(target), "existed": target.exists()}
            if target.exists():
                if target.is_symlink() or not target.is_file():
                    raise MigrationError("Checkpoint refused a non-regular file.")
                data = target.read_bytes()
                backup = directory / f"{len(files):03d}.bin"
                backup.write_bytes(data)
                entry.update({"backup": backup.name, "sha256": _sha256(data), "mode": target.stat().st_mode & 0o777})
            files.append(entry)
        manifest = {
            "schema_version": _STATE_SCHEMA,
            "migration": migration.name,
            "run_id": run_id,
            "created_at": _now(),
            "root": str(root),
            "files": files,
        }
        _write_json(directory / "checkpoint.json", manifest)
        return manifest

    def _restore(self, migration: Migration, run_id: str) -> list[str]:
        directory = self.checkpoint_dir / migration.name / run_id
        manifest_path = directory / "checkpoint.json"
        if not manifest_path.is_file():
            raise MigrationError("Checkpoint manifest is missing; refusing a blind restore.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        restored: list[str] = []
        for entry in sorted(manifest.get("files", []), key=lambda item: item["path"], reverse=True):
            target = Path(entry["path"])
            if not entry.get("existed"):
                if target.exists():
                    target.unlink()
                    restored.append(str(target))
                continue
            data = (directory / entry["backup"]).read_bytes()
            if _sha256(data) != entry.get("sha256"):
                raise MigrationError("Checkpoint data is corrupt; refusing to restore it.")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".ogx-restore")
            temporary.write_bytes(data)
            try:
                os.chmod(temporary, int(entry.get("mode", 0o644)))
            except OSError:
                pass
            os.replace(temporary, target)
            restored.append(str(target))
        return restored

    def _run_step(
        self, step: MigrationStep, *, state_home: Path, project_root: Path
    ) -> dict[str, Any]:
        if step.kind == "patch_plan":
            plan_id = str(step.payload["plan_id"])
            plan = load_plan(plan_id, state_home=state_home)
            from .review import plan_digest

            if plan_digest(plan) != step.payload["plan_digest"]:
                raise MigrationError(
                    "Patch plan digest does not match the migration manifest; refusing to apply."
                )
            result = apply_patch(plan_id, approved=True, state_home=state_home)
            return {"plan_id": plan_id, "applied": result.get("applied", [])}
        if step.kind == "write_file":
            target = _safe_relative(project_root, str(step.payload["path"]))
            data = base64.b64decode(str(step.payload["content_b64"]))
            if _sha256(data) != step.payload["content_sha256"]:
                raise MigrationError("write_file content digest mismatch.")
            if target.is_symlink():
                raise MigrationError("write_file refuses a symlinked target.")
            current = target.read_bytes() if target.is_file() else None
            expected = step.payload.get("expected_sha256")
            if expected is not None and (current is None or _sha256(current) != str(expected)):
                raise MigrationError("write_file precondition hash mismatch.")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".ogx-migrate")
            temporary.write_bytes(data)
            os.replace(temporary, target)
            return {"path": str(target), "sha256": _sha256(data)}
        if step.kind == "check":
            count = sum(1 for item in project_root.rglob("*") if item.is_file())
            minimum = int(step.payload["min_files"])
            if count < minimum:
                raise MigrationError(
                    f"Post-condition check failed: {count} files < required {minimum}."
                )
            return {"files": count}
        raise MigrationError(f"Unsupported step kind: {step.kind!r}")

    def apply(
        self,
        name: str,
        *,
        approved: bool,
        state_home: Path | str,
        root: Path | str | None = None,
    ) -> dict[str, Any]:
        if not approved:
            raise MigrationError("Applying a migration requires explicit approval.")
        migration = self._load(name)
        journal = self._read_journal(name)
        if journal is not None and journal.get("state") == "applied":
            return {"migration": name, "state": "applied", "already_applied": True}
        if journal is not None and journal.get("state") in {"applying", "failed"}:
            raise MigrationError(
                "A previous run did not complete; run 'recover' before applying again."
            )
        current = self.current_version()
        if current != migration.from_version:
            raise MigrationError(
                f"Migration expects version {migration.from_version!r}, project is {current!r}."
            )
        state = self._state()
        if int(state.get("schema_version", _STATE_SCHEMA)) < migration.requires_schema:
            raise MigrationError("Project state schema is too old for this migration.")

        project_root = Path(root).resolve() if root is not None else Path.cwd().resolve()
        run_id = uuid.uuid4().hex
        self._checkpoint(migration, run_id, project_root, Path(state_home))
        journal = {
            "schema_version": _STATE_SCHEMA,
            "migration": name,
            "run_id": run_id,
            "state": "applying",
            "completed": [],
            "started_at": _now(),
        }
        _write_json(self._journal_path(name), journal)
        try:
            for step in migration.steps:
                if step.id in journal["completed"]:
                    continue
                detail = self._run_step(
                    step, state_home=Path(state_home), project_root=project_root
                )
                journal["completed"].append(step.id)
                journal.setdefault("details", {})[step.id] = detail
                _write_json(self._journal_path(name), journal)
        except (PatchError, MigrationError, OSError) as exc:
            journal["state"] = "failed"
            journal["error"] = f"{type(exc).__name__}: {exc}"[:400]
            journal["failed_at"] = _now()
            _write_json(self._journal_path(name), journal)
            raise MigrationError(
                f"Migration {name!r} failed ({type(exc).__name__}: {exc}) and left a"
                " checkpoint; run 'recover' to roll back."
            ) from exc

        journal["state"] = "applied"
        journal["applied_at"] = _now()
        _write_json(self._journal_path(name), journal)
        state = self._state()
        state["project_version"] = migration.to_version
        state["applied"] = [
            *state.get("applied", []),
            {"name": name, "from_version": migration.from_version, "to_version": migration.to_version, "applied_at": journal["applied_at"]},
        ]
        _write_json(self.state_path, state)
        return {
            "migration": name,
            "state": "applied",
            "from_version": migration.from_version,
            "to_version": migration.to_version,
            "run_id": run_id,
            "steps": journal["completed"],
        }

    def recover(self, name: str, *, approved: bool) -> dict[str, Any]:
        if not approved:
            raise MigrationError("Recovery restores files and requires explicit approval.")
        migration = self._load(name)
        journal = self._read_journal(name)
        if journal is None:
            raise MigrationError("No journal exists for this migration.")
        if journal.get("state") not in {"applying", "failed"}:
            raise MigrationError("Nothing to recover for this migration.")
        restored = self._restore(migration, str(journal["run_id"]))
        journal["state"] = "recovered"
        journal["recovered_at"] = _now()
        _write_json(self._journal_path(name), journal)
        return {"migration": name, "state": "recovered", "restored": restored}

    def rollback(self, name: str, *, approved: bool) -> dict[str, Any]:
        if not approved:
            raise MigrationError("Rolling back a migration requires explicit approval.")
        migration = self._load(name)
        journal = self._read_journal(name)
        if journal is None or journal.get("state") != "applied":
            raise MigrationError("Only an applied migration can be rolled back.")
        restored = self._restore(migration, str(journal["run_id"]))
        journal["state"] = "rolled_back"
        journal["rolled_back_at"] = _now()
        _write_json(self._journal_path(name), journal)
        state = self._state()
        if state.get("project_version") == migration.to_version:
            state["project_version"] = migration.from_version
            state["applied"] = [
                item for item in state.get("applied", []) if item.get("name") != name
            ]
            _write_json(self.state_path, state)
        return {"migration": name, "state": "rolled_back", "restored": restored}

    def status(self, name: str) -> dict[str, Any]:
        journal = self._read_journal(name)
        migration = self._load(name)
        return {
            "migration": name,
            "from_version": migration.from_version,
            "to_version": migration.to_version,
            "steps": len(migration.steps),
            "journal": journal,
            "current_version": self.current_version(),
        }

    def checkpoint_dir_for(self, name: str) -> Path:
        return self.checkpoint_dir / name

    def prune(self, name: str) -> None:
        """Remove checkpoints for a completed migration (explicit housekeeping)."""

        journal = self._read_journal(name)
        if journal is None or journal.get("state") != "applied":
            raise MigrationError("Only checkpoints of an applied migration may be pruned.")
        shutil.rmtree(self.checkpoint_dir / name, ignore_errors=True)
