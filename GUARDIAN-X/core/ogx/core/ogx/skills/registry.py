from __future__ import annotations

import json
import re
from pathlib import Path
from importlib.resources import files as resource_files
from typing import Any

REQUIRED = {"name", "version", "description", "entrypoint", "permissions"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def validate_skill(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve(strict=True)
    manifest = path / "skill.json" if path.is_dir() else path
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Skill manifest must be a regular skill.json file.")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid skill manifest: {exc}") from exc
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ValueError("Skill manifest must be a JSON object.")
    missing = sorted(REQUIRED - data.keys())
    if missing:
        errors.append("Missing required fields: " + ", ".join(missing))
    name = data.get("name", "")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        errors.append("name must use lowercase letters, digits, dot, underscore or hyphen (2-64 chars).")
    permissions = data.get("permissions", [])
    allowed = {"read_project", "write_workspace", "network", "execute_tests", "git_read", "git_write", "provider_api"}
    if not isinstance(permissions, list) or any(p not in allowed for p in permissions):
        errors.append("permissions must be an array using only the documented permission names.")
    entrypoint = data.get("entrypoint")
    if not isinstance(entrypoint, str) or Path(entrypoint).is_absolute() or ".." in Path(entrypoint).parts:
        errors.append("entrypoint must be a relative path without parent traversal.")
    elif not (manifest.parent / entrypoint).is_file():
        errors.append("entrypoint file does not exist.")
    if data.get("execution") not in {None, "declarative", "external-adapter"}:
        errors.append("execution must be declarative or external-adapter; inline arbitrary execution is unsupported.")
    return {"manifest": str(manifest), "valid": not errors, "errors": errors, "skill": data,
            "security": {"loaded_as_code": False, "execution_performed": False, "note": "Manifest validation does not establish trust or safety of third-party skills."}}


def list_skills(paths: list[Path] | None = None) -> list[dict[str, Any]]:
    if paths is None:
        paths = [Path(str(resource_files("ogx").joinpath("skillpacks", "official")))]
    results = []
    for base in paths:
        if not base.exists() or base.is_symlink():
            continue
        manifests = [base] if base.name == "skill.json" else sorted(base.rglob("skill.json"))
        for manifest in manifests:
            try:
                result = validate_skill(manifest)
            except (OSError, ValueError) as exc:
                result = {"manifest": str(manifest), "valid": False, "errors": [str(exc)]}
            results.append(result)
    return results
