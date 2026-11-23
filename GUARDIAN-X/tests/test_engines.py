import json
import os
import tempfile
import unittest
from pathlib import Path

from ogx.detector import detect_project
from ogx.engines.build import discover_builds, discover_tests
from ogx.engines.contract import (
    CAPABILITY_NAMES, BaseEngine, Capability, CapabilityStatus, EngineContext,
)
from ogx.engines.language import engine_for_key
from ogx.engines.registry import EngineRegistry
from ogx.workspace import Workspace, WorkspaceError


def make_context(root: Path) -> EngineContext:
    workspace = Workspace.open(root)
    files = [workspace.root / entry.path for entry in workspace.files]
    profile = detect_project(workspace.root, files)
    fingerprint = workspace.fingerprint()
    return EngineContext(
        workspace=workspace,
        profile=profile,
        project_id=fingerprint["project_id"],
        fingerprint=fingerprint,
    )


class ExplodingDetectEngine(BaseEngine):
    engine_id = "test-exploding-detect"
    display_name = "Exploding detect engine"

    def detect(self, context):
        raise RuntimeError("detect failed")


class ExplodingAnalyzeEngine(BaseEngine):
    engine_id = "test-exploding-analyze"
    display_name = "Exploding analyze engine"

    def detect(self, context):
        return True

    def capabilities(self, context):
        return {
            "detect": Capability(CapabilityStatus.SUPPORTED, "detected"),
            "analyze": Capability(CapabilityStatus.SUPPORTED, "analysis"),
        }

    def analyze(self, context):
        raise RuntimeError("analysis failed")


class ExplodingSignalsEngine(BaseEngine):
    engine_id = "test-exploding-signals"
    display_name = "Exploding signals engine"

    def detect(self, context):
        return False

    def _signals(self, context):
        raise RuntimeError("signals failed")


class EngineContractTests(unittest.TestCase):
    def test_registry_ids_are_unique_and_include_android_and_languages(self):
        registry = EngineRegistry()
        engines = registry.all()
        ids = [engine.engine_id for engine in engines]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 50)
        self.assertIn("android", ids)
        self.assertIn("lang-python", ids)
        self.assertIn("lang-javascript", ids)
        self.assertIn("lang-toml", ids)

    def test_health_is_passive_and_capability_vocabulary_is_stable(self):
        registry = EngineRegistry()
        allowed = {status.value for status in CapabilityStatus}
        for health in registry.health():
            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["mode"], "static-read-only")
            self.assertFalse(health["execution_performed"])
            self.assertFalse(health["network_access"])
        with tempfile.TemporaryDirectory() as tmp:
            context = make_context(Path(tmp))
            for result in registry.evaluate(context):
                for name, capability in result.capabilities.items():
                    self.assertIn(name, CAPABILITY_NAMES)
                    self.assertIn(capability.status.value, allowed)

    def test_empty_project_evaluation_is_isolated_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = make_context(Path(tmp))
            results = EngineRegistry().evaluate(context)
            self.assertEqual(len(results), 51)
            for result in results:
                self.assertFalse(result.execution_performed)
                self.assertEqual(result.errors, [])
                self.assertFalse(result.detected)

    def test_detection_failure_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExplodingDetectEngine().evaluate(make_context(Path(tmp)))
            self.assertFalse(result.detected)
            self.assertTrue(any(error.startswith("detect:") for error in result.errors))
            self.assertTrue(all(cap.status is CapabilityStatus.FAILED
                                for cap in result.capabilities.values()))

    def test_operation_failure_is_isolated_to_that_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExplodingAnalyzeEngine().evaluate(make_context(Path(tmp)))
            self.assertTrue(result.detected)
            self.assertEqual(result.capabilities["analyze"].status, CapabilityStatus.FAILED)
            self.assertTrue(any(error.startswith("analyze:") for error in result.errors))

    def test_signal_failure_does_not_crash_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExplodingSignalsEngine().evaluate(make_context(Path(tmp)))
            self.assertFalse(result.detected)
            self.assertEqual(result.errors, [])
            self.assertTrue(result.facts["detection"]["signals"].get("unavailable"))


class LanguageEngineTests(unittest.TestCase):
    def _evaluate(self, root: Path, key: str):
        return engine_for_key(key).evaluate(make_context(root))

    def test_manifest_presence_detects_technologies_without_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"dependencies": {}}', encoding="utf-8")
            (root / "go.mod").write_text("module example.test/demo\n", encoding="utf-8")
            (root / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
            self.assertTrue(engine_for_key("javascript").detect(make_context(root)))
            self.assertTrue(engine_for_key("go").detect(make_context(root)))
            self.assertTrue(engine_for_key("rust").detect(make_context(root)))

    def test_python_syntax_and_redacted_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
            (root / "secret.py").write_text("API_KEY = 'superSecretValue123'\n", encoding="utf-8")
            result = self._evaluate(root, "python")
            rules = {finding.rule_id for finding in result.findings}
            self.assertIn("OGX-BUG-001", rules)
            self.assertIn("OGX-SEC-001", rules)
            serialized = json.dumps(result.to_dict())
            self.assertNotIn("superSecretValue123", serialized)
            secret = next(item for item in result.findings if item.rule_id == "OGX-SEC-001")
            self.assertIn("<redacted>", secret.evidence or "")

    def test_jsonc_comments_and_trailing_commas_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.ts").write_text("const value: number = 1;\n", encoding="utf-8")
            (root / "tsconfig.json").write_text(
                '{\n  // comment\n  "compilerOptions": {"strict": true,},\n}\n', encoding="utf-8"
            )
            result = self._evaluate(root, "typescript")
            self.assertNotIn("OGX-SYNTAX-JSON", {item.rule_id for item in result.findings})

    def test_structured_syntax_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.toml").write_text("[broken\nkey = 1\n", encoding="utf-8")
            (root / "bad.xml").write_text("<root><unclosed></root>\n", encoding="utf-8")
            (root / "bad.yaml").write_text("root:\n\tchild: 1\n", encoding="utf-8")
            toml_result = self._evaluate(root, "toml")
            xml_result = self._evaluate(root, "xml")
            yaml_result = self._evaluate(root, "yaml")
            self.assertIn("OGX-SYNTAX-TOML", {item.rule_id for item in toml_result.findings})
            self.assertIn("OGX-SYNTAX-XML", {item.rule_id for item in xml_result.findings})
            self.assertIn("OGX-SYNTAX-YAML", {item.rule_id for item in yaml_result.findings})

    def test_xml_dtd_is_rejected_before_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "document.xml").write_text(
                '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><root>&xxe;</root>',
                encoding="utf-8",
            )
            result = self._evaluate(root, "xml")
            finding = next(item for item in result.findings if item.rule_id == "OGX-SYNTAX-XML")
            self.assertIn("DTD", finding.title)

    def test_dependency_declarations_are_inventory_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"react": "^18.0.0", "left-pad": "*"},
            }), encoding="utf-8")
            result = self._evaluate(root, "javascript")
            components = result.facts["dependencies"]
            self.assertFalse(components["resolved"])
            self.assertFalse(components["transitive"])
            self.assertEqual(components["vulnerability_data"], "not_queried")
            names = {item["name"] for item in components["components"]}
            self.assertEqual(names, {"react", "left-pad"})
            self.assertIn("OGX-JAVASCRIPT-DEP-UNPINNED", {item.rule_id for item in result.findings})
            self.assertFalse(result.execution_performed)

    def test_build_and_test_discovery_never_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"scripts": {"test": "vitest"}}', encoding="utf-8")
            (root / "app.test.js").write_text("test('x', () => {});\n", encoding="utf-8")
            workspace = Workspace.open(root)
            builds = discover_builds(workspace)
            npm = next(item for item in builds if item["adapter_id"] == "javascript-npm")
            self.assertEqual(npm["execution"], "not_executed")
            self.assertEqual(npm["execution_status"], "not_configured")
            tests = discover_tests(workspace, ["JavaScript/TypeScript"])
            self.assertGreaterEqual(tests["total_test_files"], 1)
            self.assertFalse(tests["executed"])
            self.assertEqual(tests["execution_status"], "not_configured")

    def test_secret_is_not_written_to_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("password = 'canarySecretValue123'\n", encoding="utf-8")
            result = self._evaluate(root, "python")
            self.assertNotIn("canarySecretValue123", json.dumps(result.to_dict()))


class WorkspaceSafetyTests(unittest.TestCase):
    def test_reads_reject_traversal_absolute_paths_and_binaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            (root / "ok.txt").write_text("ok", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"\x00\x01\x02")
            workspace = Workspace.open(root)
            for unsafe in ("../outside.txt", str(outside), "C:/Windows/win.ini"):
                with self.assertRaises(WorkspaceError):
                    workspace.read_text(unsafe)
            with self.assertRaises(WorkspaceError):
                workspace.read_text("binary.bin")

    def test_symlinked_file_is_not_indexed_or_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = Path(tmp) / "outside.txt"
            target.write_text("outside", encoding="utf-8")
            link = root / "link.txt"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("Symlink creation is unavailable on this platform.")
            workspace = Workspace.open(root)
            self.assertFalse(any(entry.path == "link.txt" for entry in workspace.files))
            with self.assertRaises(WorkspaceError):
                workspace.read_text("link.txt")

    def test_workspace_bounds_file_count(self):
        from ogx.config import ScanConfig
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(5):
                (root / f"f{index}.txt").write_text("x", encoding="utf-8")
            workspace = Workspace.open(root, ScanConfig(max_files=2))
            self.assertEqual(len(workspace), 2)
            self.assertTrue(any("File limit reached" in warning for warning in workspace.warnings))


if __name__ == "__main__":
    unittest.main()
