import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.architecture import analyze_architecture, architecture_mermaid
from ogx.compatibility import compatibility_report
from ogx.diagnostics import analyze_logs
from ogx.inventory import build_inventory
from ogx.licenses import inventory_licenses
from ogx.sbom import generate_sbom
from ogx.skills.registry import validate_skill, list_skills
from ogx.agents.registry import discover_agents
from ogx.connectors.git import detect_provider, remote_summary
from ogx.connectors.providers import repository_coordinates, ProviderClient
from ogx.osv import audit_components
from ogx.reports import render_sarif
from ogx.impact import analyze_impact
from ogx.triage import build_debug_report, render_test_plan
from ogx.sandbox import validate_image_ref, _copy_snapshot, sandbox_plan


class UniversalModulesTests(unittest.TestCase):
    def test_inventory_is_read_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            before = set(root.iterdir())
            result = build_inventory(root)
            self.assertEqual(result["file_count"], 1)
            self.assertEqual(set(root.iterdir()), before)

    def test_architecture_graph_finds_python_import_and_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("from b import run\nclass A: pass\n", encoding="utf-8")
            (root / "b.py").write_text("def run(): return 1\n", encoding="utf-8")
            result = analyze_architecture(root)
            self.assertIn({"from": "a.py", "to": "b.py"}, result["edges"])
            self.assertTrue(any(x["name"] == "A" for x in result["symbols"]))
            self.assertIn("flowchart LR", architecture_mermaid(result))

    def test_sbom_parses_package_json_and_requirements_without_claiming_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8")
            (root / "requirements.txt").write_text("requests>=2.31\n", encoding="utf-8")
            result = generate_sbom(root)
            self.assertEqual(result["bomFormat"], "CycloneDX")
            names = {x["name"] for x in result["components"]}
            self.assertEqual(names, {"react", "requests"})
            self.assertTrue(all(x["version"] == "UNKNOWN" or x["version"] for x in result["components"]))
            self.assertTrue(result["ogx"]["limitations"])

    def test_skill_manifest_validation_and_no_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("# no code execution\n", encoding="utf-8")
            (root / "skill.json").write_text(json.dumps({"name": "test-skill", "version": "1.0.0", "description": "test", "entrypoint": "SKILL.md", "permissions": ["read_project"], "execution": "declarative"}), encoding="utf-8")
            result = validate_skill(root)
            self.assertTrue(result["valid"])
            self.assertFalse(result["security"]["execution_performed"])

    def test_skill_rejects_path_traversal_and_unknown_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skill.json").write_text(json.dumps({"name": "unsafe-skill", "version": "1", "description": "x", "entrypoint": "../run.py", "permissions": ["root"]}), encoding="utf-8")
            result = validate_skill(root)
            self.assertFalse(result["valid"])
            self.assertGreaterEqual(len(result["errors"]), 2)

    def test_log_diagnostics_redacts_inline_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "app.log"
            log.write_text("RuntimeError: failed\npassword=superSecretValue123\n", encoding="utf-8")
            result = analyze_logs([log])
            rendered = json.dumps(result)
            self.assertIn("python-exception", rendered)
            self.assertNotIn("superSecretValue123", rendered)

    def test_license_inventory_reads_spdx_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "LICENSE").write_text("SPDX-License-Identifier: MIT\n", encoding="utf-8")
            result = inventory_licenses(root)
            self.assertEqual(result["license_files"][0]["spdx_identifier"], "MIT")

    def test_provider_detection_and_coordinates(self):
        self.assertEqual(detect_provider("https://github.com/acme/app.git"), "github")
        self.assertEqual(repository_coordinates("https://gitlab.com/group/subgroup/app.git"), ("gitlab", "group/subgroup", "app"))
        self.assertEqual(repository_coordinates("git@github.com:acme/app.git"), ("github", "acme", "app"))
        self.assertEqual(repository_coordinates("ssh://git@git.example/acme/app.git"), ("generic-git", "acme", "app"))
        self.assertEqual(remote_summary("https://user:secret@gitea.example/acme/app.git")["url"], "https://gitea.example/acme/app.git")
        self.assertEqual(remote_summary("https://user:secret@gitea.example/acme/app.git?access_token=hidden#fragment")["url"],
                         "https://gitea.example/acme/app.git")

    def test_sandbox_requires_pinned_digest(self):
        with self.assertRaises(ValueError):
            validate_image_ref("python:3.12-slim")
        validate_image_ref("python@sha256:" + "a" * 64)

    def test_sandbox_snapshot_excludes_secrets_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"; root.mkdir()
            out = Path(tmp) / "outside.txt"; out.write_text("do not copy", encoding="utf-8")
            (root / "main.py").write_text("print('ok')", encoding="utf-8")
            (root / ".env").write_text("API_KEY=hidden", encoding="utf-8")
            try: (root / "escape.txt").symlink_to(out)
            except OSError: pass
            snapshot = Path(tmp) / "snapshot"; snapshot.mkdir()
            stats = _copy_snapshot(root, snapshot)
            self.assertTrue((snapshot / "main.py").exists())
            self.assertFalse((snapshot / ".env").exists())
            self.assertFalse((snapshot / "escape.txt").exists())
            self.assertGreaterEqual(stats["entries_skipped"], 1)

    def test_agent_registry_reports_runtime_status_without_claiming_all_detected(self):
        result = discover_agents()
        self.assertIn("copilot", {x["id"] for x in result})
        self.assertTrue(all(x["status"] in {"detected", "adapter-defined; runtime not detected"} for x in result))

    def test_osv_audit_uses_exact_versions_and_does_not_send_source(self):
        components = [
            {"name": "requests", "version": "2.31.0", "purl": "pkg:pypi/requests@2.31.0"},
            {"name": "unresolved", "version": "UNKNOWN", "purl": "pkg:pypi/unresolved"},
            {"name": "React", "version": "18.2.0", "purl": "pkg:npm/react@18.2.0"},
        ]
        with patch("ogx.osv._post_batch", return_value=[{"vulns": [{"id": "GHSA-test"}]}, {"vulns": []}]) as request:
            result = audit_components(components)
        self.assertEqual(request.call_count, 1)
        sent = request.call_args.args[0]
        self.assertEqual(len(sent), 2)
        self.assertEqual(result["packages_submitted"], 2)
        self.assertEqual(result["advisories_found"], 1)
        self.assertEqual(len(result["packages_skipped"]), 1)
        self.assertIn("source code", result["privacy"])

    def test_provider_client_requires_env_token_and_accepts_self_hosted_forgejo(self):
        with patch.dict("os.environ", {"OGX_FORGEJO_TOKEN": "test-token"}, clear=True):
            client = ProviderClient("forgejo", "https://code.example/acme/project.git")
        self.assertEqual(client.provider, "forgejo")
        self.assertEqual(client.config.api_base, "https://code.example/api/v1")
        with patch.dict("os.environ", {"OGX_GITHUB_TOKEN": "x", "OGX_GITHUB_API_BASE": "http://evil.example/api"}, clear=True):
            with self.assertRaises(ValueError):
                ProviderClient("github", "https://github.com/acme/project.git")
        with patch.dict("os.environ", {"OGX_GITHUB_TOKEN": "x"}, clear=True):
            with self.assertRaises(ValueError):
                ProviderClient("github", "https://github.enterprise/acme/project.git")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(PermissionError):
                ProviderClient("github", "https://github.com/acme/project.git")

    def test_sarif_rendering_is_valid_shape(self):
        from ogx.models import ScanReport, ProjectProfile, Finding
        profile = ProjectProfile(name="demo", root="/tmp/demo", languages=[], ecosystems=[], frameworks=[], manifests=[], lockfiles=[], ci_files=[])
        finding = Finding(rule_id="OGX-TEST", severity="high", title="Test", category="test", message="Issue", path="a.py", line=2, confidence="high")
        report = ScanReport(tool="OMEGA GUARDIAN X", scan_id="abc", version="0.4.0a1", started_at="now", finished_at="now", project=profile, files_examined=1, files_skipped=0, findings=[finding], warnings=[], capabilities={})
        sarif = render_sarif(report)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "OGX-TEST")

    def test_impact_analysis_finds_transitive_static_importers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("import b\n", encoding="utf-8")
            (root / "b.py").write_text("import c\n", encoding="utf-8")
            (root / "c.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = analyze_impact(root, "c.py")
            self.assertEqual(result["direct_dependents"], ["b.py"])
            self.assertEqual(result["transitive_dependents"], ["a.py"])
            with self.assertRaises(ValueError):
                analyze_impact(root, "../outside.py")
            linked = root / "linked.py"
            try:
                linked.symlink_to(root / "c.py")
            except OSError:
                pass
            else:
                with self.assertRaises(ValueError):
                    analyze_impact(root, "linked.py")

    def test_debug_triage_maps_traceback_and_makes_hypotheses_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "app.py"
            source.write_text("def run():\n    raise RuntimeError('boom')\n", encoding="utf-8")
            log = Path(tmp) / "app.log"
            log.write_text(f'Traceback (most recent call last):\n  File "{source}", line 2, in run\nRuntimeError: boom\n', encoding="utf-8")
            result = build_debug_report(root, [log])
            self.assertEqual(result["trace_locations"], [{"file": "app.py", "line": 2}])
            self.assertEqual(result["root_cause_status"], "hypotheses_only_not_proven")
            self.assertIn("Proposal only", render_test_plan(result))

    def test_compatibility_is_a_capability_report_not_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text("[package]\nname='demo'\n", encoding="utf-8")
            result = compatibility_report(root)
            self.assertIn("Rust", {x["language"] for x in result["languages"]})
            self.assertIn("not certification", result["warning"])


if __name__ == "__main__":
    unittest.main()

class EnterpriseStoreTests(unittest.TestCase):
    def test_tenant_project_isolation_and_audit_roles(self):
        from ogx.enterprise.store import EnterpriseStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EnterpriseStore(Path(tmp) / "enterprise.sqlite3")
            store.initialize()
            org_a = store.create_organization("acme", "Acme Inc", "idp|owner-a")
            org_b = store.create_organization("beta", "Beta Inc", "idp|owner-b")
            store.register_project(org_a["organization_id"], "app-a", "github", "https://github.com/acme/app-a", "idp|owner-a")
            store.register_project(org_b["organization_id"], "app-b", "gitlab", "https://gitlab.com/beta/app-b", "idp|owner-b")
            projects = store.list_projects(org_a["organization_id"], "idp|owner-a")
            self.assertEqual([x["name"] for x in projects], ["app-a"])
            with self.assertRaises(PermissionError):
                store.list_projects(org_a["organization_id"], "idp|owner-b")
            events = store.list_audit_events(org_a["organization_id"], "idp|owner-a")
            self.assertTrue(any(x["action"] == "project.register" for x in events))

    def test_membership_role_permissions(self):
        from ogx.enterprise.store import EnterpriseStore
        with tempfile.TemporaryDirectory() as tmp:
            store = EnterpriseStore(Path(tmp) / "enterprise.sqlite3")
            store.initialize()
            org = store.create_organization("team", "Team", "owner")
            store.add_member(org["organization_id"], "auditor", "auditor", "owner")
            with self.assertRaises(PermissionError):
                store.add_member(org["organization_id"], "new-user", "viewer", "auditor")
            with self.assertRaises(PermissionError):
                store.register_project(org["organization_id"], "unauthorized", actor_subject="unknown")
