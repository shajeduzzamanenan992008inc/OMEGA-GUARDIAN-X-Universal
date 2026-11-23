"""Enforces the one-way layering of the capability packages and the stdlib-only promise."""
import ast
import sys
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "core" / "ogx"

# package -> packages it may import (besides itself). "cli" is the composition root.
ALLOWED = {
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


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node.level, node.module or "", [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name, []


class ArchitectureTests(unittest.TestCase):
    def test_packages_only_import_allowed_layers(self):
        violations = []
        for path in sorted(CORE.rglob("*.py")):
            parts = path.relative_to(CORE).parts
            if len(parts) < 2:
                continue  # ogx/__init__.py, ogx/__main__.py
            package = parts[0]
            for level, module, _names in _imports(path):
                self.assertEqual(level, 0, f"{path}: relative import; use absolute ogx.* imports")
                if module == "ogx" or not module.startswith("ogx."):
                    continue
                target = module.split(".")[1]
                if target != package and target not in ALLOWED[package]:
                    violations.append(f"{path.relative_to(CORE)} imports ogx.{target}")
        self.assertEqual(violations, [])

    def test_every_package_is_declared(self):
        packages = {p.name for p in CORE.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
        self.assertEqual(packages, set(ALLOWED))

    def test_no_package_level_cycles(self):
        edges = {pkg: set(ALLOWED[pkg]) for pkg in ALLOWED}
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

    def test_core_uses_only_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names)
        foreign = set()
        for path in CORE.rglob("*.py"):
            for level, module, _names in _imports(path):
                top = module.split(".")[0]
                if level == 0 and top and top != "ogx" and top not in stdlib and top != "__future__":
                    foreign.add((path.relative_to(CORE).as_posix(), top))
        self.assertEqual(foreign, set())


if __name__ == "__main__":
    unittest.main()
