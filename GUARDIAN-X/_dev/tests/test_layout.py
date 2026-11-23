import tempfile
import unittest
from pathlib import Path

import ogx
from helpers import make_shield
from ogx.analysis.detector import detect_project
from ogx.kernel.config import DEFAULT_EXCLUDES, ScanConfig
from ogx.kernel.paths import (default_excludes, is_ogx_source, is_shield_folder, parse_link_groups,
                              policy_relpath, shield_dirname)
from ogx.shield.scanner import scan_repository

ROOT = Path(__file__).resolve().parents[2]


class LayoutTests(unittest.TestCase):
    def test_version_has_a_single_source(self):
        version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(ogx.__version__, version_file)
        self.assertEqual(ogx._FALLBACK_VERSION, version_file)

    def test_shield_folder_name_has_no_spaces(self):
        self.assertEqual(shield_dirname(), "omega-shield")
        self.assertNotIn(" ", policy_relpath().as_posix())
        self.assertIn(shield_dirname(), default_excludes())
        self.assertIn(shield_dirname(), DEFAULT_EXCLUDES)

    def test_this_tree_is_recognised_as_the_ogx_source(self):
        self.assertTrue(is_ogx_source(ROOT))
        self.assertFalse(is_ogx_source(ROOT / "core"))

    def test_shield_folder_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_shield(Path(tmp))
            self.assertTrue(is_shield_folder(folder))
            self.assertFalse(is_shield_folder(Path(tmp)))

    def test_shield_folder_never_pollutes_detection_or_scan(self):
        """Regression: the vendored runtime (schema.sql, fake secrets) must not count as host code."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            shield = make_shield(root)
            (shield / "core" / "schema.sql").write_text("select 1;\n", encoding="utf-8")
            (shield / "core" / "leak.py").write_text('password = "Sup3rS3cretPassw0rd!"\n', encoding="utf-8")
            self.assertEqual(detect_project(root).languages, ["Python"])
            report = scan_repository(root, ScanConfig())
            self.assertEqual(report.summary.get("high", 0) + report.summary.get("critical", 0), 0)
            self.assertEqual(report.project.languages, ["Python"])

    def test_link_group_parsing(self):
        self.assertEqual(parse_link_groups(["copilot,deps"]), ["copilot", "deps"])
        self.assertEqual(parse_link_groups(["all"]), ["copilot", "agents", "editors", "deps"])
        self.assertEqual(parse_link_groups(None), [])
        with self.assertRaises(ValueError):
            parse_link_groups(["nope"])

    def test_env_example_ships_with_the_shield(self):
        self.assertTrue((ROOT / ".env.example").is_file())


if __name__ == "__main__":
    unittest.main()
