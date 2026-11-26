"""Relocation, conflict and state-isolation fixtures for the canonical distribution (P0).

Every test here is a *characterization* of `24c954e` behavior driven by the shared
fixture ``_dev/fixtures/relocation_scenarios.json``. Where the design requires a
transition the code does not implement, the test asserts the absence explicitly
(``NOT_IMPLEMENTED``) so a later phase must flip the expectation deliberately
instead of discovering the gap by accident.

P0 does not change production behavior, so no test may require a new command.

Reference: GUARDIAN-X/docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md sections 8, 9,
13.2 and 15.3 (D4, D5, D6).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import UUID, uuid4

from ogx.cli import _external_report_dir, _prepare_report_dir
from ogx.pap.identity import create_identity, read_identity, recover_identity
from ogx.pap.exceptions import IdentityError, IdentityMissingError
from ogx.platform.identity import deterministic_repository_id, normalize_repository_url
from ogx.registry.v2 import RegistryV2

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "_dev" / "fixtures"


def _load_fixture_support():
    path = FIXTURES / "vectors.py"
    spec = importlib.util.spec_from_file_location("ogx_p0_fixture_support", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise AssertionError(f"cannot load fixture support from {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep .pyc out of the fixture directory
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


support = _load_fixture_support()
SCENARIOS_DOC = support.validate_relocation_document(
    support.load_json(support.RELOCATION_SCENARIOS_PATH)
)
SCENARIOS = {row["id"]: row for row in SCENARIOS_DOC["scenarios"]}
CONFLICTS = {row["id"]: row for row in SCENARIOS_DOC["conflict_scenarios"]}


def _scenario(scenario_id: str) -> dict[str, Any]:
    try:
        return SCENARIOS[scenario_id]
    except KeyError:  # pragma: no cover - defensive
        raise AssertionError(f"fixture is missing scenario {scenario_id}") from None


class FixtureCoverageTests(unittest.TestCase):
    """The fixture must describe the whole matrix and obey the design's own rules."""

    def test_all_twenty_two_matrix_rows_are_present(self):
        rows = sorted(row["matrix_row"] for row in SCENARIOS.values())
        self.assertEqual(rows, list(range(1, 23)))

    def test_project_id_is_immutable_except_for_a_fork_or_an_identityless_clone(self):
        # Design rule 8.2(1) protects an *existing* identity. Row 10 starts from a
        # tree with no identity file and row 11 is an explicit fork, so both mint a new id.
        no_prior_identity = {10, 11}
        for row in SCENARIOS.values():
            with self.subTest(scenario=row["id"], row=row["matrix_row"]):
                if row["matrix_row"] in no_prior_identity:
                    self.assertEqual(row["expect"]["project_id"], "new")
                else:
                    self.assertEqual(row["expect"]["project_id"], "same")

    def test_workspace_id_retained_only_for_a_move(self):
        for row in SCENARIOS.values():
            if row["operation"] != "move":
                continue
            with self.subTest(scenario=row["id"]):
                self.assertEqual(row["expect"]["workspace_id"], "retain")

    def test_copy_clone_and_fork_never_reuse_a_workspace_id(self):
        for row in SCENARIOS.values():
            if row["operation"] not in {"copy", "clone", "fork", "open_remote", "host_reinstall"}:
                continue
            with self.subTest(scenario=row["id"]):
                self.assertEqual(row["expect"]["workspace_id"], "new")

    def test_real_media_rows_are_marked_not_run(self):
        real_media = [row for row in SCENARIOS.values() if row["requires_real_media"]]
        self.assertTrue(real_media, "at least one row must be marked as requiring real media")
        for row in real_media:
            with self.subTest(scenario=row["id"]):
                self.assertEqual(row["observation"], "REAL_MEDIA")
                self.assertNotEqual(row["probe"], "P-IDENTITY-PRESERVED")

    def test_not_implemented_rows_name_a_missing_surface(self):
        for row in SCENARIOS.values():
            if row["observation"] != "NOT_IMPLEMENTED":
                continue
            with self.subTest(scenario=row["id"]):
                self.assertEqual(row["probe"], "P-MISSING-SURFACE")

    def test_invariants_are_recorded(self):
        self.assertGreaterEqual(len(SCENARIOS_DOC["invariants"]), 8)


class IdentityPreservedTests(unittest.TestCase):
    """RX-01, RX-02, RX-06, RX-12, RX-13: the identity survives the filesystem operation."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.root = self.base / "project-one"
        self.root.mkdir()
        self.identity = create_identity(
            self.root,
            project_name="Relocation Sample",
            description="P0 relocation fixture",
            created_by="owner@example.com",
            ogx_version="0.8.0a1",
        )
        self.marker = self.root / ".guardian-x" / "identity.json"
        self.original_bytes = self.marker.read_bytes()

    def tearDown(self):
        self._temp.cleanup()

    def _assert_identity_intact(self, location: Path) -> None:
        restored = read_identity(location)
        self.assertEqual(restored.project_id, self.identity.project_id)
        self.assertEqual(restored.identity_hash, self.identity.identity_hash)
        self.assertEqual((location / ".guardian-x" / "identity.json").read_bytes(), self.original_bytes)

    def test_rename_in_place_preserves_the_identity(self):
        self.assertTrue(_scenario("RX-01")["observation"] == "EXECUTABLE")
        moved = self.base / "project-one-renamed"
        self.root.rename(moved)
        self._assert_identity_intact(moved)

    def test_move_to_another_directory_preserves_the_identity(self):
        destination = self.base / "elsewhere" / "project-one"
        destination.parent.mkdir()
        shutil.move(str(self.root), str(destination))
        self._assert_identity_intact(destination)

    def test_copy_duplicates_the_project_id_at_the_new_location(self):
        # RX-06 / CX-01: this is the current behavior, recorded rather than endorsed.
        destination = self.base / "project-one-copy"
        shutil.copytree(self.root, destination)
        self._assert_identity_intact(destination)
        self.assertEqual(read_identity(destination).project_id, read_identity(self.root).project_id)

    def test_restore_at_the_original_and_a_new_path_both_verify(self):
        backup = self.base / "backup"
        shutil.copytree(self.root, backup)
        shutil.rmtree(self.root)
        shutil.copytree(backup, self.root)
        self._assert_identity_intact(self.root)

        restored_elsewhere = self.base / "restored" / "project-one"
        restored_elsewhere.parent.mkdir()
        shutil.copytree(backup, restored_elsewhere)
        self._assert_identity_intact(restored_elsewhere)

    def test_copy_preserves_the_identity_even_when_the_directory_is_renamed(self):
        destination = self.base / "different-name"
        shutil.copytree(self.root, destination)
        restored = read_identity(destination)
        self.assertEqual(restored.project_id, self.identity.project_id)
        self.assertEqual(restored.project_name, "Relocation Sample")


class IdentityAbsentTests(unittest.TestCase):
    """RX-10: a tree without an identity file is not silently adopted."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "cloned"
        self.root.mkdir()

    def tearDown(self):
        self._temp.cleanup()

    def test_read_identity_raises_missing(self):
        self.assertEqual(_scenario("RX-10")["probe"], "P-IDENTITY-ABSENT")
        with self.assertRaises(IdentityMissingError):
            read_identity(self.root)

    def test_recovery_refuses_without_confirmation(self):
        with self.assertRaises(IdentityError):
            recover_identity(self.root, confirm=False)
        self.assertFalse((self.root / ".guardian-x" / "identity.json").exists())

    def test_recovery_with_confirmation_mints_a_new_identity(self):
        # P-IDENTITY-MINTED: no registry or manifest evidence is consulted today.
        identity = recover_identity(self.root, confirm=True)
        self.assertIsInstance(UUID(str(identity.project_id)), UUID)
        self.assertEqual(identity.project_id[14], "4", "a new identity must be a UUID v4")
        self.assertTrue(identity.verify_hash())
        self.assertEqual(read_identity(self.root).project_id, identity.project_id)

    def test_recovery_with_confirmation_is_stable_on_second_call(self):
        first = recover_identity(self.root, confirm=True)
        second = recover_identity(self.root, confirm=True)
        self.assertEqual(first.project_id, second.project_id)


class MissingSurfaceTests(unittest.TestCase):
    """RX-08/09/11/15-17/20-22: the required command surface does not exist yet.

    DOCUMENTED COMPATIBILITY DECISION (post-P0): ``identity explain``,
    ``identity fork`` and the ``relocate`` domain were implemented during the
    consolidated completion work, so the three assertions that recorded their
    *absence* are inverted here into assertions that they exist and work.
    The P0 fixtures under ``_dev/fixtures`` are left untouched: they record the
    P0 baseline as historical evidence, and the completion report records this
    decision explicitly. No golden identity vector changed.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self._create_identity()

    def tearDown(self):
        self._temp.cleanup()

    def _run_cli(self, *arguments: str) -> subprocess.CompletedProcess:
        # Keep CLI state inside the temporary directory: a test must never
        # write into the developer's real OGX state root.
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OGX_HOME": str(self.base / "state"),
        }
        return subprocess.run(
            [sys.executable, "-m", "ogx", *arguments],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            check=False,
        )

    def _create_identity(self) -> None:
        created = self._run_cli("identity", "create", str(self.root), "--name", "P0 Project")
        self.assertEqual(created.returncode, 0, created.stderr)

    def test_identity_fork_command_exists(self):
        destination = self.base / "fork"
        result = self._run_cli(
            "identity", "fork", str(self.root),
            "--destination", str(destination), "--confirm", "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotEqual(payload["project_id"], payload["parent_project_id"])

    def test_identity_explain_command_exists(self):
        result = self._run_cli("identity", "explain", str(self.root), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["classification"], "verified")

    def test_relocate_domain_exists(self):
        destination = self.base / "relocated"
        result = self._run_cli(
            "relocate", "plan", str(self.root), str(destination), "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["operation"], "move")

    def test_every_not_implemented_row_maps_to_a_missing_surface(self):
        rows = [row for row in SCENARIOS.values() if row["observation"] == "NOT_IMPLEMENTED"]
        self.assertGreaterEqual(len(rows), 8)
        for row in rows:
            with self.subTest(scenario=row["id"], operation=row["operation"]):
                self.assertEqual(row["probe"], "P-MISSING-SURFACE")


class RepositoryUrlTests(unittest.TestCase):
    """RX-14: a URL spelling change does not create a new project or repository."""

    def test_https_and_ssh_share_one_normalized_key(self):
        self.assertEqual(_scenario("RX-14")["probe"], "P-REPO-URL")
        https = normalize_repository_url("https://github.com/owner/repo.git")
        ssh = normalize_repository_url("git@github.com:owner/repo.git")
        self.assertEqual(https, ssh)
        self.assertEqual(https, "github.com/owner/repo")

    def test_deterministic_repository_id_is_stable_across_spellings(self):
        https = normalize_repository_url("https://github.com/owner/repo.git")
        ssh = normalize_repository_url("git@github.com:owner/repo.git")
        self.assertEqual(
            deterministic_repository_id("github", https),
            deterministic_repository_id("github", ssh),
        )

    def test_credentials_in_a_url_are_refused(self):
        with self.assertRaises(ValueError):
            normalize_repository_url("https://user:secret@github.com/owner/repo.git")


class StateIsolationTests(unittest.TestCase):
    """RX-18, CX-04, CX-06: state stays outside the project and is keyed per location."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def test_state_root_inside_the_project_is_refused(self):
        project = self.base / "project"
        project.mkdir()
        inside = project / "state"
        with mock.patch.dict(os.environ, {"OGX_HOME": str(inside)}):
            with self.assertRaises(ValueError):
                _prepare_report_dir(project)

    def test_state_root_outside_the_project_is_accepted(self):
        project = self.base / "project"
        project.mkdir()
        outside = self.base / "state-home"
        with mock.patch.dict(os.environ, {"OGX_HOME": str(outside)}):
            report_dir = _prepare_report_dir(project)
        self.assertTrue(report_dir.is_dir())
        self.assertFalse(report_dir.resolve().is_relative_to(project.resolve()))

    def test_distinct_locations_get_distinct_state_directories(self):
        first = self.base / "alpha" / "project"
        second = self.base / "beta" / "project"
        with mock.patch.dict(os.environ, {"OGX_HOME": str(self.base / "state-home")}):
            self.assertNotEqual(_external_report_dir(first), _external_report_dir(second))

    def test_same_folder_name_in_two_parents_does_not_collide(self):
        # CX-04 / RT-55
        self.assertEqual(CONFLICTS["CX-04"]["probe"], "P-STATE-ISOLATION")
        first = self.base / "alpha" / "project"
        second = self.base / "beta" / "project"
        with mock.patch.dict(os.environ, {"OGX_HOME": str(self.base / "state-home")}):
            self.assertTrue(_external_report_dir(first).parent.name.startswith("project-"))
            self.assertNotEqual(_external_report_dir(first).parent.name,
                                _external_report_dir(second).parent.name)

    def test_symlinked_state_root_is_refused(self):
        project = self.base / "project"
        project.mkdir()
        real = self.base / "real-state"
        real.mkdir()
        link = self.base / "linked-state"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
            self.skipTest("symlink creation is unavailable on this platform")
        with mock.patch.dict(os.environ, {"OGX_HOME": str(link)}):
            with self.assertRaises(ValueError):
                _prepare_report_dir(project)


class StateKeyCollisionTests(unittest.TestCase):
    """CX-05: case folding is a live collision source for the state key."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def test_case_only_difference_maps_to_one_state_directory(self):
        self.assertEqual(CONFLICTS["CX-05"]["probe"], "P-CASEFOLD")
        lower = self.base / "alpha" / "project"
        upper = self.base / "alpha" / "PROJECT"
        with mock.patch.dict(os.environ, {"OGX_HOME": str(self.base / "state-home")}):
            self.assertEqual(_external_report_dir(lower), _external_report_dir(upper))

    def test_state_key_uses_a_sixteen_hex_character_digest(self):
        # Recorded divergence: drop-in uses 12 hex characters (see the shared fixture).
        target = self.base / "alpha" / "project"
        with mock.patch.dict(os.environ, {"OGX_HOME": str(self.base / "state-home")}):
            name = _external_report_dir(target).parent.name
        digest = name.rsplit("-", 1)[-1]
        self.assertEqual(len(digest), 16)
        int(digest, 16)  # must be hexadecimal

    def test_collision_expectation_declares_no_detection_yet(self):
        self.assertFalse(CONFLICTS["CX-05"]["expect"]["conflict_detected"])


class HostIdentityTests(unittest.TestCase):
    """CX-03 / RT-16: host registration idempotency.

    DOCUMENTED COMPATIBILITY DECISION (post-P0): registration is now idempotent
    for the same (host type, identifier) pair, so the assertion that recorded
    duplicate host ids is inverted. The CX-03 fixture row still records the P0
    baseline and is not rewritten.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.state = self.base / "state-home"
        self.host_root = self.base / "host-root"
        self.host_root.mkdir()

    def tearDown(self):
        self._temp.cleanup()

    def _register(self) -> dict[str, Any]:
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "OGX_HOME": str(self.state),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            [
                sys.executable, "-m", "ogx", "host", "register",
                "--type", "local_filesystem",
                "--provider", "local",
                "--name", "P0 Host",
                "--identifier", str(self.host_root),
                "--json",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_registering_the_same_local_host_twice_reuses_one_host_id(self):
        self.assertEqual(CONFLICTS["CX-03"]["probe"], "P-HOST-PER-CALL")
        first = self._register()
        second = self._register()
        self.assertEqual(first["host_id"], second["host_id"])
        self.assertEqual(first["normalized_identifier"], second["normalized_identifier"])
        self.assertEqual(second["registration"], "updated")
        self.assertEqual(first["machine_host_id"], second["machine_host_id"])

    def test_fixture_records_that_the_conflict_is_not_detected(self):
        self.assertFalse(CONFLICTS["CX-03"]["expect"]["conflict_detected"])


class WorkspaceConflictTests(unittest.TestCase):
    """CX-01, CX-02 / RT-24, RT-25, RT-27: workspace conflicts are not detected today."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.registry = RegistryV2(self.base / "registry.db")

    def tearDown(self):
        self.registry.close()
        self._temp.cleanup()

    def test_two_locations_can_belong_to_one_project(self):
        project_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Two Locations")
        first = self.registry.register_workspace(
            workspace_id=uuid4(), project_id=project_id,
            location_reference="/work/a", normalized_location="/work/a",
        )
        second = self.registry.register_workspace(
            workspace_id=uuid4(), project_id=project_id,
            location_reference="/work/b", normalized_location="/work/b",
        )
        self.assertNotEqual(first.workspace_id, second.workspace_id)
        self.assertEqual({w.workspace_id for w in self.registry.list_workspaces(project_id)},
                         {first.workspace_id, second.workspace_id})

    def test_one_location_can_be_reused_by_a_different_project(self):
        self.assertEqual(CONFLICTS["CX-02"]["probe"], "P-LOCATION-REUSE")
        first_project = uuid4()
        second_project = uuid4()
        self.registry.register_project(project_id=first_project, canonical_name="First")
        self.registry.register_project(project_id=second_project, canonical_name="Second")
        first_workspace = self.registry.register_workspace(
            workspace_id=uuid4(), project_id=first_project,
            location_reference="/shared/path", normalized_location="/shared/path",
        )
        second_workspace = self.registry.register_workspace(
            workspace_id=uuid4(), project_id=second_project,
            location_reference="/shared/path", normalized_location="/shared/path",
        )
        self.assertNotEqual(first_workspace.workspace_id, second_workspace.workspace_id)
        found = self.registry.find_workspace_by_normalized_location("/shared/path")
        self.assertIsNotNone(found)
        self.assertIn(found.workspace_id, {first_workspace.workspace_id, second_workspace.workspace_id})

    def test_relocation_updates_a_location_without_changing_the_project(self):
        # RT-30 exists and must keep passing while P0 adds its scenario coverage.
        project_id = uuid4()
        workspace_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Relocate")
        self.registry.register_workspace(
            workspace_id=workspace_id, project_id=project_id,
            location_reference="/old/path", normalized_location="/old/path",
        )
        updated = self.registry.update_workspace_location(
            workspace_id=workspace_id,
            location_reference="/new/path",
            normalized_location="/new/path",
        )
        self.assertEqual(updated.location_reference, "/new/path")
        self.assertEqual(updated.project_id, project_id)

    def test_missing_location_is_reported_without_deleting_state(self):
        # RT-26: no deletion path is invoked by a lookup.
        project_id = uuid4()
        workspace_id = uuid4()
        self.registry.register_project(project_id=project_id, canonical_name="Missing")
        self.registry.register_workspace(
            workspace_id=workspace_id, project_id=project_id,
            location_reference="/gone", normalized_location="/gone",
        )
        self.assertIsNone(self.registry.find_workspace_by_normalized_location("/never-registered"))
        self.assertIsNotNone(self.registry.get_workspace(workspace_id))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
