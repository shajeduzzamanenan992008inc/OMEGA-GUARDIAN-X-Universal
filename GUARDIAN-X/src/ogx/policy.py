from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

POLICY_PATH = Path("OMEGA GUARDIAN X") / "policies" / "default.toml"


def load_project_policy(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=True)
    path = root / POLICY_PATH
    if not path.exists():
        return {"source": "built-in-default", "policy": {"mode": "read-only", "allow_file_writes": False, "allow_network": False, "allow_execution": False}}
    if path.is_symlink() or not path.is_file():
        raise ValueError("OGX policy must be a regular file, not a symlink.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Invalid project policy: {exc}") from exc
    security = data.get("security", {})
    if not isinstance(security, dict):
        raise ValueError("[security] must be a TOML table.")
    for key in ("allow_file_writes", "allow_network", "allow_execution"):
        if key in security and not isinstance(security[key], bool):
            raise ValueError(f"security.{key} must be boolean.")
    # Policy can restrict but cannot grant capabilities beyond explicit command approval.
    return {"source": str(path), "policy": {"mode": "project-policy", **security},
            "note": "Policy settings can restrict OGX; they do not replace per-operation confirmation."}
