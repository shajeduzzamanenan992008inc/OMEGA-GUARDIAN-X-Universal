import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_shield
from ogx.ecosystem.integration import apply_integration, integration_status, plan_integration, remove_integration


class LinkTests(unittest.TestCase):
    def _project(self, tmp, name="sample"):
        root = Path(tmp) / name
        make_shield(root)
        return root

    def test_plan_is_read_only_and_links_nothing_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "package.json").write_text('{"name":"sample"}\n', encoding="utf-8")
            plan = plan_integration(root)
            self.assertEqual(plan["groups"], [])
            self.assertTrue(all(x["status"] == "not-selected" for x in plan["files"]))
            self.assertEqual(set(plan["available_groups"]), {"copilot", "agents", "editors", "deps"})
            self.assertEqual(sorted(x.name for x in root.iterdir()), ["omega-shield", "package.json"])

    def test_plan_lists_only_the_selected_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            plan = plan_integration(root, ["agents"])
            created = {x["path"] for x in plan["files"] if x["status"] == "create"}
            self.assertEqual(created, {"AGENTS.md", "CLAUDE.md", "GEMINI.md", "CODEX.md", "CURSOR.md", "WINDSURF.md"})
            self.assertFalse((root / "AGENTS.md").exists())

    def test_apply_requires_at_least_one_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            with self.assertRaises(ValueError):
                apply_integration(root)
            self.assertEqual([x.name for x in root.iterdir()], ["omega-shield"])

    def test_unknown_group_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                plan_integration(self._project(tmp), ["everything"])

    def test_apply_all_creates_files_and_keeps_manifest_inside_the_shield(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")
            result = apply_integration(root, ["all"])
            self.assertTrue((root / "omega-shield/link-manifest.json").is_file())
            self.assertTrue((root / ".github/copilot-instructions.md").is_file())
            self.assertTrue((root / ".github/agents/ogx-security-review.agent.md").is_file())
            self.assertTrue((root / ".github/dependabot.yml").is_file())
            renovate = json.loads((root / "renovate.json").read_text(encoding="utf-8"))
            self.assertEqual(renovate["extends"], ["config:recommended"])
            self.assertFalse(renovate["packageRules"][0]["automerge"])
            self.assertTrue((root / "AGENTS.md").is_file())
            self.assertIn("omega-shield/shield.toml", (root / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertNotIn("OMEGA GUARDIAN X/", (root / ".github/copilot-instructions.md").read_text(encoding="utf-8"))
            self.assertTrue(result["created"])
            status = integration_status(root)
            self.assertTrue(status["integrated"])
            self.assertEqual(status["groups"], ["agents", "copilot", "deps", "editors"])

    def test_a_single_group_touches_only_its_own_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            apply_integration(root, ["deps"])
            self.assertTrue((root / "renovate.json").is_file())
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / ".github/copilot-instructions.md").exists())

    def test_existing_user_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / ".github").mkdir(parents=True)
            (root / ".github/copilot-instructions.md").write_text("my existing copilot rules\n", encoding="utf-8")
            (root / ".github/dependabot.yml").write_text("my existing dependabot config\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("my existing agent rules\n", encoding="utf-8")
            result = apply_integration(root, ["all"])
            self.assertEqual((root / ".github/copilot-instructions.md").read_text(encoding="utf-8"), "my existing copilot rules\n")
            self.assertEqual((root / ".github/dependabot.yml").read_text(encoding="utf-8"), "my existing dependabot config\n")
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), "my existing agent rules\n")
            self.assertIn(".github/dependabot.yml", result["preserved"])
            self.assertTrue((root / ".github/instructions/ogx.instructions.md").is_file())

    def test_remove_only_deletes_unchanged_files_and_never_the_shield(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            apply_integration(root, ["all"])
            managed = root / ".github/agents/ogx-debugger.agent.md"
            managed.write_text(managed.read_text(encoding="utf-8") + "\nuser customization\n", encoding="utf-8")
            result = remove_integration(root)
            self.assertFalse((root / ".github/copilot-instructions.md").exists())
            self.assertTrue(managed.exists())
            self.assertIn(".github/agents/ogx-debugger.agent.md", result["preserved_modified_or_missing"])
            self.assertTrue((root / "omega-shield/init.py").is_file())
            status = integration_status(root)
            self.assertTrue(status["integrated"])
            self.assertIn(".github/agents/ogx-debugger.agent.md", status["modified"])

    def test_refuses_project_without_a_shield_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                apply_integration(root, ["all"])
            self.assertEqual(list(root.iterdir()), [])

    def test_refuses_a_folder_that_only_has_the_shield_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "omega-shield").mkdir()
            (root / "omega-shield/important.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                apply_integration(root, ["all"])
            self.assertEqual((root / "omega-shield/important.txt").read_text(encoding="utf-8"), "keep")

    def test_refuses_symlinked_github_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            external = base / "external"
            make_shield(root)
            external.mkdir()
            try:
                (root / ".github").symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are not available on this platform")
            with self.assertRaises(ValueError):
                apply_integration(root, ["all"])
            self.assertEqual(list(external.iterdir()), [])

    def test_manifest_paths_cannot_traverse_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_shield(root)
            (root / "omega-shield/link-manifest.json").write_text(json.dumps({
                "managed_by": "omega-shield-link",
                "managed_files": {"../outside.txt": "bad"},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                integration_status(root)


if __name__ == "__main__":
    unittest.main()
