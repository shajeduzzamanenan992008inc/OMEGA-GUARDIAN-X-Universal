import json
import tempfile
import unittest
from pathlib import Path

from ogx.integration import apply_integration, integration_status, plan_integration, remove_integration


class IntegrationTests(unittest.TestCase):
    def test_plan_is_read_only_and_shows_root_control_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sample"
            root.mkdir()
            (root / "package.json").write_text('{"name":"sample"}\n', encoding="utf-8")
            plan = plan_integration(root)
            self.assertTrue(any(x["path"] == "OMEGA GUARDIAN X/README.md" for x in plan["files"]))
            self.assertTrue(any(x["path"] == ".github/copilot-instructions.md" for x in plan["files"]))
            self.assertFalse((root / "OMEGA GUARDIAN X").exists())
            self.assertEqual([x.name for x in root.iterdir()], ["package.json"])

    def test_apply_creates_control_folder_and_agent_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sample"
            root.mkdir()
            (root / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")
            result = apply_integration(root)
            self.assertTrue((root / "OMEGA GUARDIAN X/project.toml").is_file())
            self.assertTrue((root / ".github/copilot-instructions.md").is_file())
            self.assertTrue((root / ".github/agents/ogx-security-review.agent.md").is_file())
            self.assertTrue((root / ".github/dependabot.yml").is_file())
            renovate = json.loads((root / "renovate.json").read_text(encoding="utf-8"))
            self.assertEqual(renovate["extends"], ["config:recommended"])
            self.assertFalse(renovate["packageRules"][0]["automerge"])
            self.assertTrue((root / "AGENTS.md").is_file())
            self.assertTrue((root / "CLAUDE.md").is_file())
            self.assertTrue(result["created"])
            self.assertTrue(integration_status(root)["integrated"])

    def test_existing_user_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sample"
            (root / ".github").mkdir(parents=True)
            (root / ".github/copilot-instructions.md").write_text("my existing copilot rules\n", encoding="utf-8")
            (root / ".github/dependabot.yml").write_text("my existing dependabot config\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("my existing agent rules\n", encoding="utf-8")
            result = apply_integration(root)
            self.assertEqual((root / ".github/copilot-instructions.md").read_text(encoding="utf-8"), "my existing copilot rules\n")
            self.assertEqual((root / ".github/dependabot.yml").read_text(encoding="utf-8"), "my existing dependabot config\n")
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "my existing agent rules\n")
            self.assertIn(".github/dependabot.yml", result["preserved"])
            self.assertTrue((root / "OMEGA GUARDIAN X/templates/dependabot.yml").is_file())
            self.assertTrue((root / "OMEGA GUARDIAN X/templates/renovate.json").is_file())
            self.assertTrue((root / ".github/instructions/ogx.instructions.md").is_file())

    def test_remove_only_deletes_unchanged_ogx_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sample"
            root.mkdir()
            apply_integration(root)
            managed = root / ".github/agents/ogx-debugger.agent.md"
            managed.write_text(managed.read_text(encoding="utf-8") + "\nuser customization\n", encoding="utf-8")
            result = remove_integration(root)
            self.assertFalse((root / ".github/copilot-instructions.md").exists())
            self.assertTrue(managed.exists())
            self.assertIn(".github/agents/ogx-debugger.agent.md", result["preserved_modified_or_missing"])
            # Modified file remains tracked by the manifest for safe future cleanup.
            status = integration_status(root)
            self.assertTrue(status["integrated"])
            self.assertIn(".github/agents/ogx-debugger.agent.md", status["modified"])

    def test_refuses_unmanaged_omega_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "OMEGA GUARDIAN X").mkdir()
            (root / "OMEGA GUARDIAN X/important.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                apply_integration(root)
            self.assertEqual((root / "OMEGA GUARDIAN X/important.txt").read_text(encoding="utf-8"), "keep")

    def test_refuses_symlinked_github_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            external = base / "external"
            root.mkdir()
            external.mkdir()
            try:
                (root / ".github").symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are not available on this platform")
            with self.assertRaises(ValueError):
                apply_integration(root)
            self.assertEqual(list(external.iterdir()), [])

    def test_manifest_paths_cannot_traverse_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "OMEGA GUARDIAN X").mkdir()
            (root / "OMEGA GUARDIAN X/integration-manifest.json").write_text(json.dumps({
                "managed_by": "omega-guardian-x-project-integration",
                "managed_files": {"../outside.txt": "bad"}
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                integration_status(root)


if __name__ == "__main__":
    unittest.main()
