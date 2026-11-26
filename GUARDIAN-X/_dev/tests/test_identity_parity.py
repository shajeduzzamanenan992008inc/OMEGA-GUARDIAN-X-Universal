"""Canonical/drop-in parity tests for the drop-in distribution (P0).

Run with::

    PYTHONPATH=core python -m unittest discover -s _dev/tests

The drop-in distribution has **no** identity implementation today (design
section 11.1 records this divergence). P0 therefore makes the gap executable
rather than pretending parity: these tests load the same shared fixtures the
canonical suite uses, verify that the frozen golden values are intact, and assert
that:

* the fixture cannot claim drop-in applicability for behavior the drop-in does
  not implement (this is the gate that forces P1/P2 to update the fixture);
* the drop-in behavior that *is* shared - state isolation and the case-fold
  collision in the state key - matches the recorded expectation;
* a drop-in run never creates, rewrites or deletes an identity file.

Reference: GUARDIAN-X/docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md sections 11.2,
11.4, 11.6 and 13.3.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
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
VECTORS_DOC = support.load_json(support.IDENTITY_VECTORS_PATH)
SCENARIOS_DOC = support.load_json(support.RELOCATION_SCENARIOS_PATH)


def _load_init_module(host_root: Path, module_name: str):
    """Load ``init.py`` with a controlled HOST_ROOT.

    ``init.py`` resolves ``HOST_ROOT`` at import time from ``OMEGA_HOST_ROOT``, so
    the environment is patched before the module is executed.
    """
    path = ROOT / "init.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise AssertionError(f"cannot load init.py from {path}")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"OMEGA_HOST_ROOT": str(host_root), "OGX_HOME": ""}):
        spec.loader.exec_module(module)
    return module


class SharedFixtureTests(unittest.TestCase):
    """Both distributions must consume the same, intact fixtures."""

    def test_identity_fixture_is_reachable_and_valid(self):
        identifiers = support.validate_identity_document(VECTORS_DOC)
        self.assertGreaterEqual(len(identifiers), 20)

    def test_golden_values_match_the_recorded_rules(self):
        # Same oracle the canonical suite runs, from the drop-in distribution.
        support.assert_golden_consistency(VECTORS_DOC)

    def test_relocation_fixture_is_valid(self):
        document = support.validate_relocation_document(SCENARIOS_DOC)
        rows = sorted(row["matrix_row"] for row in document["scenarios"])
        self.assertEqual(rows, list(range(1, 23)))

    def test_fixture_lives_outside_both_distributions(self):
        for path in (support.IDENTITY_VECTORS_PATH, support.RELOCATION_SCENARIOS_PATH):
            relative = path.resolve().relative_to(ROOT.resolve())
            self.assertEqual(relative.parts[0], "_dev", f"{path} must not ship in a distribution")


class RecordedDistributionGapTests(unittest.TestCase):
    """The drop-in has no identity surface; the fixture must say so, not hide it."""

    def test_dropin_identity_status_is_not_implemented(self):
        self.assertEqual(VECTORS_DOC["implementations"]["drop-in"]["status"], "not-implemented")
        self.assertIsNone(VECTORS_DOC["implementations"]["drop-in"]["identity_module"])

    def test_no_vector_claims_an_unimplemented_distribution(self):
        # Parity gate: enabling drop-in identity requires flipping both the status
        # and the per-vector applicability in the same change set.
        for name, implementation in VECTORS_DOC["implementations"].items():
            claims = [v["id"] for v in VECTORS_DOC["vectors"] if name in v["applies_to"]]
            with self.subTest(implementation=name, status=implementation["status"]):
                if implementation["status"] == "not-implemented":
                    self.assertEqual(claims, [], f"{name} claims {claims} while unimplemented")
                else:
                    self.assertTrue(claims, f"{name} is implemented but runs no vectors")

    def test_canonical_runs_every_vector(self):
        claims = [v["id"] for v in VECTORS_DOC["vectors"] if "canonical" in v["applies_to"]]
        self.assertEqual(len(claims), len(VECTORS_DOC["vectors"]))
        self.assertEqual(VECTORS_DOC["implementations"]["canonical"]["status"], "implemented")

    def test_no_identity_module_is_importable_from_the_dropin(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("ogx.kernel.identity")

    def test_fixture_probes_declare_their_distribution(self):
        for probe_id, probe in SCENARIOS_DOC["probes"].items():
            with self.subTest(probe=probe_id):
                self.assertIn("applies_to", probe)
                for name in probe["applies_to"]:
                    self.assertIn(name, VECTORS_DOC["implementations"])
                if probe["kind"] == "real_media":
                    self.assertEqual(probe["applies_to"], [], "real media proves nothing automatically")


class DropinAliasTests(unittest.TestCase):
    """init.py must not advertise identity, workspace or relocation commands."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.host_root = Path(self._temp.name) / "host-root"
        self.host_root.mkdir()
        self.module = _load_init_module(self.host_root, "ogx_p0_init_aliases")

    def tearDown(self):
        self._temp.cleanup()

    def test_no_identity_or_relocation_aliases_exist(self):
        for absent in ("identity", "workspace", "relocate", "host"):
            with self.subTest(alias=absent):
                self.assertNotIn(absent, self.module.ALIASES)

    def test_alias_order_is_preserved(self):
        # P0 must not perturb the existing command surface.
        self.assertEqual(
            list(self.module.ALIASES),
            ["scan", "audit", "detect", "doctor", "guard", "proxy", "logs", "errors", "snapshot"],
        )


class DropinStateKeyTests(unittest.TestCase):
    """The one shared primitive both distributions still disagree on is the state key."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def _state_home(self, host_root: Path, name: str) -> Path:
        module = _load_init_module(host_root, name)
        return module.state_home()

    def test_state_key_uses_twelve_hex_characters(self):
        self.assertEqual(
            VECTORS_DOC["implementations"]["drop-in"]["state_key_hex_length"], 12
        )
        home = self._state_home(self.base / "Alpha", "ogx_p0_state_len")
        digest = home.name.rsplit("-", 1)[-1]
        self.assertEqual(len(digest), 12)
        int(digest, 16)

    def test_canonical_state_key_length_differs(self):
        canonical_len = VECTORS_DOC["implementations"]["canonical"]["state_key_hex_length"]
        self.assertEqual(canonical_len, 16)
        self.assertNotEqual(
            canonical_len, VECTORS_DOC["implementations"]["drop-in"]["state_key_hex_length"]
        )

    def test_distinct_hosts_get_distinct_state_homes(self):
        first = self._state_home(self.base / "alpha", "ogx_p0_state_a")
        second = self._state_home(self.base / "beta", "ogx_p0_state_b")
        self.assertNotEqual(first, second)

    def test_case_only_difference_collides(self):
        # CX-05 / T5: recorded as a collision source, not as correct behavior.
        conflict = next(c for c in SCENARIOS_DOC["conflict_scenarios"] if c["id"] == "CX-05")
        self.assertEqual(conflict["probe"], "P-CASEFOLD")
        first = self._state_home(self.base / "alpha", "ogx_p0_state_c")
        second = self._state_home(self.base / "ALPHA", "ogx_p0_state_d")
        self.assertEqual(first, second)

    def test_same_folder_name_in_two_parents_does_not_collide(self):
        first = self._state_home(self.base / "one" / "project", "ogx_p0_state_e")
        second = self._state_home(self.base / "two" / "project", "ogx_p0_state_f")
        self.assertEqual(first.name.split("-")[0], "project")
        self.assertNotEqual(first, second)

    def test_case_only_difference_in_the_leaf_folder_collides(self):
        # Linux regression: the digest was case-folded but the slug was not, so
        # `project-<d>` and `PROJECT-<d>` were siblings on case-sensitive disks.
        first = self._state_home(self.base / "alpha" / "project", "ogx_p0_state_g")
        second = self._state_home(self.base / "alpha" / "PROJECT", "ogx_p0_state_h")
        self.assertEqual(first, second)
        self.assertEqual(first.name, second.name)

    def test_case_only_difference_in_a_parent_folder_collides(self):
        first = self._state_home(self.base / "alpha" / "project", "ogx_p0_state_i")
        second = self._state_home(self.base / "ALPHA" / "project", "ogx_p0_state_j")
        self.assertEqual(first, second)
        self.assertEqual(first.name, second.name)

    def test_unicode_case_folding_collides(self):
        first = self._state_home(self.base / "stra\u00dfe", "ogx_p0_state_k")
        second = self._state_home(self.base / "STRASSE", "ogx_p0_state_l")
        self.assertEqual(first, second)

    def test_distinct_names_remain_distinct(self):
        first = self._state_home(self.base / "alpha" / "project", "ogx_p0_state_m")
        second = self._state_home(self.base / "alpha" / "project2", "ogx_p0_state_n")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.name, second.name)

    def test_repeated_calls_are_deterministic(self):
        host = self.base / "Alpha" / "Project"
        module = _load_init_module(host, "ogx_p0_state_deterministic")
        first = module.state_home()
        self.assertEqual(module.state_home(), first)
        self.assertEqual(module.state_home(), first)

    def test_state_home_name_is_the_folded_slug_plus_the_twelve_hex_digest(self):
        host = self.base / "Alpha" / "Project"
        home = self._state_home(host, "ogx_p0_state_shape")
        digest = hashlib.sha256(str(host.resolve()).casefold().encode("utf-8")).hexdigest()[:12]
        self.assertEqual(home.name, f"project-{digest}")
        self.assertEqual(len(home.name.rsplit("-", 1)[-1]), 12)

    def test_canonical_and_dropin_derivations_agree_on_the_same_key(self):
        """Parity evidence: one casefolded digest, one folded slug, only the
        documented truncation length (16 vs 12 hex) differs."""
        host = self.base / "alpha" / "Project"
        home = self._state_home(host, "ogx_p0_state_parity")
        canonical_digest = hashlib.sha256(
            str(host.resolve()).casefold().encode("utf-8")
        ).hexdigest()
        self.assertEqual(home.name, f"project-{canonical_digest[:12]}")
        self.assertTrue(canonical_digest.startswith(home.name.rsplit("-", 1)[-1]))


class DropinStateIsolationTests(unittest.TestCase):
    """CX-06: the drop-in refuses a state root inside the host tree."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.host_root = self.base / "host-root"
        self.host_root.mkdir()

    def tearDown(self):
        self._temp.cleanup()

    def test_state_root_inside_the_host_is_refused(self):
        module = _load_init_module(self.host_root, "ogx_p0_isolation")
        with mock.patch.dict(os.environ, {"OGX_HOME": str(self.host_root / "state")}):
            with self.assertRaises(SystemExit):
                module._prepare_env(force=False)

    def test_state_root_outside_the_host_is_accepted(self):
        module = _load_init_module(self.host_root, "ogx_p0_isolation_ok")
        outside = self.base / "state-home"
        with mock.patch.dict(os.environ, {"OGX_HOME": str(outside)}):
            home = module._prepare_env(force=False)
        self.assertTrue(home.is_dir())
        self.assertEqual(home, outside)


class DropinIdentityFilePreservationTests(unittest.TestCase):
    """A drop-in run must never create, rewrite or delete a canonical identity file."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        self.host_root = self.base / "host-root"
        self.host_root.mkdir()

    def tearDown(self):
        self._temp.cleanup()

    def _run_status(self) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "OMEGA_HOST_ROOT": str(self.host_root),
            "OGX_HOME": str(self.base / "state-home"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            [sys.executable, str(ROOT / "init.py"), "status"],
            cwd=str(self.host_root),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            check=False,
        )

    def test_status_succeeds_without_an_identity_file(self):
        result = self._run_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.host_root / ".guardian-x" / "identity.json").exists())
        self.assertFalse((self.host_root / ".guardian-x").exists())

    def test_existing_identity_file_is_left_byte_identical(self):
        marker = self.host_root / ".guardian-x" / "identity.json"
        marker.parent.mkdir()
        payload = {
            "schema_version": "1.0",
            "project_id": "3f2a1c4e-9b7d-4a15-8c3e-2d6f5a1b0c94",
            "project_name": "Preserved",
            "description": "",
            "created_at": "2026-01-02T03:04:05+00:00",
            "created_by": "owner@example.com",
            "ogx_version": "0.4.0",
            "identity_hash": "sha256:" + "0" * 64,
        }
        original = json.dumps(payload, indent=2) + "\n"
        marker.write_text(original, encoding="utf-8", newline="")
        before = marker.read_bytes()

        result = self._run_status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_bytes(), before, "the drop-in must not touch identity.json")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
