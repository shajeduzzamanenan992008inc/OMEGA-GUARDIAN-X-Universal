"""Regression tests for the drop-in archive generator.

The generator is exercised against synthetic fixture trees in temporary
directories, so these tests never depend on the state of the repository working
tree. Each check maps to the archive policy asserted in
``.github/workflows/ci.yml`` plus the drop-in distribution contract.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "_dev" / "tools" / "make_dist.py"

# The exact tokens asserted by the drop-in job in .github/workflows/ci.yml.
CI_FORBIDDEN = ("/tests/", "/_dev/", "/.github/", "/release/", "/src/", "__pycache__", "/results.txt")

EXPECTED_ENTRIES = [
    "omega-shield/.env.example",
    "omega-shield/VERSION",
    "omega-shield/core/ogx/__init__.py",
    "omega-shield/core/ogx/cli.py",
    "omega-shield/docs/ARCHITECTURE.md",
    "omega-shield/guard/gate.py",
    "omega-shield/init.py",
    "omega-shield/shield.toml",
]


def _load_generator():
    spec = importlib.util.spec_from_file_location("ogx_make_dist", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_dist = _load_generator()


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_fixture(root: Path) -> None:
    """Create one tree containing every include and exclude rule the tool owns."""
    # Required runtime content and legitimate documentation.
    _write(root / "VERSION", "9.9.9\n")
    _write(root / "shield.toml", "[shield]\n")
    _write(root / "init.py", "print('init')\n")
    _write(root / "core" / "ogx" / "__init__.py", "_FALLBACK_VERSION = '9.9.9'\n")
    _write(root / "core" / "ogx" / "cli.py", "def main():\n    return 0\n")
    _write(root / "guard" / "gate.py", "GATE = True\n")
    _write(root / "docs" / "ARCHITECTURE.md", "# Architecture\n")

    # Repository and development directories, at the top level and nested.
    _write(root / ".github" / "workflows" / "ci.yml", "name: ci\n")
    _write(root / "GUARDIAN-X" / ".github" / "RELEASE_NOTES.md", "# stray mirror\n")
    _write(root / "GUARDIAN-X" / ".git" / "config", "[core]\n")
    _write(root / "pkg" / "_dev" / "tools" / "tool.py", "TOOL = 1\n")
    _write(root / "pkg" / "tests" / "test_x.py", "def test_x():\n    pass\n")
    _write(root / "pkg" / "src" / "app.py", "APP = 1\n")
    _write(root / "pkg" / "release" / "notes.txt", "notes\n")
    _write(root / "pkg" / "build" / "out.bin", "bin\n")
    _write(root / "pkg" / "dist" / "pkg.zip", "zip\n")

    # Caches and build metadata.
    _write(root / "pkg" / ".pytest_cache" / "CACHEDIR.TAG", "tag\n")
    _write(root / "pkg" / "__pycache__" / "mod.cpython-311.pyc", "pyc\n")
    _write(root / "core" / "omega_guardian_x.egg-info" / "PKG-INFO", "metadata\n")
    _write(root / "core" / "omega_guardian_x.egg-info" / "SOURCES.txt", "sources\n")

    # Top-level packaging file, secrets and generated artifacts.
    _write(root / "pyproject.toml", "[project]\n")
    _write(root / ".env", "SECRET=1\n")
    _write(root / ".env.example", "SECRET=\n")
    _write(root / ".env.local", "SECRET=2\n")
    _write(root / "results.txt", "results\n")
    _write(root / "core" / "cache.pyc", "pyc\n")


class ArchiveGeneratorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "tree"
        _build_fixture(self.root)
        self.archive = Path(self._tmp.name) / "dropin.zip"
        self.count = make_dist.build_archive(self.root, self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            self.names = archive.namelist()

    # ------------------------------------------------------------- exclusions
    def test_github_is_excluded_at_any_depth(self):
        offenders = [name for name in self.names if ".github" in Path(name).parts]
        self.assertEqual(offenders, [])
        self.assertNotIn("omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md", self.names)

    def test_nested_git_is_excluded(self):
        offenders = [name for name in self.names if ".git" in Path(name).parts]
        self.assertEqual(offenders, [])

    def test_development_directories_are_excluded_at_any_depth(self):
        for directory in ("_dev", "tests", "src", "release", "build", "dist"):
            with self.subTest(directory=directory):
                offenders = [name for name in self.names if directory in Path(name).parts]
                self.assertEqual(offenders, [])
        self.assertFalse(any(name.startswith("omega-shield/pkg/") for name in self.names))

    def test_cache_directories_are_excluded(self):
        for marker in (".pytest_cache", "__pycache__"):
            with self.subTest(marker=marker):
                self.assertEqual([name for name in self.names if marker in name], [])

    def test_egg_info_build_metadata_is_excluded(self):
        offenders = [name for name in self.names if ".egg-info" in name]
        self.assertEqual(offenders, [])

    def test_top_level_packaging_file_and_secret_rules(self):
        self.assertNotIn("omega-shield/pyproject.toml", self.names)
        self.assertNotIn("omega-shield/.env", self.names)
        self.assertNotIn("omega-shield/.env.local", self.names)
        self.assertNotIn("omega-shield/results.txt", self.names)
        self.assertEqual([name for name in self.names if name.endswith(".pyc")], [])
        self.assertIn("omega-shield/.env.example", self.names)

    # ------------------------------------------------------------- inclusions
    def test_required_runtime_files_are_included(self):
        for required in (
            "omega-shield/init.py",
            "omega-shield/core/ogx/__init__.py",
            "omega-shield/VERSION",
            "omega-shield/shield.toml",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.names)

    def test_documentation_and_runtime_directories_are_retained(self):
        self.assertIn("omega-shield/docs/ARCHITECTURE.md", self.names)
        self.assertIn("omega-shield/guard/gate.py", self.names)
        self.assertIn("omega-shield/core/ogx/cli.py", self.names)

    def test_archive_uses_the_omega_shield_prefix_for_every_entry(self):
        self.assertTrue(self.names)
        self.assertEqual([name for name in self.names if not name.startswith("omega-shield/")], [])
        self.assertEqual(self.count, len(self.names))

    def test_archive_contains_exactly_the_expected_runtime_content(self):
        # Compare content, not order: path ordering is case-insensitive on
        # Windows and case-sensitive on POSIX, so a fixed sequence is not a
        # portable assertion. Ordering determinism is covered separately.
        self.assertEqual(len(self.names), len(EXPECTED_ENTRIES))
        self.assertEqual(set(self.names), set(EXPECTED_ENTRIES))

    def test_no_absolute_or_traversing_entries(self):
        for name in self.names:
            with self.subTest(name=name):
                self.assertFalse(name.startswith("/"))
                self.assertNotIn("..", Path(name).parts)
                self.assertNotIn("\\", name)

    # --------------------------------------------------------------- policy
    def test_ci_forbidden_token_policy_passes_on_the_fixture(self):
        bad = [name for name in self.names if any(token in name for token in CI_FORBIDDEN)]
        self.assertEqual(bad, [])

    def test_entry_order_is_deterministic_and_path_independent(self):
        second = Path(self._tmp.name) / "dropin-again.zip"
        make_dist.build_archive(self.root, second)
        with zipfile.ZipFile(second) as archive:
            self.assertEqual(archive.namelist(), self.names)

        other_root = Path(self._tmp.name) / "other-tree"
        _build_fixture(other_root)
        other = Path(self._tmp.name) / "dropin-other.zip"
        make_dist.build_archive(other_root, other)
        with zipfile.ZipFile(other) as archive:
            self.assertEqual(archive.namelist(), self.names)


if __name__ == "__main__":
    unittest.main()
