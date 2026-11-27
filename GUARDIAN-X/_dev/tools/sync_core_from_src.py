#!/usr/bin/env python3
"""Version synchronization and distribution consistency for the two OGX trees.

Canonical installable source: ``GUARDIAN-X/src/ogx`` (packaged by
``pyproject.toml`` into the ``omega-guardian-x`` wheel).
Drop-in distribution: ``GUARDIAN-X/core/ogx`` (loaded by ``init.py`` next to
``core/`` in a host project).

Why this tool no longer generates ``core/ogx``
----------------------------------------------
The trees are deliberately different: ``src`` keeps flat feature modules plus
the provider/platform packages, while ``core`` groups runtime modules into
``kernel``/``shield``/``analysis``/``cli`` and does not ship the provider
engine. A file-by-file copy is therefore not a safe transform. The previous
version of this file mapped ``platform``/``hosts``/... to
``core/ogx/core/ogx/...`` and then deleted the top-level directories, which
produced a 125-file nested duplicate tree and removed required modules.

This tool never copies, rewrites or deletes feature code. It enforces the
invariants that make drift visible:

* ``VERSION`` is the single version source, mirrored by
  ``src/ogx/__init__.py`` and the drop-in fallback constant;
* both trees exist with exactly the declared package/module layout;
* no nested ``core/ogx/core`` tree and no module/package name collisions;
* every Python file parses;
* ``pyproject.toml`` still packages ``src`` and declares no runtime dependency.

Usage
-----
    python _dev/tools/sync_core_from_src.py --check
    python _dev/tools/sync_core_from_src.py --dry-run --write-version --version 0.8.0a1
    python _dev/tools/sync_core_from_src.py --write-version --version 0.8.0a1

Exit codes: 0 consistent (or changes written), 1 drift, 2 invalid arguments.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ogx"
CORE = ROOT / "core" / "ogx"
VERSION_FILE = ROOT / "VERSION"
PYPROJECT = ROOT / "pyproject.toml"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[a-z]+\d+)?$")

CORE_REQUIRED_PACKAGES = frozenset(
    {
        "adapters",
        "analysis",
        "cli",
        "connectors",
        "devkit",
        "ecosystem",
        "governance",
        "kernel",
        "runtime",
        "shield",
    }
)
CORE_REQUIRED_FILES = frozenset(
    {"__init__.py", "__main__.py", "envfile.py", "licenses.py", "models.py", "sbom.py"}
)
SRC_REQUIRED_PACKAGES = frozenset(
    {
        "adapters",
        "agents",
        "ai",
        "connectors",
        "engines",
        "enterprise",
        "hosts",
        "integration",
        "intelligence",
        "pap",
        "platform",
        "plugins",
        "registry",
        "skillpacks",
        "skills",
    }
)
SRC_REQUIRED_FILES = frozenset(
    {"__init__.py", "__main__.py", "cli.py", "config.py", "detector.py", "guardian.py", "models.py", "scanner.py"}
)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _packages(root: Path) -> set[str]:
    return {path.name for path in root.iterdir() if path.is_dir() and (path / "__init__.py").is_file()}


def _literal(text: str, name: str) -> str | None:
    match = re.search(rf'{name}\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


def current_versions() -> dict[str, str | None]:
    return {
        "VERSION": VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.is_file() else None,
        "src/ogx/__init__.py": _literal(
            (SRC / "__init__.py").read_text(encoding="utf-8"), "__version__"
        )
        if (SRC / "__init__.py").is_file()
        else None,
        "core/ogx/__init__.py": _literal(
            (CORE / "__init__.py").read_text(encoding="utf-8"), "_FALLBACK_VERSION"
        )
        if (CORE / "__init__.py").is_file()
        else None,
    }


def _replace_literal(path: Path, name: str, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        rf'({name}\s*=\s*)["\'][^"\']+["\']',
        rf'\g<1>"{version}"',
        text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"cannot find {name} in {path}")
    path.write_text(updated, encoding="utf-8")


def write_version(version: str) -> list[Path]:
    """Write the version to the three declared locations; returns changed paths."""
    changed: list[Path] = []
    if VERSION_FILE.read_text(encoding="utf-8").strip() != version:
        VERSION_FILE.write_text(version + "\n", encoding="utf-8")
        changed.append(VERSION_FILE)
    for path, name in (
        (SRC / "__init__.py", "__version__"),
        (CORE / "__init__.py", "_FALLBACK_VERSION"),
    ):
        if _literal(path.read_text(encoding="utf-8"), name) != version:
            _replace_literal(path, name, version)
            changed.append(path)
    return changed


def planned_version_writes(version: str) -> list[str]:
    planned = []
    for label, value in current_versions().items():
        if value != version:
            planned.append(f"{label}: {value!r} -> {version!r}")
    return planned


def _tree_issues(name: str, root: Path, packages: frozenset[str], files: frozenset[str]) -> list[str]:
    issues: list[str] = []
    if not root.is_dir():
        return [f"{name}: missing tree {root}"]
    python_files = _python_files(root)
    if len(python_files) < 20:
        issues.append(f"{name}: only {len(python_files)} Python files found; tree looks empty")
    found = _packages(root)
    for missing in sorted(packages - found):
        issues.append(f"{name}: missing package {missing}/")
    for unexpected in sorted(found - packages):
        issues.append(f"{name}: undeclared package {unexpected}/ (update the declared layout deliberately)")
    for missing in sorted(files - {path.name for path in root.glob('*.py')}):
        issues.append(f"{name}: missing root module {missing}")
    if (root / "core").exists():
        issues.append(f"{name}: nested 'core' directory exists (old unsafe generator output)")
    for path in python_files:
        rel = path.relative_to(root)
        if len(rel.parts) > 1 and rel.parts[0] not in found:
            issues.append(f"{name}: {rel} is outside a declared package")
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            issues.append(f"{name}: cannot parse {rel}: {exc}")
    collisions = {
        path.stem for path in root.glob("*.py") if path.name != "__init__.py"
    } & found
    for collision in sorted(collisions):
        issues.append(f"{name}: module and package are both named {collision!r}")
    return issues


def _pyproject_issues() -> list[str]:
    issues: list[str] = []
    try:
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"pyproject.toml cannot be read: {exc}"]
    try:
        if data["tool"]["setuptools"]["package-dir"][""] != "src":
            issues.append("pyproject.toml no longer packages from src")
        if data["tool"]["setuptools"]["packages"]["find"]["where"] != ["src"]:
            issues.append("pyproject.toml package discovery no longer searches src")
        if data["project"]["scripts"]["ogx"] != "ogx.cli:main":
            issues.append("pyproject.toml ogx entry point changed")
        if data["project"].get("dependencies", []) != []:
            issues.append("pyproject.toml declares runtime dependencies; the core promises stdlib-only")
    except (KeyError, TypeError) as exc:
        issues.append(f"pyproject.toml is missing a required declaration: {exc}")
    return issues


def check_distribution(*, expected_version: str | None = None) -> list[str]:
    """Return every consistency issue; never writes to the tree."""
    issues = _tree_issues("src", SRC, SRC_REQUIRED_PACKAGES, SRC_REQUIRED_FILES)
    issues += _tree_issues("core", CORE, CORE_REQUIRED_PACKAGES, CORE_REQUIRED_FILES)
    issues += _pyproject_issues()

    versions = current_versions()
    version = expected_version or versions["VERSION"]
    if not version or not VERSION_RE.fullmatch(version):
        issues.append(f"VERSION does not contain a valid release version: {version!r}")
    else:
        for label, value in versions.items():
            if value != version:
                issues.append(f"{label} reports {value!r} but VERSION is {version!r}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify (and optionally synchronize) the OGX source and drop-in distribution.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify only (default); never writes",
    )
    parser.add_argument(
        "--write-version",
        action="store_true",
        help="write the target version to VERSION, src/ogx/__init__.py and core/ogx/__init__.py",
    )
    parser.add_argument("--version", help="target version (required with --write-version)")
    parser.add_argument("--dry-run", action="store_true", help="print planned version writes; change nothing")
    args = parser.parse_args(argv)

    if args.version is not None and not args.write_version:
        parser.error("--version requires --write-version")
    if args.write_version and not args.version:
        parser.error("--write-version requires an explicit --version")

    target = args.version
    if target is not None and not VERSION_RE.fullmatch(target):
        parser.error(f"invalid version {target!r}; expected e.g. 0.8.0a1")

    print("OGX distribution check")
    print(f"  canonical source : {SRC}")
    print(f"  drop-in          : {CORE}")
    print(f"  versions         : {current_versions()}")

    if args.write_version:
        planned = planned_version_writes(target)
        if not planned:
            print("Version already synchronized.")
        elif args.dry_run:
            print("Dry run - would write:")
            for line in planned:
                print(f"  - {line}")
        else:
            for path in write_version(target):
                print(f"Updated {path.relative_to(ROOT)}")
        if args.dry_run:
            # A dry run reports structural health but never claims the write happened.
            issues = check_distribution(expected_version=target)
            blocking = [issue for issue in issues if "VERSION" not in issue and "src/ogx/__init__.py" not in issue and "core/ogx/__init__.py" not in issue]
            for issue in blocking:
                print(f"  ERROR: {issue}")
            return 1 if blocking else 0

    issues = check_distribution()
    if issues:
        print("\nInconsistencies found:")
        for issue in issues:
            print(f"  ERROR: {issue}")
        return 1
    print("\nOK: source and drop-in structure, versions and packaging declarations agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
