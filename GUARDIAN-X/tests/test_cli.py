import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.cli import main


class CliTests(unittest.TestCase):
    def test_env_init_creates_private_external_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "state")}, clear=True):
            self.assertEqual(main(["env", "init"]), 0)
            env_path = Path(tmp) / "state" / ".env"
            self.assertTrue(env_path.is_file())
            self.assertTrue((Path(__file__).resolve().parents[1] / ".env.example").is_file())

    def test_env_check_loads_explicit_file_and_validates_without_revealing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "private.env"
            env_path.write_text(
                "OGX_MAIL_PROVIDER=brevo\nOGX_BREVO_API_KEY=not-a-real-key\n"
                "OGX_MAIL_FROM_EMAIL=sender@example.com\nOGX_APPROVER_EMAIL=approver@example.com\n"
                "OGX_APPROVER_SUBJECT=idp|approver\nOGX_ACTOR_SUBJECT=idp|requester\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                env_path.chmod(0o600)
            with patch.dict(os.environ, {"OGX_ENV_FILE": str(env_path)}, clear=True):
                self.assertEqual(main(["env", "check"]), 0)

    def test_env_check_returns_nonzero_for_overly_permissive_env_file(self):
        if os.name == "nt":
            self.skipTest("POSIX file mode checks are not available on Windows.")
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "private.env"
            env_path.write_text(
                "OGX_MAIL_PROVIDER=brevo\nOGX_BREVO_API_KEY=unit-test-key\n"
                "OGX_MAIL_FROM_EMAIL=sender@example.com\nOGX_APPROVER_EMAIL=approver@example.com\n"
                "OGX_APPROVER_SUBJECT=idp|approver\nOGX_ACTOR_SUBJECT=idp|requester\n",
                encoding="utf-8",
            )
            env_path.chmod(0o644)
            with patch.dict(os.environ, {"OGX_ENV_FILE": str(env_path)}, clear=True):
                self.assertEqual(main(["env", "check"]), 1)

    def test_cli_refuses_unknown_setting_in_selected_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "private.env"
            env_path.write_text("OGX_TYPO_SETTING=bad\n", encoding="utf-8")
            with patch.dict(os.environ, {"OGX_ENV_FILE": str(env_path)}, clear=True):
                self.assertEqual(main(["scan", tmp]), 2)

    def test_init_writes_only_global_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "ogx-state")}):
            root = Path(tmp) / "target"
            root.mkdir()
            self.assertEqual(main(["init"]), 0)
            self.assertTrue((Path(tmp) / "ogx-state" / "config.toml").is_file())
            self.assertEqual(list(root.iterdir()), [])

    def test_detect_command_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "go.mod").write_text("module example.test/demo\n", encoding="utf-8")
            self.assertEqual(main(["detect", str(root)]), 0)

    def test_start_scans_without_writing_target(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "ogx-state")}):
            root = Path(tmp) / "target"
            root.mkdir()
            (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
            self.assertEqual(main(["start", str(root)]), 0)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["main.py"])
            reports = list((Path(tmp) / "ogx-state" / "projects").rglob("*.json"))
            self.assertEqual(len(reports), 1)

    def test_project_registry_is_external(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "ogx-state")}):
            root = Path(tmp) / "target"
            root.mkdir()
            self.assertEqual(main(["projects", "add", str(root), "--name", "demo"]), 0)
            registry = Path(tmp) / "ogx-state" / "projects.json"
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8"))["demo"], str(root.resolve()))
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(main(["scan", "--project", "demo"]), 0)

    def test_integrate_apply_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "target"
            root.mkdir()
            self.assertEqual(main(["integrate", "apply", str(root)]), 2)
            self.assertFalse((root / "OMEGA GUARDIAN X").exists())

    def test_integrate_cli_uses_explicit_local_break_glass_for_legacy_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_ALLOW_UNAPPROVED_WRITES": "1"}):
            root = Path(tmp) / "target"
            root.mkdir()
            self.assertEqual(main(["integrate", "apply", str(root), "--confirm"]), 0)
            self.assertTrue((root / "OMEGA GUARDIAN X/project.toml").is_file())
            self.assertTrue((root / ".github/instructions/ogx.instructions.md").is_file())

    def test_integrate_confirm_does_not_write_without_email_approval_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "OGX_HOME": str(Path(tmp) / "ogx-state"), "OGX_ACTOR_SUBJECT": "idp|requester",
        }, clear=False):
            root = Path(tmp) / "target"
            root.mkdir()
            self.assertEqual(main(["integrate", "apply", str(root), "--confirm"]), 2)
            self.assertFalse((root / "OMEGA GUARDIAN X").exists())

    def test_osv_network_query_requires_two_explicit_opt_ins(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "ogx-state")}):
            root = Path(tmp) / "target"
            root.mkdir()
            (root / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
            with patch("ogx.cli.audit_components", return_value={"advisories_found": 0, "packages_submitted": 0, "packages_skipped": [], "findings": [], "warnings": [], "privacy": "Only package names and versions were sent."}) as audit:
                self.assertEqual(main(["osv-audit", str(root)]), 2)
                self.assertEqual(audit.call_count, 0)
                self.assertEqual(main(["osv-audit", str(root), "--online", "--confirm-network"]), 0)
                self.assertEqual(audit.call_count, 1)

    def test_impact_cli_reports_static_importers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "a.py").write_text("import b\n", encoding="utf-8")
            (root / "b.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertEqual(main(["impact", str(root), "--file", "b.py"]), 0)

    def test_scan_refuses_symlinked_external_reports_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "ogx-state"
            home.mkdir()
            outside = base / "outside"
            outside.mkdir()
            project = base / "project"
            project.mkdir()
            (project / "app.py").write_text("print('ok')\n", encoding="utf-8")
            try:
                (home / "projects").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation is unavailable on this test platform.")
            with patch.dict(os.environ, {"OGX_HOME": str(home)}):
                self.assertEqual(main(["scan", str(project)]), 2)
            self.assertEqual(list(outside.iterdir()), [])

    def test_scan_ignores_project_local_ogx_config(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "ogx-state")}):
            root = Path(tmp) / "target"
            (root / ".ogx").mkdir(parents=True)
            (root / ".ogx" / "config.toml").write_text('[scan]\nmax_files = 0\n', encoding="utf-8")
            (root / "app.py").write_text("print('safe')\n", encoding="utf-8")
            self.assertEqual(main(["scan", str(root)]), 0)
            self.assertEqual((root / ".ogx" / "config.toml").read_text(encoding="utf-8"), '[scan]\nmax_files = 0\n')


if __name__ == "__main__":
    unittest.main()
