"""Build-system intelligence: configuration discovery only, no build execution."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from ..models import AnalysisEvidence, BuildSystemProfile, Confidence
from ..options import AnalysisContext

PYTHON_BACKENDS = {
    "poetry-core": "Poetry",
    "poetry_core": "Poetry",
    "hatchling": "Hatch",
    "setuptools": "setuptools",
    "flit-core": "Flit",
    "flit_core": "Flit",
    "maturin": "Maturin",
    "pdm-backend": "PDM",
    "pdm_backend": "PDM",
    "uv_build": "uv",
    "scikit-build-core": "scikit-build-core",
    "mesonpy": "Meson",
}

NODE_BUILD_TOOLS = (
    ("vite.config", "Vite"),
    ("next.config", "Next.js"),
    ("webpack.config", "webpack"),
    ("rollup.config", "Rollup"),
    ("esbuild.config", "esbuild"),
    (".parcelrc", "Parcel"),
    ("tsconfig.json", "TypeScript compiler"),
)

BUILD_COMMAND = re.compile(
    r"(?i)\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?build\b|"
    r"\bcargo\s+build\b|\bgo\s+build\b|\bgradle(?:w)?\b[^\n]*\bbuild\b|"
    r"\bmvn\b[^\n]*\b(?:package|install|verify)\b|\bdotnet\s+build\b|"
    r"\bcmake\s+--build\b|\bmake\b|\bswift\s+build\b|\bflutter\s+build\b|"
    r"\bpython\s+-m\s+build\b|\bpoetry\s+build\b|\buv\s+build\b"
)


def _add(systems: list[BuildSystemProfile], name: str, category: str, manifest: str,
         confidence: Confidence, evidence: list[str], notes: list[str] | None = None) -> None:
    if any(item.name == name and item.manifest == manifest for item in systems):
        return
    systems.append(BuildSystemProfile(
        name=name, category=category, manifest=manifest, confidence=confidence,
        evidence=sorted(set(evidence)), notes=notes or [],
    ))


def _ci_files(context: AnalysisContext) -> list[str]:
    return sorted(set(context.profile.ci_files) | {
        entry.path for entry in context.workspace.files
        if Path(entry.path).name.casefold() in {
            "jenkinsfile", "bitbucket-pipelines.yml", "azure-pipelines.yml",
        } or entry.path.startswith(".github/workflows/") or entry.path.startswith(".gitlab/")
    })


def analyze_build_systems(context: AnalysisContext) -> list[BuildSystemProfile]:
    systems: list[BuildSystemProfile] = []
    names = {Path(entry.path).name.casefold(): entry.path for entry in context.workspace.files}

    # Python packaging backends.
    pyproject = names.get("pyproject.toml")
    if pyproject:
        text = context.read_text(pyproject, max_bytes=512_000)
        if text:
            try:
                data = tomllib.loads(text)
                build = data.get("build-system", {})
                requires = build.get("requires", []) if isinstance(build, dict) else []
                backend = build.get("build-backend", "") if isinstance(build, dict) else ""
                matched = False
                for requirement in requires if isinstance(requires, list) else []:
                    normalized = str(requirement).casefold().split()[0].split(">=")[0].split("==")[0]
                    for key, name in PYTHON_BACKENDS.items():
                        if key in normalized or key in str(backend).casefold():
                            _add(systems, name, "python", pyproject, Confidence.CONFIRMED,
                                 [f"build-system backend: {backend or key}", f"pyproject.toml: {pyproject}"])
                            matched = True
                if not matched and isinstance(build, dict) and build:
                    _add(systems, "Python build backend (unspecified)", "python", pyproject,
                         Confidence.LIKELY, [f"pyproject.toml build-system: {pyproject}"])
            except tomllib.TOMLDecodeError:
                context.warnings.append("Could not parse pyproject.toml build-system.")
    if names.get("setup.py") or names.get("setup.cfg"):
        manifest = names.get("setup.py") or names.get("setup.cfg")
        _add(systems, "setuptools", "python", manifest, Confidence.LIKELY,
             [f"setup metadata: {manifest}"])

    # Node build tooling.
    package_json = names.get("package.json")
    if package_json:
        text = context.read_text(package_json, max_bytes=512_000)
        scripts: dict = {}
        if text:
            try:
                data = json.loads(text)
                scripts = data.get("scripts", {}) if isinstance(data.get("scripts"), dict) else {}
            except json.JSONDecodeError:
                context.warnings.append("Could not parse package.json scripts.")
        for marker, tool in NODE_BUILD_TOOLS:
            match = next((entry.path for entry in context.workspace.files
                          if Path(entry.path).name.casefold().startswith(marker)), None)
            if match:
                _add(systems, tool, "javascript", match, Confidence.CONFIRMED,
                     [f"configuration file: {match}"])
        if isinstance(scripts.get("build"), str):
            _add(systems, "package.json build script", "javascript", package_json,
                 Confidence.CONFIRMED, [f"scripts.build: {scripts['build'][:120]}"])
        manager = next((profile.name for profile in context.dependencies.package_managers
                        if profile.ecosystem == "npm"), None)
        if manager and not any(item.category == "javascript" for item in systems):
            _add(systems, manager, "javascript", package_json, Confidence.LIKELY,
                 [f"package manager: {manager}"])

    # Native, JVM, .NET and other build systems.
    markers = (
        ("CMakeLists.txt", "CMake", "native"),
        ("Makefile", "Make", "native"),
        ("meson.build", "Meson", "native"),
        ("build.zig", "Zig", "native"),
        ("build.gradle", "Gradle", "jvm"),
        ("build.gradle.kts", "Gradle", "jvm"),
        ("settings.gradle", "Gradle", "jvm"),
        ("pom.xml", "Maven", "jvm"),
        ("build.sbt", "sbt", "jvm"),
        ("Cargo.toml", "Cargo", "rust"),
        ("go.mod", "Go toolchain", "go"),
        ("Package.swift", "Swift Package Manager", "swift"),
        ("pubspec.yaml", "Pub", "dart"),
        ("composer.json", "Composer", "php"),
        ("Gemfile", "Bundler", "ruby"),
        ("mix.exs", "Mix", "elixir"),
        ("stack.yaml", "Stack", "haskell"),
        ("cabal.project", "Cabal", "haskell"),
        ("foundry.toml", "Foundry", "solidity"),
    )
    for filename, name, category in markers:
        rel = names.get(filename.casefold())
        if rel:
            _add(systems, name, category, rel, Confidence.CONFIRMED, [f"build manifest: {rel}"])
    for entry in context.workspace.files:
        suffix = Path(entry.path).suffix.casefold()
        if suffix in {".sln", ".csproj", ".fsproj", ".vbproj"}:
            _add(systems, "MSBuild / .NET SDK", "dotnet", entry.path, Confidence.CONFIRMED,
                 [f"MSBuild project: {entry.path}"])
            break
    for entry in context.workspace.files:
        if Path(entry.path).name.casefold() in {"build", "workspace", "module.bazel"}:
            _add(systems, "Bazel", "bazel", entry.path, Confidence.CONFIRMED,
                 [f"Bazel workspace: {entry.path}"])
            break
    for entry in context.workspace.files:
        if Path(entry.path).suffix.casefold() == ".cabal":
            _add(systems, "Cabal", "haskell", entry.path, Confidence.CONFIRMED,
                 [f"Cabal package: {entry.path}"])
            break

    # CI build workflows.
    workflows = _ci_files(context)
    build_workflows: list[str] = []
    for rel in workflows[:80]:
        text = context.read_text(rel, max_bytes=512_000) or ""
        if BUILD_COMMAND.search(text):
            build_workflows.append(rel)
    for system in systems:
        system.ci_workflows = build_workflows[:20]
    context.metrics["ci_build_workflows"] = build_workflows

    systems.sort(key=lambda item: (item.category, item.name.casefold(), item.manifest))
    if systems:
        context.finding(
            "OGX-INTEL-BUILD", "Build systems: " + ", ".join(item.name for item in systems[:8]),
            "build", Confidence.CONFIRMED, "Build manifest and CI workflow inspection",
            evidence=[AnalysisEvidence(kind="build-manifest", location=item.manifest,
                                       detail=f"{item.name} ({item.confidence.value})")
                      for item in systems[:8]],
            limitations=["No build, wrapper or package manager was executed."],
        )
    else:
        context.add_limitation("build", "No supported build-system manifest was detected.")
    if build_workflows:
        context.add_limitation(
            "build", "CI files containing build commands were detected; their commands were not executed.")
    context.add_limitation(
        "build", "Toolchain availability and compilation requirements were not validated by a build.")
    return systems
