from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.cli import main
from ogx.shield.guardian import (
    apply_safe_fixes, build_evolution_report, build_guardian_audit,
    check_upstream, fetch_remote, plan_safe_fixes,
)


class GuardianAuditTests(unittest.TestCase):
    def test_audit_combines_stack_health_quality_and_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("def run():\n    return 42\n", encoding="utf-8")
            report = build_guardian_audit(root)
            self.assertEqual(report["project"]["name"], "demo")
            self.assertIn("Python", report["project"]["languages"])
            self.assertEqual(report["quality_gates"]["runtime_build"], "not_executed")
            self.assertTrue(any(x["area"] == "testing" for x in report["recommendations"]))
            self.assertTrue(any(x["area"] == "dependency-upgrades" for x in report["recommendations"]))
            self.assertFalse(report["self_evolution"]["is_ogx_repository"])

    def test_audit_does_not_run_project_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "executed.txt"
            (root / "danger.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n", encoding="utf-8"
            )
            build_guardian_audit(root)
            self.assertFalse(marker.exists())

    def test_evolution_report_is_proposal_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_evolution_report(Path(tmp))
            self.assertIn("evolution", report)
            self.assertFalse(report["self_evolution"]["automatic_engine_source_rewrite"])
            self.assertIn("reviewed branch", report["evolution"]["change_policy"])

    def test_non_git_folder_can_be_audited_without_git_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("ogx.shield.guardian.shutil.which", return_value=None):
                report = build_guardian_audit(root)
            self.assertFalse(report["signals"]["git"]["is_repository"])


class GuardianRepairTests(unittest.TestCase):
    def test_safe_fix_is_preview_only_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("API_KEY=do-not-read\n", encoding="utf-8")
            preview = apply_safe_fixes(root)
            self.assertEqual(preview["applied"], [])
            self.assertTrue((root / ".env").exists())
            self.assertFalse((root / ".gitignore").exists())
            self.assertEqual(preview["planned"][0]["id"], "OGX-FIX-ENV-IGNORE")

    def test_confirmed_safe_fix_creates_external_backup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "ogx-state")}):
            root = Path(tmp) / "project"
            root.mkdir()
            (root / ".env.local").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / ".gitignore").write_text("dist/\n", encoding="utf-8")
            result = apply_safe_fixes(root, confirm=True)
            text = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("dist/", text)
            self.assertIn(".env", text)
            self.assertIn("!.env.example", text)
            self.assertEqual(len(result["applied"]), 1)
            backup = Path(result["applied"][0]["backup"]) / "gitignore.before"
            self.assertEqual(backup.read_text(encoding="utf-8"), "dist/\n")
            self.assertFalse((Path(tmp) / "ogx-state").is_relative_to(root))

    def test_state_directory_inside_project_blocks_external_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            with patch.dict(os.environ, {"OGX_HOME": str(root / ".ogx-state")}):
                with self.assertRaisesRegex(ValueError, "outside the target project"):
                    apply_safe_fixes(root, confirm=True)
            self.assertFalse((root / ".gitignore").exists())

    def test_tracked_environment_file_blocks_autofix(self):
        if not shutil.which("git"):
            self.skipTest("Git is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".env"], check=True)
            plan = plan_safe_fixes(root)
            self.assertEqual(plan[0]["status"], "blocked")
            result = apply_safe_fixes(root, confirm=True)
            self.assertEqual(result["applied"], [])
            self.assertFalse((root / ".gitignore").exists())

    def test_nested_environment_file_is_found_and_non_utf8_gitignore_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "config"
            nested.mkdir()
            (nested / ".env.local").write_text("TOKEN=x\n", encoding="utf-8")
            (root / ".gitignore").write_bytes(b"keep\xff\n")
            plan = plan_safe_fixes(root)
            self.assertEqual(plan[0]["status"], "blocked")
            self.assertIn("UTF-8", plan[0]["reason"])
            self.assertEqual((root / ".gitignore").read_bytes(), b"keep\xff\n")

    def test_symlinked_gitignore_blocks_autofix(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            outside = base / "outside"
            outside.write_text("user-data\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            try:
                (root / ".gitignore").symlink_to(outside)
            except OSError:
                self.skipTest("Symlink creation is unavailable")
            plan = plan_safe_fixes(root)
            self.assertEqual(plan[0]["status"], "blocked")
            self.assertEqual(outside.read_text(encoding="utf-8"), "user-data\n")


class GuardianUpgradeTests(unittest.TestCase):
    def test_upgrade_check_is_offline_by_default_for_non_git_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = check_upstream(Path(tmp))
            self.assertFalse(report["is_repository"])
            self.assertFalse(report["online_checked"])

    def test_local_git_remote_check_and_fetch_are_ref_only(self):
        if not shutil.which("git"):
            self.skipTest("Git is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            remote = base / "remote.git"
            project = base / "project"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.email", "guardian-test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Guardian Test"], check=True)
            (project / "app.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "app.py"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "initial"], check=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-qu", "origin", "main"], check=True)
            checked = check_upstream(project, online=True)
            self.assertEqual(checked["remotes"][0]["status"], "up_to_date")
            fetched = fetch_remote(project, confirm=True)
            self.assertTrue(fetched["fetched"])
            self.assertTrue(fetched["working_tree_status_unchanged"])
            self.assertEqual((project / "app.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_fetch_requires_confirmation_and_never_attempts_non_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                fetch_remote(Path(tmp), confirm=False)
            with self.assertRaises(ValueError):
                fetch_remote(Path(tmp), confirm=True)

    def test_cli_guardian_audit_writes_reports_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OGX_HOME": str(Path(tmp) / "ogx-state")}):
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            self.assertEqual(main(["guardian", "audit", str(root)]), 0)
            self.assertEqual(list(root.iterdir()), [root / "app.py"])
            reports = list((Path(tmp) / "ogx-state" / "projects").rglob("guardian-audit-*.json"))
            self.assertEqual(len(reports), 1)
            parsed = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(parsed["project"]["name"], "project")

    def test_cli_rejects_report_storage_inside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            with patch.dict(os.environ, {"OGX_HOME": str(root / ".ogx-state")}):
                self.assertEqual(main(["guardian", "audit", str(root)]), 2)
            self.assertFalse((root / ".ogx-state").exists())

    def test_cli_repair_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            self.assertEqual(main(["guardian", "repair", str(root)]), 0)
            self.assertFalse((root / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
