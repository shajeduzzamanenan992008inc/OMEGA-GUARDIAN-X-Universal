"""Bounded project structure analysis."""

from __future__ import annotations

import json
import tomllib
from collections import Counter
from pathlib import Path

from ..models import ComponentInfo, MonorepoPackage, ProjectStructure, TreeEntry
from ..options import AnalysisContext
from .common import (
    ASSET_DIR_NAMES, CODE_SUFFIXES, DOCUMENTATION_DIR_NAMES, SCRIPT_DIR_NAMES,
    TEST_DIR_NAMES, is_counted_source, is_generated, is_monorepo_manifest, is_vendored,
    path_parts,
)

BUILD_OUTPUT_NAMES = ("dist", "build", "target", "out", "bin", "obj", ".next", ".nuxt", "coverage", "htmlcov")


def _top_level(path: str) -> str:
    parts = path_parts(path)
    return parts[0] if len(parts) > 1 else "."


def _workspace_indicators(context: AnalysisContext) -> list[str]:
    indicators: list[str] = []
    names = {Path(entry.path).name.casefold(): entry.path for entry in context.workspace.files}
    for marker in ("pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json", "go.work"):
        if marker in names:
            indicators.append(f"workspace file: {names[marker]}")
    package_json = names.get("package.json")
    if package_json:
        text = context.read_text(package_json, max_bytes=512_000)
        if text:
            try:
                data = json.loads(text)
                if isinstance(data.get("workspaces"), (list, dict)):
                    indicators.append(f"package.json workspaces: {package_json}")
            except json.JSONDecodeError:
                pass
    cargo = names.get("cargo.toml")
    if cargo:
        text = context.read_text(cargo, max_bytes=512_000)
        if text:
            try:
                data = tomllib.loads(text)
                if isinstance(data.get("workspace"), dict):
                    indicators.append(f"Cargo workspace: {cargo}")
            except tomllib.TOMLDecodeError:
                pass
    pyproject = names.get("pyproject.toml")
    if pyproject:
        text = context.read_text(pyproject, max_bytes=512_000)
        if text:
            try:
                data = tomllib.loads(text)
                tool = data.get("tool", {})
                if isinstance(tool, dict) and any(
                        isinstance(tool.get(manager), dict) and isinstance(tool.get(manager, {}).get("workspace"), dict)
                        for manager in ("uv", "pdm")):
                    indicators.append(f"Python workspace configuration: {pyproject}")
            except tomllib.TOMLDecodeError:
                pass
    return indicators


def _monorepo_packages(context: AnalysisContext) -> list[MonorepoPackage]:
    grouped: dict[str, list[str]] = {}
    for entry in context.workspace.files:
        if not is_monorepo_manifest(entry.path) or is_vendored(entry.path):
            continue
        parent = str(Path(entry.path).parent).replace("\\", "/")
        if parent in {".", ""}:
            continue
        grouped.setdefault(parent, []).append(entry.path)
    packages: list[MonorepoPackage] = []
    for path, manifests in sorted(grouped.items()):
        suffixes = Counter(Path(entry.path).suffix.casefold() for entry in context.workspace.files
                           if entry.path.startswith(path + "/") and Path(entry.path).suffix.casefold() in CODE_SUFFIXES)
        hint = suffixes.most_common(1)[0][0] if suffixes else ""
        packages.append(MonorepoPackage(path=path, manifests=sorted(manifests), language_hint=hint))
        if len(packages) >= 100:
            break
    return packages


def _tree_entries(context: AnalysisContext) -> tuple[list[TreeEntry], bool]:
    entries: dict[str, TreeEntry] = {}
    max_depth = context.options.max_depth
    for entry in context.workspace.files:
        parts = path_parts(entry.path)
        for index in range(1, min(len(parts), max_depth) + 1):
            path = "/".join(parts[:index])
            if index < len(parts):
                entries.setdefault(path, TreeEntry(path=path, kind="directory"))
            else:
                entries.setdefault(path, TreeEntry(path=path, kind="file", size=entry.size))
    ordered = sorted(entries.values(), key=lambda item: (item.path, item.kind))
    limit = context.options.max_tree_entries
    return ordered[:limit], len(ordered) > limit


def analyze_structure(context: AnalysisContext) -> ProjectStructure:
    structure = ProjectStructure()
    source_counts: Counter[str] = Counter()
    directory_paths: set[str] = set()
    generated = 0

    for entry in context.workspace.files:
        parts = path_parts(entry.path)
        for index in range(1, len(parts)):
            directory_paths.add("/".join(parts[:index]))
        if is_generated(entry.path):
            generated += 1
        if is_vendored(entry.path):
            continue
        if any(part.casefold() in TEST_DIR_NAMES for part in parts[:-1]):
            continue
        if Path(entry.path).suffix.casefold() in CODE_SUFFIXES:
            source_counts[_top_level(entry.path)] += 1

    structure.source_directories = [path for path, _ in source_counts.most_common(20)]
    test_dirs: set[str] = set()
    doc_dirs: set[str] = set()
    script_dirs: set[str] = set()
    asset_dirs: set[str] = set()
    for entry in context.workspace.files:
        parts = path_parts(entry.path)
        for index, part in enumerate(parts[:-1]):
            folded = part.casefold()
            prefix = "/".join(parts[:index + 1])
            if folded in TEST_DIR_NAMES:
                test_dirs.add(prefix)
            if folded in DOCUMENTATION_DIR_NAMES:
                doc_dirs.add(prefix)
            if folded in SCRIPT_DIR_NAMES:
                script_dirs.add(prefix)
            if folded in ASSET_DIR_NAMES:
                asset_dirs.add(prefix)
    structure.test_directories = sorted(test_dirs)[:50]
    structure.documentation_directories = sorted(doc_dirs)[:20]
    structure.script_directories = sorted(script_dirs)[:20]
    structure.asset_directories = sorted(asset_dirs)[:20]
    structure.ci_files = sorted(set(context.profile.ci_files))
    structure.infrastructure_files = sorted(
        entry.path for entry in context.workspace.files
        if Path(entry.path).name.casefold() in {
            "dockerfile", "containerfile", "docker-compose.yml", "docker-compose.yaml",
            "compose.yml", "compose.yaml", "chart.yaml", "ansible.cfg", "kustomization.yaml",
        } or Path(entry.path).suffix.casefold() in {".tf", ".tfvars"}
    )[:100]
    structure.build_output_directories = sorted(
        name for name in BUILD_OUTPUT_NAMES
        if (context.root / name).is_dir() and not (context.root / name).is_symlink()
    )
    structure.monorepo_packages = _monorepo_packages(context)
    structure.workspace_indicators = _workspace_indicators(context)
    structure.components = [
        ComponentInfo(path=package.path, kind="package", manifests=package.manifests)
        for package in structure.monorepo_packages
    ]
    for path in structure.source_directories[:20]:
        if path in {".", ""}:
            continue
        file_count = sum(1 for entry in context.workspace.files
                         if entry.path.startswith(path + "/")
                         and Path(entry.path).suffix.casefold() in CODE_SUFFIXES)
        structure.components.append(ComponentInfo(path=path, kind="source-directory", file_count=file_count))
    structure.tree, structure.tree_truncated = _tree_entries(context)
    structure.file_count = len(context.workspace.files)
    structure.directory_count = len(directory_paths)
    structure.total_bytes = context.workspace.total_bytes
    structure.generated_files = generated
    structure.warnings = list(context.workspace.warnings)
    structure.limitations = [
        "The project tree is bounded by depth and entry limits; it is not a complete filesystem inventory.",
        "Build output directories are recorded by name and are not traversed.",
        "Filesystem exclusions are directory-name based; permission errors are counted as skipped.",
    ]
    for limitation in structure.limitations:
        context.add_limitation("structure", limitation)
    if structure.tree_truncated:
        context.warnings.append(
            f"Project tree truncated to {context.options.max_tree_entries} entries.")
    return structure
