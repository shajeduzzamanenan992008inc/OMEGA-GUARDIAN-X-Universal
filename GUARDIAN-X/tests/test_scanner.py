import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os

from ogx.config import ScanConfig, load_config
from ogx.scanner import scan_repository


class ScannerTests(unittest.TestCase):
    def test_secret_value_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "sk_live_1234567890abcdef"
            (root / "app.py").write_text(f'API_KEY = "{secret}"\n', encoding="utf-8")
            report = scan_repository(root)
            hits = [f for f in report.findings if f.rule_id == "OGX-SEC-001"]
            self.assertEqual(len(hits), 1)
            self.assertNotIn(secret, json.dumps(report.to_dict()))
            self.assertIn("redacted", hits[0].evidence)

    def test_secret_detection_does_not_ignore_values_containing_test_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_name = "API_" + "KEY"
            sample = f'{key_name} = "contestSuperLongKeyABC123"\n'
            (root / "config.py").write_text(sample, encoding="utf-8")
            report = scan_repository(root)
            self.assertTrue(any(f.rule_id == "OGX-SEC-001" for f in report.findings))

    def test_detects_python_and_javascript_dependency_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("requests\nPillow>=10\npillow\n", encoding="utf-8")
            (root / "package.json").write_text(json.dumps({"dependencies": {"left-pad": "*"}}), encoding="utf-8")
            report = scan_repository(root)
            ids = {f.rule_id for f in report.findings}
            self.assertIn("OGX-DEP-001", ids)
            self.assertIn("OGX-DEP-002", ids)
            self.assertIn("OGX-DEP-011", ids)
            self.assertIn("OGX-DEP-012", ids)

    def test_scans_dot_env_file_without_showing_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = "ghp_1234567890abcdefghijklmnop"
            (root / ".env").write_text(f"ACCESS_TOKEN='{token}'\n", encoding="utf-8")
            report = scan_repository(root)
            self.assertTrue(any(f.rule_id == "OGX-SEC-001" for f in report.findings))
            self.assertNotIn(token, json.dumps(report.to_dict()))

    def test_detects_invalid_json_toml_and_xml_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text('{"broken": }', encoding="utf-8")
            (root / "pyproject.toml").write_text('[project\nname = "sample"', encoding="utf-8")
            (root / "pom.xml").write_text("<project><broken></project>", encoding="utf-8")
            report = scan_repository(root)
            rules = {item.rule_id for item in report.findings}
            self.assertIn("OGX-SYNTAX-JSON", rules)
            self.assertIn("OGX-SYNTAX-TOML", rules)
            self.assertIn("OGX-SYNTAX-XML", rules)

    def test_bounded_walk_reports_partial_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(5):
                (root / f"file{i}.py").write_text("print('ok')\n", encoding="utf-8")
            config = ScanConfig(max_files=2)
            report = scan_repository(root, config)
            self.assertLessEqual(report.files_examined + report.files_skipped, 2)
            self.assertTrue(any("File limit reached" in warning for warning in report.warnings))

    def test_invalid_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "ogx-state"
            state.mkdir()
            (state / "config.toml").write_text("[scan]\nmax_files = 0\n", encoding="utf-8")
            with patch.dict(os.environ, {"OGX_HOME": str(state)}):
                with self.assertRaises(ValueError):
                    load_config()

    def test_source_tree_is_not_modified_by_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.py"
            source.write_text("print('hello')\n", encoding="utf-8")
            before = source.read_bytes()
            scan_repository(root)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((root / ".ogx").exists())


if __name__ == "__main__":
    unittest.main()
