from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDES = [
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "vendor",
    "dist", "build", "target", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".next", ".nuxt", "coverage", "site-packages", ".idea", ".vscode",
]


@dataclass(slots=True)
class ScanConfig:
    excludes: list[str] = field(default_factory=lambda: DEFAULT_EXCLUDES.copy())
    max_files: int = 12000
    max_file_bytes: int = 2_000_000


def state_dir() -> Path:
    """Return OGX-owned storage outside every scanned repository."""
    override = os.environ.get("OGX_HOME")
    if override:
        path = Path(override).expanduser()
        if path.is_symlink():
            raise ValueError("OGX_HOME must not be a symlink.")
        return path.absolute()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "OMEGA-GUARDIAN-X"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "omega-guardian-x"


def load_config() -> ScanConfig:
    """Load only the user's global OGX config; never read config from target repos."""
    path = state_dir() / "config.toml"
    if path.is_symlink():
        raise ValueError("Global OGX config must not be a symlink.")
    if not path.exists():
        return ScanConfig()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        scan = raw.get("scan", {})
        excludes = scan.get("exclude", DEFAULT_EXCLUDES)
        max_files = int(scan.get("max_files", 12000))
        max_file_bytes = int(scan.get("max_file_bytes", 2_000_000))
        if max_files < 1 or max_files > 1_000_000:
            raise ValueError("scan.max_files must be between 1 and 1,000,000")
        if max_file_bytes < 1024 or max_file_bytes > 100_000_000:
            raise ValueError("scan.max_file_bytes must be between 1 KB and 100 MB")
        if not isinstance(excludes, list) or not all(isinstance(x, str) for x in excludes):
            raise ValueError("scan.exclude must be an array of strings")
        return ScanConfig(sorted(set(DEFAULT_EXCLUDES + excludes)), max_files, max_file_bytes)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid global OGX config at {path}: {exc}") from exc
