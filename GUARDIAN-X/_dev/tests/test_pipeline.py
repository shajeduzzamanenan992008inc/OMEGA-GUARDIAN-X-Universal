import json
import tempfile
import unittest
from pathlib import Path

from ogx.kernel.config import ScanConfig
from ogx.kernel.findings import unify
from ogx.kernel.models import Finding
from ogx.analysis.pipeline import (
    _cross_engine_dedupe, analyze_project, inspect_engines, render_universal_markdown,
)


class PipelineTests(unittest.TestCase):
    def test_empty_project_produces_an_honest_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(root.iterdir())
            report = analyze_project(root)
            self.assertEqual(report["schema"], "ogx-universal-report-1")
            self.assertEqual(len(report["engines"]), 51)
            self.assertEqual(report["findings"], [])
            self.assertEqual(report["errors"], [])
            self.assertFalse(report["execution_performed"])
            self.assertFalse(report["network_access"])
            self.assertIn("not a certification", render_universal_markdown(report))
            self.assertEqual(sorted(root.iterdir()), before)

    def test_mixed_project_reports_detected_engines_and_unified_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("API_KEY = 'canarySecretValue123'\n", encoding="utf-8")
            (root / "package.json").write_text('{"dependencies": {"react": "^18.0.0"}}', encoding="utf-8")
            (root / "app.ts").write_text("const value: number = 1;\n", encoding="utf-8")
            (root / "go.mod").write_text("module example.test/demo\n", encoding="utf-8")
            app = root / "app" / "src" / "main"
            app.mkdir(parents=True)
            (app / "AndroidManifest.xml").write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                'package="com.example"><uses-permission android:name="android.permission.CAMERA" />'
                '<application android:usesCleartextTraffic="true" /></manifest>',
                encoding="utf-8",
            )
            report = analyze_project(root)
            self.assertIn("android", report["detected_engines"])
            self.assertIn("lang-python", report["detected_engines"])
            self.assertIn("lang-typescript", report["detected_engines"])
            self.assertIn("lang-go", report["detected_engines"])
            self.assertGreater(len(report["findings"]), 0)
            for finding in report["findings"]:
                self.assertTrue(finding["finding_id"].startswith("ogx-"))
                self.assertIn(finding["verification_status"], {"observed", "inferred", "ai_suggested", "verified"})
                self.assertIn("also_reported_by", finding)
            serialized = json.dumps(report)
            self.assertNotIn("canarySecretValue123", serialized)

    def test_malformed_files_do_not_crash_the_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            (root / "bad.json").write_text("{", encoding="utf-8")
            (root / "bad.toml").write_text("[broken\n", encoding="utf-8")
            (root / "bad.xml").write_text("<root>", encoding="utf-8")
            report = analyze_project(root)
            rules = {item["rule_id"] for item in report["findings"]}
            self.assertIn("OGX-BUG-001", rules)
            self.assertIn("OGX-SYNTAX-JSON", rules)
            self.assertIn("OGX-SYNTAX-TOML", rules)
            self.assertIn("OGX-SYNTAX-XML", rules)
            self.assertEqual(report["errors"], [])

    def test_cross_engine_duplicates_are_merged_with_provenance(self):
        finding = Finding(
            "OGX-TEST-DEDUPE", "Duplicate", "medium", "test", "Same observation",
            "app.py", 3, recommendation="Review it.",
        )
        first = unify(finding, project_id="project", engine_id="lang-python", engine_version="1")
        second = unify(finding, project_id="project", engine_id="lang-kotlin", engine_version="1")
        merged = _cross_engine_dedupe([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].rule_id, "OGX-TEST-DEDUPE")
        self.assertTrue(merged[0].also_reported_by)

    def test_engine_selection_and_unknown_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            report = analyze_project(root, engine_ids=["lang-python"])
            self.assertEqual([item["engine_id"] for item in report["engines"]], ["lang-python"])
            with self.assertRaises(ValueError):
                analyze_project(root, engine_ids=["lang-does-not-exist"])

    def test_inspect_engines_is_lightweight_and_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            result = inspect_engines(root, engine_ids=["lang-python", "android"])
            self.assertEqual(result["schema"], "ogx-engine-inspection-1")
            self.assertEqual(len(result["engines"]), 2)
            self.assertIn("lang-python", result["detected_engines"])
            self.assertFalse(result["execution_performed"])
            self.assertFalse(result["network_access"])
            python = next(item for item in result["engines"] if item["engine_id"] == "lang-python")
            self.assertEqual(python["health"]["mode"], "static-read-only")

    def test_bounded_workspace_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(6):
                (root / f"f{index}.py").write_text("x = 1\n", encoding="utf-8")
            report = analyze_project(root, ScanConfig(max_files=2))
            self.assertEqual(report["project"]["file_count"], 2)
            self.assertTrue(any("File limit reached" in warning for warning in report["warnings"]))

    def test_symlinked_files_are_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "outside.py"
            outside.write_text("API_KEY = 'outsideSecretValue123'\n", encoding="utf-8")
            try:
                (root / "link.py").symlink_to(outside)
            except OSError:
                self.skipTest("Symlink creation is unavailable on this platform.")
            report = analyze_project(root)
            self.assertFalse(any(item["path"] == "link.py" for item in report["findings"]))
            self.assertNotIn("outsideSecretValue123", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
