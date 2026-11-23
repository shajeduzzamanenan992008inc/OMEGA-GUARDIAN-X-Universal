"""Single source of truth for product names, folder conventions and exclusion sets.

Before the restructure these facts were duplicated in config.py, detector.py, sandbox.py,
policy.py and integration.py (and the shield folder name contained spaces). Every module now
imports them from here, so they cannot drift apart.

Layer 0: imports nothing else from ogx.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

PRODUCT_NAME = "OMEGA GUARDIAN X"
CLI_NAME = "ogx"
DEFAULT_SHIELD_DIRNAME = "omega-shield"
SHIELD_DIRNAME_ENV = "OMEGA_SHIELD_DIRNAME"  # set by init.py to the real (possibly renamed) folder name
HOST_ROOT_ENV = "OMEGA_HOST_ROOT"
SHIELD_HOME_ENV = "OMEGA_SHIELD_HOME"  # absolute path of the shield folder; set by init.py

VCS_DIRS = (".git", ".hg", ".svn")
VIRTUALENV_DIRS = (".venv", "venv", "env", "site-packages")
DEPENDENCY_DIRS = ("node_modules", "vendor")
BUILD_DIRS = ("dist", "build", "target", ".next", ".nuxt", "coverage")
CACHE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
IDE_DIRS = (".idea", ".vscode")
LEGACY_LOCAL_DIRS = (".ogx",)  # never trusted as configuration, never scanned


def shield_dirname() -> str:
    """Folder name of the dropped-in shield (default ``omega-shield``; no spaces, ASCII)."""
    name = os.environ.get(SHIELD_DIRNAME_ENV, "").strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return DEFAULT_SHIELD_DIRNAME
    return name


def shield_home() -> Path | None:
    """Absolute path of the running shield folder, when launched through init.py."""
    value = os.environ.get(SHIELD_HOME_ENV, "").strip()
    return Path(value).expanduser() if value else None


def default_excludes() -> list[str]:
    """Directory names that are never scanned, detected, inventoried or copied into a sandbox."""
    names = (*VCS_DIRS, *VIRTUALENV_DIRS, *DEPENDENCY_DIRS, *BUILD_DIRS, *CACHE_DIRS, *IDE_DIRS, shield_dirname())
    return list(dict.fromkeys(names))


def policy_relpath() -> Path:
    """Project policy (restrict-only) lives inside the shield folder."""
    return Path(shield_dirname()) / "shield.toml"


def manifest_relpath() -> Path:
    """Ownership manifest of the opt-in host linker."""
    return Path(shield_dirname()) / "link-manifest.json"


def is_shield_folder(path: Path) -> bool:
    """True when `path` looks like a real, installed shield folder."""
    return path.is_dir() and (path / "init.py").is_file() and (path / "core" / "ogx").is_dir()


def is_ogx_source(root: Path) -> bool:
    """True when `root` is the OGX/shield source tree itself (self-audit mode)."""
    shield_layout = (root / "core" / "ogx").is_dir() and (root / "VERSION").is_file()
    legacy_layout = (root / "pyproject.toml").is_file() and (root / "src" / "ogx").is_dir()
    return shield_layout or legacy_layout


# ---- opt-in host link vocabulary (shared by the linker, the CLI and the approval validator)
LINK_GROUPS: dict[str, str] = {
    "copilot": "GitHub Copilot instructions and custom agents (.github/copilot-instructions.md, .github/instructions, .github/agents)",
    "agents": "Root agent guides: AGENTS.md, CLAUDE.md, GEMINI.md, CODEX.md, CURSOR.md, WINDSURF.md",
    "editors": "Editor rule files for Cursor, Cline, Windsurf, Continue and Roo",
    "deps": "Dependency-update bots: .github/dependabot.yml and renovate.json",
}


def parse_link_groups(values: Iterable[str] | None) -> list[str]:
    """Accept repeated or comma-separated group names; ``all`` selects every group."""
    selected: list[str] = []
    for value in values or []:
        for item in str(value).split(","):
            item = item.strip().lower()
            if not item:
                continue
            if item == "all":
                selected.extend(LINK_GROUPS)
            elif item in LINK_GROUPS:
                selected.append(item)
            else:
                raise ValueError(f"Unknown link group {item!r}. Choose from: {', '.join(LINK_GROUPS)}, all.")
    return list(dict.fromkeys(selected))
