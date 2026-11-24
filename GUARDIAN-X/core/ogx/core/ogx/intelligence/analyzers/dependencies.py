"""Dependency intelligence: declarations only, never resolution or installation.

Parsing is delegated to the existing declaration-only parsers in
``ogx.engines.language`` so the intelligence layer does not invent a second
manifest dialect.  Scope enrichment is layered on top from the same manifest
sections; no lockfile is parsed for installed versions and no network is used.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from ...engines.language import (
    _parse_cargo,
    _parse_composer,
    _parse_cpanfile,
    _parse_csproj,
    _parse_gemfile,
    _parse_go_mod,
    _parse_gradle,
    _parse_julia_project,
    _parse_package_json,
    _parse_package_swift,
    _parse_pom,
    _parse_pubspec,
    _parse_pyproject,
    _parse_r_description,
    _parse_requirements,
    _parse_rockspec,
)
from ..models import (
    AnalysisEvidence, Confidence, DependencyDeclaration, DependencyProfile, ManifestAnalysis,
)
from ..options import AnalysisContext
from ..detectors.package_manager import detect_package_managers
from .common import is_vendored

LOCKFILE_NAMES = {
    "poetry.lock", "uv.lock", "pipfile.lock", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "bun.lock", "bun.lockb", "cargo.lock", "go.sum", "composer.lock",
    "gemfile.lock", "packages.lock.json", "package.resolved", "pubspec.lock",
    "gradle.lockfile", "mix.lock", "manifest.toml", "renv.lock", "stack.yaml.lock",
    "build.sbt.lock",
}

UNSUPPORTED_MANIFESTS = {
    "pipfile": "Pipfile is recognized but no declaration-only parser is registered.",
    "setup.py": "setup.py is not executed; install_requires was not evaluated.",
    "setup.cfg": "setup.cfg metadata was not parsed for dependency declarations.",
    "environment.yml": "Conda environment files were not parsed (no YAML dependency parser).",
    "environment.yaml": "Conda environment files were not parsed (no YAML dependency parser).",
    "mix.exs": "mix.exs is executable project code and was not evaluated.",
    "build.sbt": "build.sbt was not parsed for dependency declarations.",
    "packages.config": "packages.config was not parsed; use PackageReference for supported extraction.",
    "vcpkg.json": "vcpkg.json was not parsed for dependency declarations.",
    "conanfile.txt": "Conan declarations were not parsed.",
    "conanfile.py": "conanfile.py is project code and was not executed.",
}


def _manifest_parser(rel: str) -> tuple[str, Callable[[str, str], list]] | None:
    name = Path(rel).name
    lower = name.casefold()
    suffix = Path(rel).suffix.casefold()
    if lower.startswith("requirements") and suffix == ".txt":
        return "pip", _parse_requirements
    if lower == "package.json":
        return "npm", _parse_package_json
    if lower == "pyproject.toml":
        return "pip", _parse_pyproject
    if lower == "pom.xml":
        return "maven", _parse_pom
    if lower in {"build.gradle", "build.gradle.kts"}:
        return "gradle", _parse_gradle
    if lower == "cargo.toml":
        return "cargo", _parse_cargo
    if lower == "go.mod":
        return "go", _parse_go_mod
    if suffix in {".csproj", ".fsproj", ".vbproj"}:
        return "nuget", _parse_csproj
    if lower == "composer.json":
        return "composer", _parse_composer
    if lower == "gemfile":
        return "rubygems", _parse_gemfile
    if lower == "pubspec.yaml":
        return "pub", _parse_pubspec
    if lower == "package.swift":
        return "swiftpm", _parse_package_swift
    if lower == "project.toml":
        return "julia", _parse_julia_project
    if lower == "description":
        return "cran", _parse_r_description
    if lower == "cpanfile":
        return "cpan", _parse_cpanfile
    if suffix == ".rockspec":
        return "lua", _parse_rockspec
    return None


def _norm(value: str) -> str:
    return value.strip().casefold().replace("_", "-")


def _scope_map(context: AnalysisContext, rel: str) -> dict[str, tuple[str, str]]:
    name = Path(rel).name.casefold()
    text = context.read_text(rel, max_bytes=context.options.max_manifest_bytes)
    if text is None:
        return {}
    mapping: dict[str, tuple[str, str]] = {}

    def put(package: str, scope: str, group: str = "") -> None:
        mapping[_norm(package)] = (scope, group)

    try:
        if name == "package.json":
            data = json.loads(text)
            for section, scope in (("dependencies", "runtime"),
                                   ("devDependencies", "development"),
                                   ("optionalDependencies", "optional"),
                                   ("peerDependencies", "peer")):
                dependencies = data.get(section, {})
                if isinstance(dependencies, dict):
                    for package in dependencies:
                        put(str(package), scope)
        elif name == "pyproject.toml":
            data = tomllib.loads(text)
            project = data.get("project", {})
            if isinstance(project, dict):
                for item in project.get("dependencies", []) or []:
                    match = re.match(r"^([A-Za-z0-9_.-]+)", str(item))
                    if match:
                        put(match.group(1), "runtime")
                optional = project.get("optional-dependencies", {})
                if isinstance(optional, dict):
                    for group, items in optional.items():
                        for item in items or []:
                            match = re.match(r"^([A-Za-z0-9_.-]+)", str(item))
                            if match:
                                put(match.group(1), "optional", str(group))
            groups = data.get("dependency-groups", {})
            if isinstance(groups, dict):
                for group, items in groups.items():
                    for item in items or []:
                        if isinstance(item, str):
                            match = re.match(r"^([A-Za-z0-9_.-]+)", item)
                            if match:
                                put(match.group(1), "development", str(group))
            poetry = data.get("tool", {}).get("poetry", {})
            if isinstance(poetry, dict):
                for section, scope in (("dependencies", "runtime"),
                                       ("dev-dependencies", "development")):
                    dependencies = poetry.get(section, {})
                    if isinstance(dependencies, dict):
                        for package in dependencies:
                            if str(package).casefold() != "python":
                                put(str(package), scope)
                groups = poetry.get("group", {})
                if isinstance(groups, dict):
                    for group, payload in groups.items():
                        dependencies = payload.get("dependencies", {}) if isinstance(payload, dict) else {}
                        if isinstance(dependencies, dict):
                            for package in dependencies:
                                put(str(package), "development", str(group))
        elif name == "cargo.toml":
            data = tomllib.loads(text)
            for section, scope in (("dependencies", "runtime"),
                                   ("dev-dependencies", "development"),
                                   ("build-dependencies", "build")):
                dependencies = data.get(section, {})
                if isinstance(dependencies, dict):
                    for package in dependencies:
                        put(str(package), scope)
            targets = data.get("target", {})
            if isinstance(targets, dict):
                for target in targets.values():
                    dependencies = target.get("dependencies", {}) if isinstance(target, dict) else {}
                    if isinstance(dependencies, dict):
                        for package in dependencies:
                            put(str(package), "runtime")
        elif name == "composer.json":
            data = json.loads(text)
            for section, scope in (("require", "runtime"), ("require-dev", "development")):
                dependencies = data.get(section, {})
                if isinstance(dependencies, dict):
                    for package in dependencies:
                        put(str(package), scope)
        elif name == "pubspec.yaml":
            section = ""
            for raw in text.splitlines():
                if not raw.strip() or raw.lstrip().startswith("#"):
                    continue
                if not raw.startswith((" ", "\t")):
                    section = raw.split(":", 1)[0].strip().casefold()
                    continue
                if section in {"dependencies", "dev_dependencies"}:
                    package = raw.strip().split(":", 1)[0].strip()
                    if package:
                        put(package, "runtime" if section == "dependencies" else "development")
        elif name == "pom.xml":
            root = ET.fromstring(text)
            for dependency in root.iter():
                tag = dependency.tag.rsplit("}", 1)[-1]
                if tag != "dependency":
                    continue
                fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
                          for child in dependency}
                artifact = fields.get("artifactId", "")
                scope = fields.get("scope", "compile")
                mapped = {"test": "development", "provided": "optional",
                          "runtime": "runtime", "compile": "runtime"}.get(scope, "runtime")
                if artifact:
                    put(artifact, mapped)
        elif name in {"build.gradle", "build.gradle.kts"}:
            pattern = re.compile(
                r"(?m)^\s*(implementation|api|compileOnly|runtimeOnly|kapt|ksp|annotationProcessor|"
                r"testImplementation|androidTestImplementation|debugImplementation)\s*\(?\s*"
                r"['\"]([^:'\"]+):([^:'\"]+):([^'\"]+)['\"]")
            for match in pattern.finditer(text):
                configuration, group, artifact = match.group(1), match.group(2), match.group(3)
                scope = "development" if "test" in configuration.casefold() else "runtime"
                put(f"{group}:{artifact}", scope)
                put(artifact, scope)
        elif name == "gemfile":
            in_group = False
            for raw in text.splitlines():
                line = raw.strip()
                if re.match(r"^group\s+.*(?:development|test)", line):
                    in_group = True
                    continue
                if in_group and line == "end":
                    in_group = False
                    continue
                match = re.match(r"^gem\s+['\"]([^'\"]+)['\"]", line)
                if match:
                    put(match.group(1), "development" if in_group else "runtime")
        elif name in {"requirements.txt", "requirements-dev.txt"} or (
                name.startswith("requirements") and name.endswith(".txt")):
            scope = "development" if ("dev" in name or "test" in name) else "runtime"
            for line in text.splitlines():
                match = re.match(r"^([A-Za-z0-9_.-]+)", line.strip())
                if match:
                    put(match.group(1), scope)
        elif Path(rel).suffix.casefold() in {".csproj", ".fsproj", ".vbproj"}:
            scope = "development" if "test" in rel.casefold() else "runtime"
            root = ET.fromstring(text)
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] != "PackageReference":
                    continue
                package = element.attrib.get("Include") or element.attrib.get("Update")
                if package:
                    put(package, scope)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError,
            tomllib.TOMLDecodeError, ET.ParseError):
        return mapping
    return mapping


def _apply_scopes(context: AnalysisContext, rel: str,
                  components: list) -> list[DependencyDeclaration]:
    scopes = _scope_map(context, rel)
    declarations: list[DependencyDeclaration] = []
    for component in components:
        key = _norm(component.name)
        scope, group = scopes.get(key, (None, ""))
        if scope is None and ":" in key:
            scope, group = scopes.get(key.rsplit(":", 1)[-1], ("runtime", ""))
        if scope is None:
            scope = "runtime"
        declarations.append(DependencyDeclaration(
            ecosystem=component.ecosystem,
            name=component.name,
            constraint=component.constraint,
            scope=scope,
            source=rel,
            group=group,
        ))
    return declarations


def analyze_dependencies(context: AnalysisContext) -> DependencyProfile:
    profile = DependencyProfile()
    seen: set[tuple[str, str, str, str, str]] = set()

    for entry in context.workspace.files:
        if is_vendored(entry.path):
            continue
        name = Path(entry.path).name.casefold()
        if name in LOCKFILE_NAMES:
            profile.lockfiles.append(entry.path)
            continue
        parser = _manifest_parser(entry.path)
        if parser is None:
            if name in UNSUPPORTED_MANIFESTS:
                profile.manifests.append(ManifestAnalysis(
                    path=entry.path, ecosystem="unknown", status="unsupported",
                    error=UNSUPPORTED_MANIFESTS[name],
                ))
                profile.unsupported_manifests.append(entry.path)
            continue
        ecosystem, parse = parser
        if entry.size > context.options.max_manifest_bytes:
            profile.manifests.append(ManifestAnalysis(
                path=entry.path, ecosystem=ecosystem, status="skipped_oversized",
                error=f"Manifest exceeds {context.options.max_manifest_bytes} bytes.",
            ))
            context.warnings.append(f"Skipped oversized dependency manifest: {entry.path}")
            continue
        text = context.read_text(entry.path, max_bytes=context.options.max_manifest_bytes)
        if text is None:
            profile.manifests.append(ManifestAnalysis(
                path=entry.path, ecosystem=ecosystem, status="parse_error",
                error="Manifest could not be read within the configured limits.",
            ))
            continue
        try:
            components = parse(text, entry.path)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError,
                tomllib.TOMLDecodeError, ET.ParseError) as exc:
            profile.manifests.append(ManifestAnalysis(
                path=entry.path, ecosystem=ecosystem, status="parse_error",
                error=f"{type(exc).__name__}: manifest is malformed or unsupported.",
            ))
            context.warnings.append(f"Could not parse dependency manifest {entry.path}: {type(exc).__name__}")
            context.finding(
                "OGX-INTEL-DEP-PARSE", "Malformed or unsupported dependency manifest",
                "dependencies", Confidence.CONFIRMED,
                "Static manifest parse (project code not executed)",
                evidence=[AnalysisEvidence(kind="manifest", location=entry.path,
                                           detail=f"{ecosystem} manifest could not be parsed: "
                                                  f"{type(exc).__name__}")],
                limitations=[f"{entry.path}: {type(exc).__name__}"],
            )
            context.recommend("dependencies", f"Repair manifest: {entry.path}",
                              "A dependency manifest could not be parsed, so its declared dependencies are unknown.",
                              "medium")
            continue
        declarations = _apply_scopes(context, entry.path, components)
        added = 0
        for declaration in declarations:
            key = (declaration.ecosystem.casefold(), _norm(declaration.name),
                   declaration.constraint, declaration.source, declaration.scope)
            if key in seen:
                continue
            seen.add(key)
            if len(profile.declarations) >= context.options.max_dependencies:
                profile.truncated = True
                continue
            profile.declarations.append(declaration)
            added += 1
        profile.manifests.append(ManifestAnalysis(
            path=entry.path, ecosystem=ecosystem, status="parsed",
            declaration_count=added,
        ))

    profile.lockfiles = sorted(set(profile.lockfiles))
    profile.manifests.sort(key=lambda item: item.path)
    profile.declarations.sort(key=lambda item: (item.ecosystem, item.scope, item.name.casefold(), item.source))
    profile.package_managers = detect_package_managers(context)

    if profile.truncated:
        context.warnings.append(
            f"Dependency declaration limit reached ({context.options.max_dependencies}); the inventory is partial.")
    profile.limitations = [
        "Only declared dependency constraints are reported; installed and resolved versions are unknown.",
        "No transitive dependency graph is produced and no lockfile is resolved.",
        "No package is downloaded, installed or executed; no vulnerability database is queried.",
    ]
    if profile.unsupported_manifests:
        profile.limitations.append(
            "Unsupported manifests were reported without parsing: "
            + ", ".join(profile.unsupported_manifests[:10]))
    for limitation in profile.limitations:
        context.add_limitation("dependencies", limitation)
    return profile
