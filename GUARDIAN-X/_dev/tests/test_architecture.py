"""Enforce the source/distribution architecture and the stdlib-only promise.

Two Python trees exist on purpose:

* ``src/ogx`` is the canonical installable package (``pyproject.toml`` packages
  from ``src`` and exposes the ``ogx`` console script).
* ``core/ogx`` is the drop-in distribution loaded by ``init.py``. It uses a
  different internal layout (``kernel``/``shield``/``analysis``/``cli``) and
  does not ship the provider engine.

Both trees are validated here. These checks fail when a tree is missing or
suspiciously empty, so a zero-file scan can never be reported as success.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ogx"
CORE = ROOT / "core" / "ogx"

# package -> packages it may import (besides itself). "cli" is the composition root.
CORE_ALLOWED = {
    "adapters": set(),
    "kernel": set(),
    "connectors": {"kernel"},
    "analysis": {"kernel"},
    "shield": {"kernel", "analysis", "connectors"},
    "devkit": {"kernel", "analysis", "shield"},
    "governance": {"kernel", "connectors", "shield"},
    "ecosystem": {"kernel", "analysis"},
    "runtime": {"kernel"},
    "cli": {"kernel", "connectors", "analysis", "shield", "devkit", "governance", "ecosystem", "runtime"},
}

# The canonical source uses flat feature modules plus these packages. Edges are
# the declared contract, not a snapshot of whatever currently imports what.
SRC_ALLOWED = {
    "adapters": set(),
    # api: authenticated REST + dashboard assets. It is a leaf: it calls into
    # enterprise policy/store, host registry and plugin metadata, never back.
    "api": {"enterprise", "hosts", "plugins"},
    "agents": set(),
    # ai: provider-independent LLM boundary, patch generation, review, evolution.
    # It may use the platform transport/redaction primitives but no provider engine.
    "ai": {"platform"},
    "connectors": set(),
    "engines": set(),
    "enterprise": {"connectors"},
    "hosts": {"connectors", "pap", "platform", "registry"},
    "integration": {"platform"},
    "intelligence": {"engines", "pap", "registry"},
    "pap": {"registry"},
    "platform": {"connectors", "registry"},
    # plugins: manifests, trust, permissions, store and isolation backends.
    # Execution goes through ogx.sandbox (module), never a provider package.
    "plugins": set(),
    # registry -> pap is a deliberate function-local (lazy) import in
    # registry/v2.py; it is declared here but does not create an import-time cycle.
    "registry": {"connectors", "pap"},
    "skillpacks": set(),
    "skills": set(),
}

CORE_REQUIRED_FILES = {
    "__init__.py",
    "__main__.py",
    "envfile.py",
    "licenses.py",
    "models.py",
    "sbom.py",
}

SRC_REQUIRED_FILES = {
    "__init__.py",
    "__main__.py",
    "cli.py",
    "config.py",
    "detector.py",
    "guardian.py",
    "models.py",
    "scanner.py",
}


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node.level, node.module or "", [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name, []


def _toplevel_imports(path: Path):
    """Imports evaluated while the module is loading (not function-local lazies)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            yield node.level, node.module or "", [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name, []


def _packages(root: Path) -> set[str]:
    return {
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }


def _resolve_target(path: Path, root: Path, level: int, module: str) -> str | None:
    """Resolve an import to a top-level package name in ``root``."""
    if level:
        container = list(path.relative_to(root).parts[:-1])
        drop = level - 1
        if drop > len(container):
            return None
        base = container[: len(container) - drop]
        combined = base + ([part for part in module.split(".") if part] if module else [])
        candidate = combined[0] if combined else None
    else:
        parts = module.split(".")
        candidate = parts[1] if len(parts) > 1 and parts[0] == "ogx" else None
    if candidate in _packages(root):
        return candidate
    return None


def _version_literal(text: str, name: str) -> str | None:
    match = re.search(rf'{name}\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


class ArchitectureTests(unittest.TestCase):
    # ---------------------------------------------------------------- presence
    def test_canonical_tree_is_present_and_non_empty(self):
        self.assertTrue(SRC.is_dir(), f"missing canonical source tree: {SRC}")
        files = _python_files(SRC)
        self.assertGreaterEqual(len(files), 100, "canonical source tree is suspiciously small")
        self.assertEqual(_packages(SRC), set(SRC_ALLOWED))
        self.assertTrue((SRC / "__init__.py").is_file())

    def test_dropin_tree_is_present_and_non_empty(self):
        self.assertTrue(CORE.is_dir(), f"missing drop-in tree: {CORE}")
        files = _python_files(CORE)
        self.assertGreaterEqual(len(files), 60, "drop-in tree is suspiciously small")
        self.assertEqual(_packages(CORE), set(CORE_ALLOWED))
        self.assertTrue((CORE / "__init__.py").is_file())

    def test_no_nested_distribution_tree(self):
        self.assertFalse((CORE / "core").exists(), "core/ogx/core nested duplicate tree is back")
        offenders = [
            path for path in _python_files(CORE)
            if path.relative_to(CORE).parts[0] == "core"
        ]
        self.assertEqual(offenders, [])

    def test_every_python_file_belongs_to_a_declared_package(self):
        for name, root, allowed in (("src", SRC, SRC_ALLOWED), ("core", CORE, CORE_ALLOWED)):
            offenders = []
            for path in _python_files(root):
                parts = path.relative_to(root).parts
                if len(parts) > 1 and parts[0] not in allowed:
                    offenders.append(f"{name}:{path.relative_to(root)}")
            self.assertEqual(offenders, [], f"files outside declared packages: {offenders[:10]}")

    def test_required_root_modules_exist(self):
        for root, required in ((SRC, SRC_REQUIRED_FILES), (CORE, CORE_REQUIRED_FILES)):
            missing = sorted(name for name in required if not (root / name).is_file())
            self.assertEqual(missing, [])

    def test_dropin_does_not_duplicate_the_provider_engine(self):
        for name in ("platform", "hosts", "integration", "intelligence", "pap", "registry"):
            self.assertFalse((CORE / name).exists(), f"drop-in unexpectedly ships {name}/")

    # ---------------------------------------------------------------- layout
    def test_core_packages_only_import_allowed_layers(self):
        violations = []
        for path in _python_files(CORE):
            parts = path.relative_to(CORE).parts
            if len(parts) < 2:
                continue
            package = parts[0]
            self.assertIn(package, CORE_ALLOWED, f"undeclared package in {path}")
            for level, module, _names in _imports(path):
                self.assertEqual(level, 0, f"{path}: relative import; use absolute ogx.* imports")
                if module == "ogx" or not module.startswith("ogx."):
                    continue
                target = module.split(".")[1]
                if target != package and target not in CORE_ALLOWED[package]:
                    violations.append(f"{path.relative_to(CORE)} imports ogx.{target}")
        self.assertEqual(violations, [])

    def test_core_package_graph_has_no_cycles(self):
        edges = {pkg: set(CORE_ALLOWED[pkg]) for pkg in CORE_ALLOWED}
        visiting, done = set(), set()

        def visit(node):
            self.assertNotIn(node, visiting, f"cycle through {node}")
            if node in done:
                return
            visiting.add(node)
            for nxt in edges[node]:
                visit(nxt)
            visiting.discard(node)
            done.add(node)

        for pkg in edges:
            visit(pkg)

    def test_src_packages_only_import_their_declared_dependencies(self):
        violations = []
        for path in _python_files(SRC):
            parts = path.relative_to(SRC).parts
            if len(parts) < 2:
                continue
            package = parts[0]
            self.assertIn(package, SRC_ALLOWED, f"undeclared package in {path}")
            for level, module, _names in _imports(path):
                target = _resolve_target(path, SRC, level, module)
                if target is None or target == package:
                    continue
                if target not in SRC_ALLOWED[package]:
                    violations.append(f"{path.relative_to(SRC)} imports {target}")
        self.assertEqual(violations, [])

    def test_src_package_graph_has_no_cycles(self):
        edges = {pkg: set() for pkg in SRC_ALLOWED}
        for path in _python_files(SRC):
            parts = path.relative_to(SRC).parts
            if len(parts) < 2:
                continue
            package = parts[0]
            for level, module, _names in _toplevel_imports(path):
                target = _resolve_target(path, SRC, level, module)
                if target and target != package:
                    edges[package].add(target)
        visiting, done = set(), set()

        def visit(node):
            self.assertNotIn(node, visiting, f"cycle through {node}")
            if node in done:
                return
            visiting.add(node)
            for nxt in edges[node]:
                visit(nxt)
            visiting.discard(node)
            done.add(node)

        for pkg in edges:
            visit(pkg)

    def test_no_module_package_name_collisions(self):
        for name, root in (("src", SRC), ("core", CORE)):
            modules = {path.stem for path in root.glob("*.py") if path.name != "__init__.py"}
            packages = _packages(root)
            self.assertEqual(
                sorted(modules & packages),
                [],
                f"{name}: module and package share a name, which makes imports ambiguous",
            )

    def test_all_python_files_parse_without_executing(self):
        for root in (SRC, CORE):
            for path in _python_files(root):
                try:
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except SyntaxError as exc:  # pragma: no cover - failure path
                    self.fail(f"cannot parse {path}: {exc}")

    # ---------------------------------------------------------------- style
    def test_core_uses_only_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names)
        foreign = set()
        for path in _python_files(CORE):
            for level, module, _names in _imports(path):
                top = module.split(".")[0]
                if level == 0 and top and top != "ogx" and top not in stdlib and top != "__future__":
                    foreign.add((path.relative_to(CORE).as_posix(), top))
        self.assertEqual(foreign, set())

    def test_src_uses_only_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names)
        foreign = set()
        for path in _python_files(SRC):
            for level, module, _names in _imports(path):
                top = module.split(".")[0]
                if level == 0 and top and top != "ogx" and top not in stdlib and top != "__future__":
                    foreign.add((path.relative_to(SRC).as_posix(), top))
        self.assertEqual(foreign, set(), f"third-party runtime imports: {sorted(foreign)[:10]}")

    # ---------------------------------------------------------------- versions
    def test_version_sources_agree(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+(?:[a-z]+\d+)?$")
        src_version = _version_literal((SRC / "__init__.py").read_text(encoding="utf-8"), "__version__")
        core_fallback = _version_literal((CORE / "__init__.py").read_text(encoding="utf-8"), "_FALLBACK_VERSION")
        self.assertEqual(src_version, version, "src/ogx/__init__.py version drifted from VERSION")
        self.assertEqual(core_fallback, version, "core/ogx fallback version drifted from VERSION")

    # ---------------------------------------------------------------- packaging
    def test_pyproject_packages_the_canonical_source(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["tool"]["setuptools"]["package-dir"][""], "src")
        self.assertEqual(data["tool"]["setuptools"]["packages"]["find"]["where"], ["src"])
        self.assertEqual(data["project"]["scripts"]["ogx"], "ogx.cli:main")
        self.assertEqual(data["project"]["requires-python"], ">=3.11")
        self.assertEqual(data["project"].get("dependencies", []), [], "stdlib-only promise is broken")
        self.assertEqual(data["tool"]["pytest"]["ini_options"]["pythonpath"], ["src"])

    def test_both_packages_import_cleanly(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        for name, root in (("src", SRC), ("core", CORE)):
            env = dict(os.environ)
            # The import root is the parent (src/ or core/), not the ogx
            # package directory itself, or "import ogx" cannot resolve.
            env["PYTHONPATH"] = str(root.parent)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [sys.executable, "-c", "import ogx, ogx.cli; print(ogx.__version__)"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"{name} package does not import: {result.stderr.strip()}",
            )
            self.assertEqual(result.stdout.strip(), version, f"{name} reports the wrong version")


if __name__ == "__main__":
    unittest.main()
