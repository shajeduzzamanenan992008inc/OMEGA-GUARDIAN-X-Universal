import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.cli import build_parser, main


def run_cli(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class UniversalCliTests(unittest.TestCase):
    def test_engines_list_json_is_additive_and_passive(self):
        code, output, _ = run_cli(["engines", "list", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(data["schema"], "ogx-engine-registry-1")
        self.assertGreaterEqual(data["count"], 50)
        self.assertIn("android", {item["engine_id"] for item in data["engines"]})
        self.assertTrue(all(item["health"]["execution_performed"] is False
                            for item in data["engines"]))

    def test_engines_inspect_reports_selected_engines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
            code, output, _ = run_cli([
                "engines", "inspect", str(root), "--engines", "lang-python,android", "--json",
            ])
            self.assertEqual(code, 0)
            data = json.loads(output)
            self.assertEqual(len(data["engines"]), 2)
            self.assertIn("lang-python", data["detected_engines"])
            self.assertFalse(data["execution_performed"])

    def test_analyze_writes_only_external_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            (root / "app.py").write_text("API_KEY = 'cliSecretValue123'\n", encoding="utf-8")
            before = sorted(root.iterdir())
            with patch.dict(os.environ, {"OGX_HOME": str(base / "state")}, clear=False), \
                    patch("ogx.cli.app.load_runtime_env", return_value=None):
                code, output, _ = run_cli(["analyze", str(root), "--format", "both"])
                self.assertEqual(code, 0)
                self.assertIn("External report:", output)
                self.assertIn("Target repository: READ-ONLY", output)
                reports = list((base / "state" / "projects").rglob("universal-analysis-*"))
                self.assertEqual(len(reports), 2)
                self.assertTrue(all(not path.is_relative_to(root) for path in reports))
                serialized = "\n".join(path.read_text(encoding="utf-8") for path in reports)
                self.assertNotIn("cliSecretValue123", serialized)
            self.assertEqual(sorted(root.iterdir()), before)

    def test_analyze_strict_returns_one_for_high_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            (root / "app.py").write_text("API_KEY = 'strictSecretValue123'\n", encoding="utf-8")
            with patch.dict(os.environ, {"OGX_HOME": str(base / "state")}, clear=False), \
                    patch("ogx.cli.app.load_runtime_env", return_value=None):
                code, _, _ = run_cli(["analyze", str(root), "--format", "json", "--strict"])
            self.assertEqual(code, 1)

    def test_analyze_unknown_engine_returns_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "state")}, clear=False), \
                    patch("ogx.cli.app.load_runtime_env", return_value=None):
                code, _, stderr = run_cli(["analyze", str(root), "--engines", "lang-missing"])
            self.assertEqual(code, 2)
            self.assertIn("Universal analysis failed", stderr)

    def test_android_command_writes_external_report_without_device_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            main_dir = root / "app" / "src" / "main"
            main_dir.mkdir(parents=True)
            (main_dir / "AndroidManifest.xml").write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                'package="com.example"><application android:usesCleartextTraffic="true" />'
                '</manifest>',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OGX_HOME": str(base / "state")}, clear=False), \
                    patch("ogx.cli.app.load_runtime_env", return_value=None):
                code, output, _ = run_cli(["android", str(root), "--format", "json", "--strict"])
                self.assertEqual(code, 1)
                self.assertIn("Android detected: True", output)
                self.assertIn("No Gradle build, Android SDK tool", output)
                reports = list((base / "state" / "projects").rglob("android-analysis-*.json"))
                self.assertEqual(len(reports), 1)
                report = json.loads(reports[0].read_text(encoding="utf-8"))
                self.assertEqual(report["schema"], "ogx-android-report-1")
                self.assertFalse(report["execution_performed"])

    def test_existing_commands_and_parser_shape_are_preserved(self):
        parser = build_parser()
        actions = next(action for action in parser._actions if action.dest == "command")
        commands = set(actions.choices)
        for command in ("detect", "scan", "start", "inventory", "architecture", "sbom",
                        "diagnose", "validate", "guardian", "enterprise", "governance"):
            self.assertIn(command, commands)
        for command in ("engines", "analyze", "android"):
            self.assertIn(command, commands)

    def test_detect_command_still_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text("module example.test/demo\n", encoding="utf-8")
            code, output, _ = run_cli(["detect", str(root)])
            self.assertEqual(code, 0)
            data = json.loads(output)
            self.assertIn("Go", data["languages"])


if __name__ == "__main__":
    unittest.main()
