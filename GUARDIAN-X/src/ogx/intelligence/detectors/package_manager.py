"""Package-manager detection from manifests and lockfiles only."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..models import Confidence, PackageManagerProfile
from ..options import AnalysisContext


@dataclass(frozen=True, slots=True)
class ManagerRule:
    name: str
    ecosystem: str
    manifests: tuple[str, ...]
    lockfiles: tuple[str, ...] = ()
    glob_manifests: tuple[str, ...] = ()
    confidence: Confidence = Confidence.CONFIRMED
    notes: tuple[str, ...] = ()


RULES: tuple[ManagerRule, ...] = (
    ManagerRule("Poetry", "pip", ("pyproject.toml",), ("poetry.lock",)),
    ManagerRule("uv", "pip", ("pyproject.toml",), ("uv.lock",)),
    ManagerRule("Pipenv", "pip", ("Pipfile",), ("Pipfile.lock",)),
    ManagerRule("Conda", "conda", ("environment.yml", "environment.yaml")),
    ManagerRule("pnpm", "npm", ("package.json", "pnpm-workspace.yaml"), ("pnpm-lock.yaml",)),
    ManagerRule("Yarn", "npm", ("package.json",), ("yarn.lock",)),
    ManagerRule("Bun", "npm", ("package.json",), ("bun.lock", "bun.lockb")),
    ManagerRule("npm", "npm", ("package.json",), ("package-lock.json",)),
    ManagerRule("Cargo", "cargo", ("Cargo.toml",), ("Cargo.lock",)),
    ManagerRule("Go Modules", "go", ("go.mod",), ("go.sum",)),
    ManagerRule("Maven", "maven", ("pom.xml",)),
    ManagerRule("Gradle", "gradle", ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
                ("gradle.lockfile",)),
    ManagerRule("Composer", "composer", ("composer.json",), ("composer.lock",)),
    ManagerRule("Bundler", "rubygems", ("Gemfile",), ("Gemfile.lock",)),
    ManagerRule("Swift Package Manager", "swiftpm", ("Package.swift",), ("Package.resolved",)),
    ManagerRule("Pub", "pub", ("pubspec.yaml",), ("pubspec.lock",)),
    ManagerRule("NuGet", "nuget", ("packages.config", "Directory.Build.props"),
                ("packages.lock.json",), ("*.csproj", "*.sln")),
    ManagerRule("Mix", "hex", ("mix.exs",), ("mix.lock",)),
    ManagerRule("sbt", "maven", ("build.sbt",), ("build.sbt.lock",)),
    ManagerRule("Stack", "hackage", ("stack.yaml",), ("stack.yaml.lock",)),
    ManagerRule("Cabal", "hackage", ("cabal.project",), (), ("*.cabal",)),
    ManagerRule("Julia Pkg", "julia", ("Project.toml",), ("Manifest.toml",)),
    ManagerRule("renv", "cran", ("DESCRIPTION",), ("renv.lock",)),
    ManagerRule("CPAN", "cpan", ("cpanfile",)),
    ManagerRule("LuaRocks", "lua", (), (), ("*.rockspec",)),
)


def _name_index(context: AnalysisContext) -> tuple[dict[str, str], dict[str, list[str]]]:
    exact: dict[str, str] = {}
    by_suffix: dict[str, list[str]] = {}
    for entry in context.workspace.files:
        name = Path(entry.path).name
        exact.setdefault(name.casefold(), entry.path)
        suffix = Path(entry.path).suffix.casefold()
        if suffix:
            by_suffix.setdefault(suffix, []).append(entry.path)
    return exact, by_suffix


def _python_backend(context: AnalysisContext, rel: str = "pyproject.toml") -> str:
    entry = context.workspace.entry(rel)
    if entry is None:
        return ""
    text = context.read_text(rel, max_bytes=512_000)
    if not text:
        return ""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ""
    build = data.get("build-system", {})
    requires = build.get("requires", []) if isinstance(build, dict) else []
    return " ".join(str(item).casefold() for item in requires) if isinstance(requires, list) else ""


def _python_declares_poetry_or_uv(context: AnalysisContext) -> tuple[bool, bool]:
    exact = {Path(entry.path).name.casefold(): entry.path for entry in context.workspace.files}
    rel = exact.get("pyproject.toml")
    entry = context.workspace.entry(rel) if rel else None
    if entry is None:
        return False, False
    text = context.read_text(rel, max_bytes=512_000)
    if not text:
        return False, False
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False, False
    tool = data.get("tool", {})
    poetry = isinstance(tool, dict) and isinstance(tool.get("poetry"), dict)
    uv = isinstance(tool, dict) and isinstance(tool.get("uv"), dict)
    backend = _python_backend(context, rel)
    return poetry or "poetry" in backend, uv or "uv" in backend


def _requirements(context: AnalysisContext) -> list[str]:
    return sorted(entry.path for entry in context.workspace.files
                  if Path(entry.path).name.casefold().startswith("requirements")
                  and Path(entry.path).suffix.casefold() == ".txt")


def _node_declared_manager(context: AnalysisContext) -> str:
    entry = context.workspace.entry("package.json")
    if entry is None:
        return ""
    text = context.read_text("package.json", max_bytes=512_000)
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    value = data.get("packageManager") if isinstance(data, dict) else None
    if not isinstance(value, str):
        return ""
    return value.split("@", 1)[0].strip().casefold()


def detect_package_managers(context: AnalysisContext) -> list[PackageManagerProfile]:
    exact, by_suffix = _name_index(context)
    pyproject_poetry, pyproject_uv = _python_declares_poetry_or_uv(context)
    python_backend = _python_backend(
        context, exact.get("pyproject.toml", "pyproject.toml"))
    node_manager = _node_declared_manager(context)
    profiles: list[PackageManagerProfile] = []
    seen: set[str] = set()

    def add(rule: ManagerRule, manifest: str, *, confidence: Confidence | None = None,
            lockfile: str | None = None, evidence_extra: list[str] | None = None) -> None:
        if rule.name in seen:
            return
        seen.add(rule.name)
        evidence = [f"manifest: {manifest}"]
        if lockfile:
            evidence.append(f"lockfile: {lockfile}")
        if evidence_extra:
            evidence.extend(evidence_extra)
        profiles.append(PackageManagerProfile(
            name=rule.name, ecosystem=rule.ecosystem, manifest=manifest,
            lockfile=lockfile, confidence=confidence or rule.confidence,
            evidence=evidence,
        ))

    skip_pip = pyproject_poetry or pyproject_uv
    for rule in RULES:
        if rule.name == "Poetry" and not pyproject_poetry:
            lock = exact.get("poetry.lock")
            if not lock:
                continue
        if rule.name == "uv" and not pyproject_uv:
            lock = exact.get("uv.lock")
            if not lock:
                continue
        node_lock = next((exact[name.casefold()] for name in rule.lockfiles
                          if name.casefold() in exact), None)
        if rule.name in {"pnpm", "Yarn", "Bun"}:
            declared = node_manager == rule.name.casefold()
            dedicated = rule.name == "pnpm" and exact.get("pnpm-workspace.yaml")
            if not node_lock and not declared and not dedicated:
                continue
        manifest: str | None = None
        for name in rule.manifests:
            if rule.name in {"Poetry", "uv"}:
                manifest = exact.get("pyproject.toml")
                if manifest:
                    break
                continue
            manifest = exact.get(name.casefold())
            if manifest:
                break
        if manifest is None and rule.glob_manifests:
            for pattern in rule.glob_manifests:
                suffix = pattern.lstrip("*")
                candidates = sorted(by_suffix.get(suffix.casefold(), []))
                if candidates:
                    manifest = candidates[0]
                    break
        if manifest is None:
            continue
        lockfile = node_lock if rule.name in {"pnpm", "Yarn", "Bun"} else next(
            (exact[name.casefold()] for name in rule.lockfiles
             if name.casefold() in exact), None)
        confidence = (Confidence.CONFIRMED if lockfile else Confidence.LIKELY
                      if rule.name in {"pnpm", "Yarn", "Bun"} else None)
        add(rule, manifest, lockfile=lockfile, confidence=confidence)

    # PEP 621 / setup.py projects without Poetry or uv are pip-compatible.
    if "pip" not in {profile.ecosystem for profile in profiles} and not skip_pip:
        manifest = None
        if exact.get("pyproject.toml"):
            manifest = exact["pyproject.toml"]
        elif exact.get("setup.py"):
            manifest = exact["setup.py"]
        elif exact.get("setup.cfg"):
            manifest = exact["setup.cfg"]
        requirements = _requirements(context)
        if manifest is None and requirements:
            manifest = requirements[0]
        if manifest:
            evidence_extra = []
            if requirements and manifest != requirements[0]:
                evidence_extra.append(f"requirements: {requirements[0]}")
            if python_backend:
                evidence_extra.append(f"build backend: {python_backend[:120]}")
            add(ManagerRule("pip", "pip", (manifest,), confidence=Confidence.LIKELY),
                manifest, confidence=Confidence.LIKELY, evidence_extra=evidence_extra)
    elif requirements := _requirements(context):
        if "pip" not in {profile.name for profile in profiles}:
            add(ManagerRule("pip", "pip", (requirements[0],), confidence=Confidence.LIKELY),
                requirements[0], confidence=Confidence.LIKELY,
                evidence_extra=[f"requirements file: {requirements[0]}"])

    # package.json is evidence of npm-compatible tooling even without a lockfile.
    package_json = exact.get("package.json")
    if package_json and not any(profile.ecosystem == "npm" for profile in profiles):
        add(ManagerRule("npm", "npm", ("package.json",), confidence=Confidence.LIKELY),
            package_json, confidence=Confidence.LIKELY)

    profiles.sort(key=lambda item: (item.ecosystem, item.name.casefold()))
    if not profiles:
        context.add_limitation("dependencies",
                               "No supported package manager manifest was detected.")
    return profiles
