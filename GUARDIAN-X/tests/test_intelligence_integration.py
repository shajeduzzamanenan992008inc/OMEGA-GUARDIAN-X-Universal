from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ogx.cli import main
from ogx.intelligence import AnalysisOptions, ProjectIntelligenceEngine
from ogx.intelligence.integration import (
    resolve_project_identity, resolve_registered_project,
)
from ogx.pap import create_identity, read_identity
from ogx.registry.v2 import RegistryV2


def write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_cli(arguments: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(arguments)
    return code, out.getvalue(), err.getvalue()


class IdentityAndRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        write(self.root, "pyproject.toml", """[project]
name = "demo"
dependencies = ["fastapi>=0.110"]
""")
        write(self.root, "app.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        self._registries: list[RegistryV2] = []

    def _registry(self) -> RegistryV2:
        registry = RegistryV2(self.base / "registry.db")
        self._registries.append(registry)
        return registry

    def tearDown(self):
        for registry in self._registries:
            registry.close()
        self._tmp.cleanup()

    def test_identity_is_preserved_and_survives_relocation(self):
        identity = create_identity(self.root, project_name="Immutable Project")
        report = ProjectIntelligenceEngine().analyze(
            self.root, AnalysisOptions(registry_lookup=False))
        self.assertEqual(report.project.identity.source, "identity")
        self.assertEqual(report.project.project_id, str(identity.project_id))
        self.assertEqual(report.project.identity.canonical_name, "Immutable Project")

        moved = self.base / "relocated"
        shutil.move(str(self.root), str(moved))
        relocated = ProjectIntelligenceEngine().analyze(
            moved, AnalysisOptions(registry_lookup=False))
        self.assertEqual(relocated.project.project_id, str(identity.project_id))
        self.assertEqual(relocated.project.identity.source, "identity")
        self.assertEqual(read_identity(moved).project_id, identity.project_id)

    def test_explicit_different_id_does_not_override_identity(self):
        identity = create_identity(self.root, project_name="Immutable")
        explicit = str(uuid4())
        report = ProjectIntelligenceEngine().analyze(
            self.root, AnalysisOptions(registry_lookup=False), project_id=explicit)
        self.assertEqual(report.project.project_id, str(identity.project_id))
        self.assertTrue(any("differs from the immutable identity" in note
                            for note in report.project.identity.notes))

    def test_fingerprint_identity_is_deterministic_without_registry(self):
        first = resolve_project_identity(self.root, fallback_project_id="ogx-test")
        second = resolve_project_identity(self.root, fallback_project_id="ogx-test")
        self.assertEqual(first.source, "fingerprint")
        self.assertEqual(first.project_id, second.project_id)
        self.assertTrue(first.project_id.startswith("ogx-"))

    def test_registry_association_does_not_create_duplicates(self):
        registry = self._registry()
        project_id = uuid4()
        workspace_id = uuid4()
        registry.register_project(project_id, "demo", identity_hash="sha256:demo")
        registry.register_workspace(
            workspace_id, project_id, str(self.root.resolve()),
            str(self.root.resolve()), display_name="demo")
        before_projects = len(registry.list_projects())
        before_workspaces = len(registry.list_workspaces(project_id))

        identity = resolve_project_identity(self.root, registry=registry)
        self.assertEqual(identity.source, "registry")
        self.assertEqual(identity.project_id, str(project_id))
        self.assertEqual(identity.registry_project_id, str(project_id))
        self.assertEqual(len(registry.list_projects()), before_projects)
        self.assertEqual(len(registry.list_workspaces(project_id)), before_workspaces)

        resolved_root, resolved_id, resolved_name = resolve_registered_project(
            "demo", registry=registry)
        self.assertEqual(resolved_root, self.root.resolve())
        self.assertEqual(resolved_id, str(project_id))
        self.assertEqual(resolved_name, "demo")
        by_id = resolve_registered_project(str(project_id), registry=registry)
        self.assertEqual(by_id[0], self.root.resolve())

    def test_engine_registry_lookup_associates_existing_project_only(self):
        registry = self._registry()
        project_id = uuid4()
        registry.register_project(project_id, "demo")
        registry.register_workspace(
            uuid4(), project_id, str(self.root.resolve()), str(self.root.resolve()))
        with patch("ogx.registry.get_registry", return_value=registry):
            report = ProjectIntelligenceEngine().analyze(
                self.root, AnalysisOptions(registry_lookup=True))
        self.assertEqual(report.project.identity.source, "registry")
        self.assertEqual(report.project.identity.registry_project_id, str(project_id))
        self.assertEqual(len(registry.list_projects()), 1)

    def test_registry_project_without_local_workspace_is_an_error(self):
        registry = self._registry()
        project_id = uuid4()
        registry.register_project(project_id, "remote-only")
        with self.assertRaises(ValueError):
            resolve_registered_project("remote-only", registry=registry)


class CliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        write(self.root, "pyproject.toml", """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "demo"
version = "0.1.0"
dependencies = ["fastapi>=0.110"]
""")
        write(self.root, "README.md", "# Demo\n")
        write(self.root, "src/demo/main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        write(self.root, "tests/test_main.py", "def test_ok():\n    assert True\n")

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self):
        return patch.dict(os.environ, {"OGX_HOME": str(self.base / "state")})

    def test_json_output_is_machine_readable_and_read_only(self):
        with self._env():
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--format", "json", "--no-registry",
            ])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["metadata"]["schema_version"], "ogx-intelligence-1")
        self.assertFalse(data["metadata"]["execution_performed"])
        self.assertFalse(data["metadata"]["network_access"])
        self.assertIn("FastAPI", {item["name"] for item in data["frameworks"]})
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["README.md", "pyproject.toml", "src", "tests"])

    def test_json_alias_and_section_filters(self):
        with self._env():
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--json", "--no-registry",
            ])
            self.assertEqual(code, 0, err)
            json.loads(out)
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--languages", "--no-registry",
            ])
        self.assertEqual(code, 0, err)
        self.assertIn("Languages", out)
        self.assertNotIn("Engineering health", out)

    def test_markdown_output(self):
        with self._env():
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--format", "markdown", "--no-registry",
            ])
        self.assertEqual(code, 0, err)
        self.assertIn("# OMEGA GUARDIAN X", out)
        self.assertIn("## Languages", out)
        self.assertIn("Python", out)

    def test_both_format_writes_external_artifacts(self):
        before = sorted(path.relative_to(self.root).as_posix()
                        for path in self.root.rglob("*") if path.is_file())
        with self._env():
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--format", "both", "--no-registry",
            ])
            self.assertEqual(code, 0, err)
            self.assertIn("External JSON report:", out)
            state = self.base / "state"
            json_reports = list(state.rglob("intelligence-*.json"))
            markdown_reports = list(state.rglob("intelligence-*.md"))
            self.assertEqual(len(json_reports), 1)
            self.assertEqual(len(markdown_reports), 1)
            json.loads(json_reports[0].read_text(encoding="utf-8"))
        after = sorted(path.relative_to(self.root).as_posix()
                       for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(before, after)

    def test_project_name_and_registry_target_resolution(self):
        with self._env():
            code, _, err = run_cli(["projects", "add", str(self.root), "--name", "demo"])
            self.assertEqual(code, 0, err)
            code, out, err = run_cli([
                "intelligence", "analyze", "--project", "demo",
                "--format", "json", "--no-registry",
            ])
            self.assertEqual(code, 0, err)
            data = json.loads(out)
            self.assertEqual(data["project"]["identity"]["source"], "fingerprint")

            code, out, err = run_cli([
                "intelligence", "analyze", "not-a-path", "--path", str(self.root),
                "--format", "json", "--no-registry",
            ])
            self.assertEqual(code, 0, err)

    def test_registry_project_id_target_is_explicit(self):
        project_id = str(uuid4())
        with patch("ogx.cli.resolve_registered_project",
                   return_value=(self.root.resolve(), project_id, "demo")):
            code, out, err = run_cli([
                "intelligence", "analyze", "external-reference",
                "--format", "json", "--no-registry",
            ])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["project"]["identity"]["source"], "explicit")
        self.assertEqual(data["project"]["project_id"], project_id)

    def test_errors_are_clean_and_nonzero(self):
        with self._env():
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.base / "missing"),
                "--no-registry",
            ])
            self.assertEqual(code, 2)
            self.assertIn("Intelligence analysis failed", err)
            self.assertNotIn("Traceback", err)
            code, _, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--max-files", "0", "--no-registry",
            ])
            self.assertEqual(code, 2)
            self.assertIn("max_files", err)

    def test_secret_values_never_reach_cli_output(self):
        write(self.root, ".env", "SECRET_CANARY=cliCanaryValue456\n")
        with self._env():
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--format", "json", "--no-registry",
            ])
            self.assertEqual(code, 0, err)
            self.assertNotIn("cliCanaryValue456", out)
            self.assertNotIn("cliCanaryValue456", err)

    def test_explicit_exclusion_is_respected(self):
        write(self.root, "private/secret.py", "print('hidden')\n")
        with self._env():
            code, out, err = run_cli([
                "intelligence", "analyze", "--path", str(self.root),
                "--exclude", "private", "--format", "json", "--no-registry",
            ])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertNotIn("private/secret.py", {item["path"] for item in data["structure"]["tree"]})


if __name__ == "__main__":
    unittest.main()
