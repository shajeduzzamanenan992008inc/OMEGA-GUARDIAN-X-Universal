from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit

TIMEOUT_SECONDS = 30


def git_available() -> bool:
    return shutil.which("git") is not None


def _run(args: list[str], cwd: Path | None = None, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    if not git_available():
        raise RuntimeError("Git executable was not found on PATH.")
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, text=True,
                          encoding="utf-8", errors="replace", capture_output=True,
                          timeout=timeout, check=False, shell=False)


def detect_provider(remote: str) -> str:
    value = remote.lower().strip()
    if "github.com" in value:
        return "github"
    if "gitlab.com" in value or "gitlab." in value:
        return "gitlab"
    if "bitbucket.org" in value:
        return "bitbucket"
    if "gitea" in value or "forgejo" in value:
        return "gitea-or-forgejo"
    if re.match(r"^[^/@:]+@[^/:]+:.+$", value):
        return "generic-git"
    parsed = urlparse(value if "://" in value else "https://" + value)
    return "generic-git" if parsed.netloc else "unknown"


def remote_summary(remote: str) -> dict[str, str]:
    clean = remote.strip()
    # Strip userinfo, query parameters and fragments so tokenized remote URLs
    # cannot leak into console output or generated reports.
    if "://" in clean:
        try:
            parsed = urlsplit(clean)
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parsed.port:
                host += f":{parsed.port}"
            if host:
                clean = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            else:
                clean = clean.split("?", 1)[0].split("#", 1)[0]
        except ValueError:
            clean = clean.split("?", 1)[0].split("#", 1)[0]
    return {"url": clean, "provider": detect_provider(clean)}


def inspect_repository(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    check = _run(["rev-parse", "--is-inside-work-tree"], cwd=root)
    if check.returncode != 0:
        raise ValueError(f"Not a Git worktree: {root}")
    branch = _run(["branch", "--show-current"], cwd=root)
    status = _run(["status", "--short", "--branch"], cwd=root)
    remotes = _run(["remote", "-v"], cwd=root)
    remote_items: dict[str, dict[str, str]] = {}
    if remotes.returncode == 0:
        for line in remotes.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[2].startswith("("):
                remote_items.setdefault(parts[0], remote_summary(parts[1]))
    return {"root": str(root), "branch": branch.stdout.strip() if branch.returncode == 0 else "",
            "status": status.stdout.splitlines(), "remotes": remote_items,
            "dirty": bool(status.stdout.strip().splitlines()[1:]), "git_version": _run(["--version"]).stdout.strip()}


def inspect_remote(url: str) -> dict[str, Any]:
    summary = remote_summary(url)
    result = _run(["ls-remote", "--symref", "--", summary["url"]], timeout=45)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        # Do not echo URLs that may carry credentials.
        raise RuntimeError(f"Remote inspection failed ({summary['provider']}): {message[:500]}")
    refs = []
    for line in result.stdout.splitlines()[:500]:
        parts = line.split("\t", 1)
        if len(parts) == 2:
            refs.append({"object": parts[0], "ref": parts[1]})
    return {**summary, "reachable": True, "refs": refs, "ref_count_sampled": len(refs)}


def clone_repository(url: str, destination: Path, *, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise PermissionError("Clone writes to disk. Re-run with explicit --confirm after reviewing the destination.")
    target = destination.expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Destination already exists; OGX will not overwrite it: {target}")
    for ancestor in (target.parent, *target.parent.parents):
        if ancestor.is_symlink():
            raise ValueError("Refusing to clone through a symlinked parent directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    summary = remote_summary(url)
    result = _run(["clone", "--", summary["url"], str(target)], timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Clone failed: {(result.stderr or result.stdout).strip()[:700]}")
    return {"destination": str(target.resolve()), "provider": summary["provider"], "cloned": True}
