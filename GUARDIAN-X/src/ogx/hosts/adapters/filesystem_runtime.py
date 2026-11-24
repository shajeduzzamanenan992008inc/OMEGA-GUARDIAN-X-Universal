"""Scoped read-only local filesystem host adapter.

Every path is resolved and checked against the canonical configured root with
``os.path.commonpath``; symlink escapes are rejected because containment is
checked after ``resolve()``. No write, execute or network operation exists.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from ..lifecycle import (
    HealthState,
    HostAdapter,
    HostHealth,
    HostLifecycleError,
)


class HostPathError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "path_rejected") -> None:
        super().__init__(message, code=code)


class HostPermissionError(HostLifecycleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="permission_denied")


class HostReadError(HostLifecycleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="read_failed")


def _within(root: Path, candidate: Path) -> bool:
    try:
        root_key = os.path.normcase(str(root))
        candidate_key = os.path.normcase(str(candidate))
        return os.path.commonpath([root_key, candidate_key]) == root_key
    except (ValueError, OSError):
        return False


class FilesystemHostAdapter(HostAdapter):
    host_type = "local_filesystem"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._root: Optional[Path] = None
        self._connected = False

    # ------------------------------------------------------------------ config
    def _configured_root(self) -> Path:
        try:
            return Path(self.config.root).expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise HostPathError(f"Configured root does not exist: {self.config.root}", code="root_missing") from exc
        except (OSError, RuntimeError) as exc:
            raise HostPathError(f"Configured root cannot be resolved: {exc}", code="root_invalid") from exc

    def _all_roots(self) -> list[Path]:
        roots = [self._root] if self._root is not None else [self._configured_root()]
        for extra in self.config.allowed_roots:
            try:
                resolved = Path(extra).expanduser().resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise HostPathError(f"Allowed root cannot be resolved: {extra}", code="root_invalid") from exc
            roots.append(resolved)
        return roots

    def _reject_relative(self, relative: str) -> None:
        raw = "" if relative is None else str(relative)
        if "\x00" in raw:
            raise HostPathError("Path contains a NUL byte", code="path_rejected")
        candidate = Path(raw)
        if candidate.is_absolute() or candidate.drive or raw.startswith(("\\\\", "//")):
            raise HostPathError("Absolute, drive and UNC paths are not accepted", code="path_rejected")
        parts = [part for part in raw.replace("\\", "/").split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise HostPathError("Path traversal is not accepted", code="path_traversal")

    def _resolve(self, relative: str, *, must_exist: bool = True) -> Path:
        self._reject_relative(relative)
        roots = self._all_roots()
        candidate = roots[0].joinpath(*[
            part for part in str(relative or "").replace("\\", "/").split("/")
            if part not in ("", ".")
        ])
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise HostPathError(f"Path does not exist: {relative}", code="not_found") from exc
        except (OSError, RuntimeError) as exc:
            raise HostPathError(f"Path cannot be resolved: {exc}", code="path_invalid") from exc
        if not any(_within(root, resolved) for root in roots):
            raise HostPathError("Path resolves outside the permitted root", code="outside_root")
        return resolved

    # --------------------------------------------------------------- lifecycle
    def validate(self) -> None:
        root = self._configured_root()
        if not root.is_dir():
            raise HostPathError(f"Configured root is not a directory: {root}", code="root_invalid")
        if not os.access(root, os.R_OK | os.X_OK):
            raise HostPermissionError(f"Configured root is not readable: {root}")

    def connect(self) -> None:
        self.validate()
        self._root = self._configured_root()
        try:
            with os.scandir(self._root) as iterator:
                next(iterator, None)
        except PermissionError as exc:
            raise HostPermissionError(f"Cannot list configured root: {self._root}") from exc
        except OSError as exc:
            raise HostReadError(f"Cannot open configured root: {exc}") from exc
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._root = None

    def _require_connected(self) -> Path:
        if not self._connected or self._root is None:
            raise HostReadError("Host session is not connected")
        return self._root

    def capabilities(self) -> dict:
        return {
            "repository_metadata_read": "supported",
            "repository_structure_read": "supported",
            "file_read": "supported",
            "file_system_access": "supported",
            "workspace_read": "partial",
            "workspace_create": "unsupported",
            "workspace_delete": "unsupported",
            "workspace_start": "unsupported",
            "workspace_stop": "unsupported",
            "workspace_exec": "unsupported",
            "workspace_execute": "unsupported",
            "file_write": "unsupported",
            "branch_read": "unsupported",
            "commit_read": "unsupported",
            "branch_create": "unsupported",
            "branch_delete": "unsupported",
            "commit_create": "unsupported",
            "push": "unsupported",
            "fetch": "unsupported",
            "clone": "unsupported",
            "repository_write": "unsupported",
            "pull_request_create": "unsupported",
            "pull_request_merge": "unsupported",
            "testing": "unsupported",
            "deployment": "unsupported",
        }

    # ------------------------------------------------------------------ health
    def health(self) -> HostHealth:
        from datetime import datetime, timezone

        issues: list[str] = []
        details: dict = {}
        if not self._connected or self._root is None:
            return HostHealth(
                state=HealthState.UNKNOWN,
                checked_at=datetime.now(timezone.utc),
                issues=("session is not connected",),
            )
        root = self._root
        exists = root.exists()
        is_dir = root.is_dir()
        readable = exists and is_dir and os.access(root, os.R_OK | os.X_OK)
        details.update({"root": str(root), "exists": exists, "is_directory": is_dir, "readable": readable})
        if not exists:
            issues.append("root no longer exists")
        elif not is_dir:
            issues.append("root is no longer a directory")
        elif not readable:
            issues.append("root is no longer readable")
        else:
            try:
                current = root.resolve(strict=True)
                if os.path.normcase(str(current)) != os.path.normcase(str(root)):
                    issues.append("root now resolves to a different location")
            except (OSError, RuntimeError) as exc:
                issues.append(f"root cannot be resolved: {exc}")
        state = HealthState.HEALTHY if not issues else HealthState.UNREACHABLE
        return HostHealth(
            state=state,
            checked_at=datetime.now(timezone.utc),
            issues=tuple(issues),
            details=details,
        )

    # ------------------------------------------------------------------- reads
    def metadata(self) -> dict:
        root = self._require_connected()
        return {"root": str(root), "host_type": self.host_type, "read_only": True}

    def list_directory(self, path: str = "") -> dict:
        self._require_connected()
        target = self._resolve(path, must_exist=True)
        if not target.is_dir():
            raise HostReadError(f"Not a directory: {path}")
        entries: list[dict] = []
        issues: list[str] = []
        truncated = False
        try:
            with os.scandir(target) as iterator:
                for entry in iterator:
                    if len(entries) >= self.config.max_directory_entries:
                        truncated = True
                        break
                    record = {
                        "name": entry.name,
                        "path": (Path(path) / entry.name).as_posix(),
                        "type": "directory" if entry.is_dir(follow_symlinks=False) else "file",
                        "is_symlink": entry.is_symlink(),
                    }
                    try:
                        if entry.is_symlink():
                            resolved = Path(entry.path).resolve(strict=True)
                            record["escapes_root"] = not any(
                                _within(root, resolved) for root in self._all_roots()
                            )
                            record["type"] = (
                                "directory" if resolved.is_dir() else "file"
                            )
                            if record["escapes_root"]:
                                record["type"] = "symlink"
                        elif entry.is_file(follow_symlinks=False):
                            record["size"] = entry.stat(follow_symlinks=False).st_size
                    except (OSError, RuntimeError) as exc:
                        issues.append(f"{entry.name}: {exc}")
                    entries.append(record)
        except PermissionError as exc:
            raise HostPermissionError(f"Permission denied listing: {path}") from exc
        except OSError as exc:
            raise HostReadError(f"Cannot list directory: {exc}") from exc
        entries.sort(key=lambda item: (item["type"], item["name"].lower()))
        return {
            "path": Path(path or ".").as_posix(),
            "entries": entries,
            "truncated": truncated,
            "issues": issues,
        }

    def read_file(self, path: str, max_bytes: Optional[int] = None) -> dict:
        self._require_connected()
        target = self._resolve(path, must_exist=True)
        if target.is_dir():
            raise HostReadError(f"Path is a directory: {path}")
        if not target.is_file():
            raise HostReadError(f"Path is not a regular file: {path}")
        limit = self.config.max_file_bytes if max_bytes is None else max(0, int(max_bytes))
        try:
            size = target.stat().st_size
            with open(target, "rb") as handle:
                data = handle.read(limit + 1)
        except PermissionError as exc:
            raise HostPermissionError(f"Permission denied reading: {path}") from exc
        except OSError as exc:
            raise HostReadError(f"Cannot read file: {exc}") from exc
        truncated = len(data) > limit
        if truncated:
            data = data[:limit]
        is_binary = b"\x00" in data
        text = None if is_binary else data.decode("utf-8", errors="replace")
        return {
            "path": Path(path).as_posix(),
            "size": size,
            "bytes_read": len(data),
            "truncated": truncated,
            "binary": is_binary,
            "text": text,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def discover_projects(self, limit: int = 100) -> list[dict]:
        root = self._require_connected()
        try:
            from ...discovery import discover_root
            projects = discover_root(root)
        except Exception as exc:
            raise HostReadError(f"Project discovery failed: {exc}") from exc
        return [
            {
                "name": project.candidate.project_name,
                "path": str(project.candidate.root_path),
                "languages": list(project.candidate.languages),
                "is_git_repo": project.candidate.is_git_repo,
                "confidence": project.candidate.confidence,
            }
            for project in projects[: max(1, int(limit))]
        ]
