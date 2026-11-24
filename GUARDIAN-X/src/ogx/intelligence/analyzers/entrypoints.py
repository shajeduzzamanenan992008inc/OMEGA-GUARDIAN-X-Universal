"""Static entry-point candidates. Project code is read, never imported or run."""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import AnalysisEvidence, Confidence, EntryPoint
from ..options import AnalysisContext
from .common import is_vendored, path_parts

PYTHON_NAMES = {"__main__.py", "main.py", "app.py", "cli.py", "manage.py",
                "wsgi.py", "asgi.py", "run.py", "server.py"}
MAX_SCANNED = 240


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(element, name: str) -> str:
    return next((value for key, value in element.attrib.items()
                 if _local(key) == name), "")


def analyze_entry_points(context: AnalysisContext) -> list[EntryPoint]:
    found: dict[tuple[str, str], EntryPoint] = {}

    def add(path: str, kind: str, evidence: str, confidence: Confidence) -> None:
        key = (path, kind)
        existing = found.get(key)
        if existing is None:
            found[key] = EntryPoint(path=path, kind=kind, evidence=evidence, confidence=confidence)
        elif confidence is Confidence.CONFIRMED:
            found[key] = EntryPoint(path=path, kind=kind, evidence=evidence, confidence=confidence)

    names = {Path(entry.path).name.casefold(): entry.path for entry in context.workspace.files}

    # Declared Python entry points (PEP 621 / Poetry).
    pyproject = names.get("pyproject.toml")
    if pyproject:
        text = context.read_text(pyproject, max_bytes=512_000)
        if text:
            try:
                data = tomllib.loads(text)
                project = data.get("project", {})
                if isinstance(project, dict):
                    for section in ("scripts", "gui-scripts"):
                        scripts = project.get(section, {})
                        if isinstance(scripts, dict):
                            for name, target in scripts.items():
                                add(pyproject, f"python-{section}", f"{name} = {target}", Confidence.CONFIRMED)
                poetry = data.get("tool", {}).get("poetry", {})
                if isinstance(poetry, dict) and isinstance(poetry.get("scripts"), dict):
                    for name, target in poetry["scripts"].items():
                        add(pyproject, "python-poetry-script", f"{name} = {target}", Confidence.CONFIRMED)
            except tomllib.TOMLDecodeError:
                context.warnings.append("Could not parse pyproject.toml entry points.")
    setup_py = names.get("setup.py")
    if setup_py:
        text = context.read_text(setup_py, max_bytes=512_000)
        if text and "entry_points" in text and "console_scripts" in text:
            add(setup_py, "python-console-script", "setup.py declares console_scripts", Confidence.LIKELY)

    # Python file conventions and main guards.
    scanned = 0
    for entry in context.workspace.files:
        if scanned >= MAX_SCANNED:
            break
        if is_vendored(entry.path) or Path(entry.path).suffix.casefold() != ".py":
            continue
        scanned += 1
        name = Path(entry.path).name.casefold()
        if name in PYTHON_NAMES:
            text = context.read_text(entry.path, max_bytes=256_000) or ""
            confirmed = ("if __name__" in text or "def main(" in text or
                         "click.command" in text or "typer.Typer" in text)
            add(entry.path, "python-module-entry" if name == "__main__.py" else "python-candidate",
                f"conventional Python entry filename: {name}",
                Confidence.CONFIRMED if confirmed else Confidence.LIKELY)
        elif entry.path.count("/") <= 3:
            text = context.read_text(entry.path, max_bytes=128_000) or ""
            if re.search(r"(?m)^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", text):
                add(entry.path, "python-main-guard", "module-level __main__ guard", Confidence.CONFIRMED)

    # Node / Electron entry points.
    package_json = names.get("package.json")
    if package_json:
        text = context.read_text(package_json, max_bytes=512_000)
        if text:
            try:
                data = json.loads(text)
                for field in ("main", "module"):
                    value = data.get(field)
                    if isinstance(value, str) and value:
                        add(package_json, f"javascript-{field}", f"package.json {field}: {value}", Confidence.CONFIRMED)
                binary = data.get("bin")
                if isinstance(binary, str):
                    add(package_json, "javascript-bin", f"package.json bin: {binary}", Confidence.CONFIRMED)
                elif isinstance(binary, dict):
                    for name, target in list(binary.items())[:10]:
                        add(package_json, "javascript-bin", f"package.json bin {name}: {target}", Confidence.CONFIRMED)
                scripts = data.get("scripts", {})
                if isinstance(scripts, dict):
                    for script in ("start", "dev", "serve"):
                        if isinstance(scripts.get(script), str):
                            add(package_json, "javascript-script", f"package.json scripts.{script}", Confidence.LIKELY)
                if "electron" in data or "build" in data and any(
                        Path(entry.path).name.casefold().startswith("electron") for entry in context.workspace.files):
                    add(package_json, "electron-main", "Electron package configuration", Confidence.LIKELY)
            except json.JSONDecodeError:
                context.warnings.append("Could not parse package.json entry points.")

    # Rust binary targets.
    cargo = names.get("cargo.toml")
    if cargo:
        text = context.read_text(cargo, max_bytes=512_000)
        if text:
            try:
                data = tomllib.loads(text)
                binaries = data.get("bin", [])
                if isinstance(binaries, list):
                    for item in binaries[:20]:
                        if isinstance(item, dict) and item.get("name"):
                            add(cargo, "rust-bin", f"Cargo [[bin]]: {item.get('name')}", Confidence.CONFIRMED)
                if context.workspace.exists("src/main.rs"):
                    add("src/main.rs", "rust-main", "Cargo package convention", Confidence.CONFIRMED)
                if context.workspace.exists("src/lib.rs"):
                    add("src/lib.rs", "rust-library", "Cargo library target", Confidence.LIKELY)
            except tomllib.TOMLDecodeError:
                context.warnings.append("Could not parse Cargo.toml entry points.")

    # Go, JVM, C/C++, C#, Swift, Dart source signatures.
    go_scanned = jvm_scanned = c_scanned = 0
    for entry in context.workspace.files:
        if is_vendored(entry.path):
            continue
        suffix = Path(entry.path).suffix.casefold()
        if suffix == ".go" and go_scanned < MAX_SCANNED:
            go_scanned += 1
            text = context.read_text(entry.path, max_bytes=256_000) or ""
            if re.search(r"(?m)^package\s+main\b", text):
                add(entry.path, "go-main", "package main declaration", Confidence.CONFIRMED)
        elif suffix in {".java", ".kt", ".kts"} and jvm_scanned < MAX_SCANNED:
            jvm_scanned += 1
            text = context.read_text(entry.path, max_bytes=256_000) or ""
            if re.search(r"public\s+static\s+void\s+main\s*\(", text):
                add(entry.path, "jvm-main", "public static void main(String[]) signature", Confidence.CONFIRMED)
            elif re.search(r"(?m)^\s*fun\s+main\s*\(", text):
                add(entry.path, "kotlin-main", "fun main() signature", Confidence.CONFIRMED)
        elif suffix in {".c", ".cc", ".cpp", ".cxx"} and c_scanned < MAX_SCANNED:
            c_scanned += 1
            text = context.read_text(entry.path, max_bytes=256_000) or ""
            if re.search(r"(?m)^\s*(?:int|void)\s+main\s*\(", text):
                add(entry.path, "native-main", "main() definition", Confidence.CONFIRMED)
        elif suffix == ".cs":
            text = context.read_text(entry.path, max_bytes=256_000) or ""
            if re.search(r"static\s+(?:async\s+)?(?:void|int|Task)\s+Main\s*\(", text):
                add(entry.path, "dotnet-main", "static Main() signature", Confidence.CONFIRMED)

    if context.workspace.exists("main.swift"):
        add("main.swift", "swift-main", "Swift main.swift convention", Confidence.CONFIRMED)
    for entry in context.workspace.files:
        if Path(entry.path).suffix.casefold() == ".swift" and not is_vendored(entry.path):
            text = context.read_text(entry.path, max_bytes=128_000) or ""
            if re.search(r"(?m)^\s*@main\b", text):
                add(entry.path, "swift-main", "@main declaration", Confidence.CONFIRMED)
    if context.workspace.exists("lib/main.dart"):
        add("lib/main.dart", "dart-main", "Dart lib/main.dart convention", Confidence.CONFIRMED)
    elif names.get("main.dart"):
        add(names["main.dart"], "dart-main", "Dart main.dart convention", Confidence.CONFIRMED)
    html = names.get("index.html")
    if html:
        add(html, "web-document", "index.html document entry", Confidence.LIKELY)

    # Android launcher activity.
    android_manifest = names.get("androidmanifest.xml")
    if android_manifest:
        try:
            root = context.workspace.xml(android_manifest)
        except (ValueError, ET.ParseError):
            root = None
        if root is not None:
            for activity in root.iter():
                if _local(activity.tag) not in {"activity", "activity-alias"}:
                    continue
                name = _attr(activity, "name")
                actions = {_attr(element, "name").rsplit(".", 1)[-1]
                           for element in activity.iter() if _local(element.tag) == "action"}
                categories = {_attr(element, "name").rsplit(".", 1)[-1]
                              for element in activity.iter() if _local(element.tag) == "category"}
                if "MAIN" in actions and "LAUNCHER" in categories and name:
                    add(android_manifest, "android-launcher-activity", f"launcher activity: {name}",
                        Confidence.CONFIRMED)

    points = sorted(found.values(), key=lambda item: (item.kind, item.path))[:100]
    if not points:
        context.add_limitation("entrypoints", "No conventional entry point was detected.")
    else:
        context.finding(
            "OGX-INTEL-ENTRYPOINT", f"Entry-point candidates: {len(points)}", "entrypoints",
            Confidence.CONFIRMED if any(item.confidence is Confidence.CONFIRMED for item in points) else Confidence.LIKELY,
            "Static convention, manifest and signature inspection",
            evidence=[AnalysisEvidence(kind="entry-point", location=item.path,
                                       detail=f"{item.kind}: {item.evidence}")
                      for item in points[:12]],
            limitations=["Entry points were not executed or import-checked."],
        )
    context.add_limitation(
        "entrypoints",
        "Entry-point discovery is convention and signature based; dynamic dispatch and framework routing are not evaluated.",
    )
    return points
