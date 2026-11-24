"""Read-only local Git host adapter.

Only local inspection commands are issued. There is no push, fetch, merge,
reset, branch mutation, configuration change or remote access. Argument lists
are passed with ``shell=False`` and every call is bounded by a timeout.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..lifecycle import (
    HealthState,
    HostAdapter,
    HostHealth,
    HostLifecycleError,
)
from ...connectors.git import git_available, remote_summary, run_git

_DRIVE = re.compile(r"^[A-Za-z]:")


class GitHostError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "git_error") -> None:
        super().__init__(message, code=code)


class GitUnavailableError(GitHostError):
    def __init__(self) -> None:
        super().__init__(
            "Git executable was not found on PATH; the local Git host is unavailable",
            code="git_unavailable",
        )


class GitHostAdapter(HostAdapter):
    host_type = "local_git"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._root: Optional[Path] = None
        self._connected = False

    # ----------------------------------------------------------------- helpers
    def _git(self, args: list[str], timeout: Optional[float] = None) -> subprocess.CompletedProcess[str]:
        if not git_available():
            raise GitUnavailableError()
        if self._root is None:
            raise GitHostError("Repository root is not configured", code="not_configured")
        seconds = float(timeout if timeout is not None else self.config.timeout_seconds)
        try:
            return run_git(args, cwd=self._root, timeout=seconds)
        except subprocess.TimeoutExpired as exc:
            raise GitHostError(
                f"Git command timed out after {seconds:.1f}s: {args[0]}",
                code="git_timeout",
            ) from exc
        except FileNotFoundError as exc:
            raise GitUnavailableError() from exc
        except OSError as exc:
            raise GitHostError(f"Git command failed to start: {exc}", code="git_launch_failed") from exc

    def _checked(self, args: list[str], timeout: Optional[float] = None) -> str:
        result = self._git(args, timeout=timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")[:400]
            raise GitHostError(f"Git {args[0]} failed: {detail}", code="git_command_failed")
        return result.stdout

    @staticmethod
    def _safe_repo_path(path: str) -> str:
        raw = "" if path is None else str(path)
        if not raw or "\x00" in raw:
            raise GitHostError("Repository path is empty or invalid", code="path_rejected")
        if raw.startswith("-") or raw.startswith(("/", "\\")) or _DRIVE.match(raw):
            raise GitHostError("Absolute, option-like and drive paths are not accepted", code="path_rejected")
        normalized = raw.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise GitHostError("Repository path traversal is not accepted", code="path_traversal")
        if any(":" in part for part in parts):
            raise GitHostError("Repository paths may not contain ':'", code="path_rejected")
        return "/".join(parts)

    def _configured_root(self) -> Path:
        try:
            return Path(self.config.root).expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise GitHostError(f"Repository path does not exist: {self.config.root}", code="root_missing") from exc
        except (OSError, RuntimeError) as exc:
            raise GitHostError(f"Repository path cannot be resolved: {exc}", code="root_invalid") from exc

    def _top_level(self) -> Path:
        output = self._checked(["rev-parse", "--show-toplevel"]).strip()
        if not output:
            raise GitHostError("Git did not report a worktree root", code="root_invalid")
        return Path(output).resolve(strict=True)

    def _root_path(self) -> Path:
        if self._root is None:
            raise GitHostError("Host session is not connected", code="not_connected")
        return self._root

    def _has_head(self) -> bool:
        result = self._git(["rev-parse", "--verify", "--quiet", "HEAD"])
        return result.returncode == 0

    # --------------------------------------------------------------- lifecycle
    def validate(self) -> None:
        if not git_available():
            raise GitUnavailableError()
        root = self._configured_root()
        if not root.is_dir():
            raise GitHostError(f"Repository path is not a directory: {root}", code="root_invalid")
        check = run_git(["rev-parse", "--is-inside-work-tree"], cwd=root, timeout=self.config.timeout_seconds)
        if check.returncode != 0:
            detail = (check.stderr or check.stdout or "").strip()[:300]
            raise GitHostError(
                f"Not a Git worktree: {root}" + (f" ({detail})" if detail else ""),
                code="not_a_repository",
            )
        top_result = run_git(
            ["rev-parse", "--show-toplevel"], cwd=root, timeout=self.config.timeout_seconds
        )
        if top_result.returncode != 0:
            raise GitHostError("Git could not report the worktree root", code="root_invalid")
        top = Path(top_result.stdout.strip()).resolve(strict=True)
        if os.path.normcase(str(top)) != os.path.normcase(str(root)):
            raise GitHostError(
                "Configured path is a subdirectory of another repository; configure the repository root",
                code="repository_scope_mismatch",
            )

    def connect(self) -> None:
        self.validate()
        root = self._configured_root()
        self._root = root
        # Fail closed if a Git command cannot run at all.
        self._checked(["rev-parse", "--git-dir"])
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._root = None

    def capabilities(self) -> dict:
        return {
            "repository_metadata_read": "supported",
            "repository_structure_read": "supported",
            "branch_read": "supported",
            "commit_read": "supported",
            "file_read": "supported",
            "file_system_access": "partial",
            "workspace_read": "unsupported",
            "workspace_create": "unsupported",
            "workspace_delete": "unsupported",
            "workspace_start": "unsupported",
            "workspace_stop": "unsupported",
            "workspace_exec": "unsupported",
            "workspace_execute": "unsupported",
            "file_write": "unsupported",
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
        issues: list[str] = []
        details: dict = {}
        if not self._connected or self._root is None:
            return HostHealth(
                state=HealthState.UNKNOWN,
                checked_at=datetime.now(timezone.utc),
                issues=("session is not connected",),
            )
        details["root"] = str(self._root)
        if not git_available():
            issues.append("Git executable is no longer available on PATH")
        elif not self._root.exists():
            issues.append("repository path no longer exists")
        elif not self._root.is_dir():
            issues.append("repository path is no longer a directory")
        else:
            result = self._git(["rev-parse", "--is-inside-work-tree"])
            details["git_returncode"] = result.returncode
            if result.returncode != 0:
                issues.append("repository metadata is no longer readable")
            else:
                try:
                    if os.path.normcase(str(self._top_level())) != os.path.normcase(str(self._root)):
                        issues.append("repository root changed")
                except GitHostError as exc:
                    issues.append(str(exc))
                if not issues and not self._has_head():
                    details["unborn_head"] = True
                    issues.append("repository has no commits yet (unborn HEAD)")
        state = HealthState.HEALTHY if not issues else HealthState.DEGRADED
        return HostHealth(
            state=state,
            checked_at=datetime.now(timezone.utc),
            issues=tuple(issues),
            details=details,
        )

    # ------------------------------------------------------------------- reads
    def metadata(self) -> dict:
        root = self._root_path()
        branch = self._git(["branch", "--show-current"]).stdout.strip()
        head = None
        short_head = None
        if self._has_head():
            head = self._checked(["rev-parse", "HEAD"]).strip()
            short_head = self._checked(["rev-parse", "--short", "HEAD"]).strip()
        status = self._git(["status", "--porcelain", "--untracked-files=normal"])
        status_lines = [line for line in status.stdout.splitlines() if line]
        remotes: dict[str, dict] = {}
        remote_result = self._git(["remote", "-v"])
        if remote_result.returncode == 0:
            for line in remote_result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[2].startswith("("):
                    remotes.setdefault(parts[0], remote_summary(parts[1]))
        return {
            "root": str(root),
            "branch": branch or None,
            "detached": not bool(branch) and head is not None,
            "head": head,
            "short_head": short_head,
            "dirty": bool(status_lines),
            "changed_entry_count": len(status_lines),
            "remotes": remotes,
            "git_version": self._git(["--version"]).stdout.strip(),
        }

    def list_branches(self, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        output = self._checked([
            "for-each-ref",
            "--format=%(refname:short)%00%(objectname)%00%(HEAD)",
            "refs/heads",
            "refs/remotes",
        ])
        branches: list[dict] = []
        for line in output.splitlines()[:limit]:
            parts = line.split("\x00")
            if len(parts) >= 2 and parts[0]:
                branches.append({
                    "name": parts[0],
                    "commit": parts[1],
                    "current": len(parts) > 2 and parts[2].strip() == "*",
                })
        return branches

    def list_commits(self, limit: int = 50, ref: Optional[str] = None) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        if not self._has_head():
            return []
        revision = "HEAD"
        if ref:
            resolved = self._git(["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"])
            if resolved.returncode != 0:
                raise GitHostError(f"Unknown commit reference: {ref}", code="ref_not_found")
            revision = resolved.stdout.strip()
        output = self._checked([
            "log",
            "-n", str(limit),
            "--format=%H%x00%an%x00%ae%x00%aI%x00%s",
            revision,
            "--",
        ])
        commits: list[dict] = []
        for line in output.splitlines():
            parts = line.split("\x00")
            if len(parts) >= 5:
                commits.append({
                    "sha": parts[0],
                    "author": parts[1],
                    "author_email": parts[2],
                    "date": parts[3],
                    "subject": parts[4],
                })
        return commits

    def list_tree(self, path: str = "", recursive: bool = False, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), 2000))
        if not self._has_head():
            return []
        target = "HEAD" if not path else f"HEAD:{self._safe_repo_path(path)}"
        args = ["ls-tree", "-z", "--name-only"]
        if recursive:
            args.append("-r")
        args.append(target)
        output = self._checked(args)
        entries = [item for item in output.split("\x00") if item]
        return [{"path": item} for item in entries[:limit]]

    def read_file(self, path: str, max_bytes: Optional[int] = None) -> dict:
        safe = self._safe_repo_path(path)
        if not self._has_head():
            raise GitHostError("Repository has no commits; file content is unavailable", code="unborn_head")
        spec = f"HEAD:{safe}"
        object_type = self._checked(["cat-file", "-t", spec]).strip()
        if object_type != "blob":
            raise GitHostError(f"Not a file at HEAD: {path} ({object_type})", code="not_a_file")
        size = int(self._checked(["cat-file", "-s", spec]).strip() or "0")
        limit = self.config.max_file_bytes if max_bytes is None else max(0, int(max_bytes))
        if size > limit:
            raise GitHostError(
                f"File is {size} bytes, above the {limit}-byte limit",
                code="file_too_large",
            )
        data = self._git(["cat-file", "blob", spec]).stdout.encode("utf-8", errors="replace")
        # stdout was decoded as text by run_git; hash the re-encoded bytes and
        # report that limitation rather than claiming binary identity.
        binary = b"\x00" in data
        return {
            "path": safe,
            "size": size,
            "bytes_read": len(data),
            "truncated": False,
            "binary": binary,
            "text": None if binary else data.decode("utf-8", errors="replace"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "hash_basis": "decoded-subprocess-output",
        }
