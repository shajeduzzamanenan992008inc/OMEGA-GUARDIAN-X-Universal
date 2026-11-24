from __future__ import annotations

import json
import re
import tomllib
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import ScanConfig, load_config


def _component(name: str, declared: str, ecosystem: str, scope: str = "required") -> dict[str, Any]:
    safe_name = quote(name, safe="@._+-")
    raw = (declared or "").strip()
    exact = raw[2:] if raw.startswith("==") and not raw.startswith("===") else raw[3:] if raw.startswith("===") else raw
    is_exact = bool(re.fullmatch(r"[vV]?[0-9]+(?:\.[0-9A-Za-z_-]+){1,3}(?:[-+][0-9A-Za-z.-]+)?", exact))
    item: dict[str, Any] = {"type": "library", "name": name, "version": "UNKNOWN",
                            "purl": f"pkg:{ecosystem}/{safe_name}", "scope": scope,
                            "properties": [{"name": "ogx:declared-version", "value": declared or "UNSPECIFIED"},
                                           {"name": "ogx:version-resolution", "value": "unresolved-manifest-declaration"}]}
    if is_exact:
        item["version"] = exact.lstrip("vV")
        item["purl"] += "@" + quote(item["version"], safe=".+-_")
        item["properties"][1]["value"] = "exact-manifest-declaration; installed-version-not-verified"
    return item


def _add(store: dict[tuple[str, str, str], dict[str, Any]], ecosystem: str, name: str, version: str, scope: str = "required") -> None:
    if name:
        store[(ecosystem, name.lower(), version)] = _component(name, version, ecosystem, scope)


def _add_toml(data: dict[str, Any], store: dict, ecosystem: str, warnings: list[str], rel: str) -> None:
    if ecosystem == "pypi":
        project = data.get("project", {})
        deps = project.get("dependencies", []) if isinstance(project, dict) else []
        optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
        for raw in deps if isinstance(deps, list) else []:
            match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*(.*)", str(raw))
            if match: _add(store, ecosystem, match.group(1), match.group(2).strip() or "UNPINNED")
        if isinstance(optional, dict):
            for items in optional.values():
                for raw in items if isinstance(items, list) else []:
                    match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*(.*)", str(raw))
                    if match: _add(store, ecosystem, match.group(1), match.group(2).strip() or "UNPINNED", "optional")
        poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
        for section in ("dependencies", "dev-dependencies"):
            deps_map = poetry.get(section, {}) if isinstance(poetry, dict) else {}
            if isinstance(deps_map, dict):
                for name, spec in deps_map.items():
                    if name.lower() == "python": continue
                    version = spec if isinstance(spec, str) else str(spec.get("version", "UNPINNED")) if isinstance(spec, dict) else "UNPINNED"
                    _add(store, ecosystem, name, version, "development" if section == "dev-dependencies" else "required")
    elif ecosystem == "cargo":
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            deps = data.get(section, {})
            if isinstance(deps, dict):
                for name, spec in deps.items():
                    version = spec if isinstance(spec, str) else str(spec.get("version", "UNPINNED")) if isinstance(spec, dict) else "UNPINNED"
                    _add(store, ecosystem, name, version, "development" if "dev" in section else "required")


def generate_sbom(root: Path, config: ScanConfig | None = None) -> dict[str, Any]:
    """Generate a best-effort CycloneDX 1.5 manifest inventory, not a resolved SBOM."""
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    config = config or load_config()
    components: dict[tuple[str, str, str], dict[str, Any]] = {}
    manifests: list[str] = []
    warnings: list[str] = []
    excluded = set(config.excludes)
    for current, dirs, names in __import__("os").walk(root, topdown=True, followlinks=False):
        base = Path(current)
        dirs[:] = [d for d in dirs if d not in excluded and not (base / d).is_symlink()]
        for name in names:
            path = base / name
            if path.is_symlink() or not path.is_file(): continue
            rel = path.relative_to(root).as_posix()
            try:
                if path.stat().st_size > config.max_file_bytes: continue
                if name == "package.json":
                    data = json.loads(path.read_text(encoding="utf-8")); manifests.append(rel)
                    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                        deps = data.get(section, {})
                        if isinstance(deps, dict):
                            for dep, version in deps.items():
                                scope = "development" if section == "devDependencies" else "optional" if section == "optionalDependencies" else "required"
                                _add(components, "npm", dep, str(version), scope)
                elif name.startswith("requirements") and name.endswith(".txt"):
                    manifests.append(rel)
                    for line in path.read_text(encoding="utf-8-sig").splitlines():
                        line = line.strip()
                        if not line or line.startswith(("#", "-", "http://", "https://")): continue
                        match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)$", line)
                        if match: _add(components, "pypi", match.group(1), match.group(2).strip() or "UNPINNED")
                elif name == "pyproject.toml":
                    manifests.append(rel); _add_toml(tomllib.loads(path.read_text(encoding="utf-8")), components, "pypi", warnings, rel)
                elif name == "Cargo.toml":
                    manifests.append(rel); _add_toml(tomllib.loads(path.read_text(encoding="utf-8")), components, "cargo", warnings, rel)
                elif name == "go.mod":
                    manifests.append(rel); text = path.read_text(encoding="utf-8")
                    for dep, version in re.findall(r"(?m)^\s*([^\s()]+)\s+(v[^\s]+)", text): _add(components, "golang", dep, version)
                elif name == "composer.json":
                    manifests.append(rel); data = json.loads(path.read_text(encoding="utf-8"))
                    for section in ("require", "require-dev"):
                        deps = data.get(section, {})
                        if isinstance(deps, dict):
                            for dep, version in deps.items():
                                if dep.lower() != "php": _add(components, "composer", dep, str(version), "development" if section == "require-dev" else "required")
                elif name == "Gemfile":
                    manifests.append(rel)
                    for dep, version in re.findall(r"(?m)^\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", path.read_text(encoding="utf-8")):
                        _add(components, "gem", dep, version or "UNPINNED")
                elif name == "pubspec.yaml":
                    manifests.append(rel)
                    section = "required"
                    for line in path.read_text(encoding="utf-8").splitlines():
                        header = re.match(r"^(dev_dependencies|dependencies):", line)
                        if header: section = "development" if header.group(1) == "dev_dependencies" else "required"; continue
                        dep = re.match(r"^  ([A-Za-z0-9_-]+):\s*(.*)$", line)
                        if dep and dep.group(1) not in {"flutter", "sdk"}: _add(components, "pub", dep.group(1), dep.group(2).strip() or "UNPINNED", section)
                elif name.endswith(".csproj") or name.endswith(".vbproj") or name.endswith(".fsproj"):
                    manifests.append(rel)
                    for dep, version in re.findall(r"<PackageReference\s+Include=[\"']([^\"']+)[\"']\s+Version=[\"']([^\"']+)[\"']", path.read_text(encoding="utf-8"), flags=re.I):
                        _add(components, "nuget", dep, version)
                elif name == "pom.xml":
                    manifests.append(rel)
                    text = path.read_text(encoding="utf-8")
                    blocks = re.findall(r"<dependency>(.*?)</dependency>", text, flags=re.S)
                    for block in blocks:
                        group = re.search(r"<groupId>(.*?)</groupId>", block)
                        artifact = re.search(r"<artifactId>(.*?)</artifactId>", block)
                        version = re.search(r"<version>(.*?)</version>", block)
                        if artifact:
                            dep = (group.group(1).strip() + ":" if group else "") + artifact.group(1).strip()
                            _add(components, "maven", dep, version.group(1).strip() if version else "UNPINNED")
            except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                warnings.append(f"Could not parse {rel}: {type(exc).__name__}")
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}",
            "version": 1, "metadata": {"component": {"type": "application", "name": root.name, "version": "UNKNOWN"}},
            "components": sorted(components.values(), key=lambda x: (x["name"].lower(), x["version"])),
            "ogx": {"manifests": sorted(set(manifests)), "warnings": warnings,
                    "limitations": ["Manifest-derived inventory only; dependency resolution and transitive closure are not performed.",
                                    "Declared constraints are not proof of installed or selected versions.",
                                    "This SBOM has not been verified against a third-party CycloneDX validator."]}}
