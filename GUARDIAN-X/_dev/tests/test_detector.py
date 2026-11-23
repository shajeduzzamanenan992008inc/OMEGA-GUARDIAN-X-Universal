import tempfile
import unittest
from pathlib import Path

from ogx.analysis.detector import detect_project


class DetectorTests(unittest.TestCase):
    def test_detects_multiple_ecosystems_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            (root / "frontend").mkdir()
            (root / "backend" / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (root / "frontend" / "package.json").write_text("{}", encoding="utf-8")
            (root / "frontend" / "package-lock.json").write_text("{}", encoding="utf-8")
            profile = detect_project(root)
            self.assertIn("Python", profile.languages)
            self.assertIn("JavaScript/TypeScript", profile.languages)
            self.assertIn("npm-compatible", profile.ecosystems)
            self.assertTrue(any(p.endswith("package-lock.json") for p in profile.lockfiles))

    def test_ignores_vendor_and_symlink_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendor = root / "node_modules"
            vendor.mkdir()
            (vendor / "package.json").write_text("{}", encoding="utf-8")
            profile = detect_project(root)
            self.assertNotIn("JavaScript/TypeScript", profile.languages)


if __name__ == "__main__":
    unittest.main()
