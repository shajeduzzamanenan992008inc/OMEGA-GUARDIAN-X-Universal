"""Shared bounded path heuristics for intelligence analyzers."""

from __future__ import annotations

from pathlib import Path

from ...engines.language_specs import SPECS
from ..models import AnalysisEvidence
from ..options import AnalysisContext

VENDORED_DIR_NAMES = {
    "node_modules", "vendor", "vendors", "third_party", "third-party",
    "bower_components", "site-packages", ".venv", "venv", "env",
    "dist", "build", "target", "out", ".next", ".nuxt", ".tox", ".nox",
    "coverage", "htmlcov", "__pycache__",
}

GENERATED_DIR_NAMES = {
    "generated", "gen", "__generated__", "auto-generated", "autogen",
}

GENERATED_SUFFIXES = (
    ".min.js", ".min.css", ".map", ".g.dart", ".freezed.dart",
    "_pb2.py", "_pb2_grpc.py", "_pb2.pyi", ".pb.go", ".pb.cc", ".pb.h",
    ".designer.cs", ".generated.cs", ".generated.ts", ".generated.js",
)

GENERATED_NAME_MARKERS = (".generated.", ".g.", ".designer.", ".min.")


def path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.replace("\\", "/").split("/") if part)


def is_vendored(path: str) -> bool:
    parts = path_parts(path)
    return any(part.casefold() in VENDORED_DIR_NAMES for part in parts[:-1])


def is_generated(path: str) -> bool:
    parts = path_parts(path)
    if any(part.casefold() in GENERATED_DIR_NAMES for part in parts[:-1]):
        return True
    name = Path(path).name.casefold()
    if name.endswith(GENERATED_SUFFIXES):
        return True
    return any(marker in name for marker in GENERATED_NAME_MARKERS)


def is_counted_source(path: str) -> bool:
    return not is_vendored(path) and not is_generated(path)


def evidence(kind: str, location: str, detail: str = "") -> AnalysisEvidence:
    return AnalysisEvidence(kind=kind, location=location, detail=detail)


CODE_CATEGORIES = {"language", "web", "automation"}


def spec_extension_map() -> dict[str, object]:
    mapping: dict[str, object] = {}
    for spec in SPECS:
        if spec.detection_mode != "extension":
            continue
        for extension in spec.extensions:
            mapping.setdefault(extension.casefold(), spec)
    return mapping


CODE_SUFFIXES: frozenset[str] = frozenset(
    extension
    for spec in SPECS
    if spec.category in CODE_CATEGORIES
    for extension in spec.extensions
)

TEST_DIR_NAMES = {
    "test", "tests", "__tests__", "spec", "specs", "e2e", "integration",
    "integration_tests", "androidtest", "unittest", "unit_tests", "__mocks__",
}

DOCUMENTATION_DIR_NAMES = {"docs", "doc", "documentation", "manual", "handbook"}
SCRIPT_DIR_NAMES = {"scripts", "script", "bin", "tools", "tooling", "hack", "ci"}
ASSET_DIR_NAMES = {
    "assets", "static", "public", "resources", "images", "media", "fonts",
    "locales", "i18n", "templates",
}

MONOREPO_MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts", "composer.json", "gemfile",
    "pubspec.yaml", "package.swift", "build.sbt", "mix.exs",
}

MONOREPO_MANIFEST_SUFFIXES = {".csproj", ".fsproj", ".vbproj", ".sln"}


def is_monorepo_manifest(path: str) -> bool:
    name = Path(path).name.casefold()
    return name in MONOREPO_MANIFEST_NAMES or Path(path).suffix.casefold() in MONOREPO_MANIFEST_SUFFIXES


def relative_evidence(context: AnalysisContext, kind: str, path: str) -> AnalysisEvidence:
    return evidence(kind, path, "Repository-relative path; file contents were not executed.")
