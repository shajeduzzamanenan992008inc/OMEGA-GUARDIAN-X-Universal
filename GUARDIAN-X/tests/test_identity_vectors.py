"""Golden-vector tests for the canonical identity implementation (P0).

These tests execute the shared fixture ``_dev/fixtures/identity_vectors.json``
against ``ogx.pap.identity``. The fixture is the single source of truth for
identity canonicalization; the drop-in suite loads the same file. A failure here
means the canonical implementation no longer matches the recorded contract, and
the golden file must not be regenerated to hide it.

Reference: GUARDIAN-X/docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md sections 6.1,
11.2, 11.4 and 13.3.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ogx.pap import exceptions as identity_exceptions
from ogx.pap.identity import ProjectIdentity, read_identity
from ogx.pap.identity_schema import validate_identity_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "_dev" / "fixtures"


def _load_fixture_support():
    """Load the shared, stdlib-only fixture helper without touching sys.path."""
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
VECTORS = [v for v in VECTORS_DOC["vectors"] if "canonical" in v["applies_to"]]


def _identity_from(data: dict[str, Any]) -> ProjectIdentity:
    validated = validate_identity_json(data)
    return ProjectIdentity(
        schema_version=validated["schema_version"],
        project_id=validated["project_id"],
        project_name=validated["project_name"],
        description=validated.get("description", ""),
        created_at=validated["created_at"],
        created_by=validated["created_by"],
        ogx_version=validated["ogx_version"],
        identity_hash=validated.get("identity_hash", ""),
    )


def _write_identity_file(root: Path, text: str) -> Path:
    target = root / ".guardian-x" / "identity.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return target


class FixtureIntegrityTests(unittest.TestCase):
    """The fixture itself must be loadable, complete and internally consistent."""

    def test_document_structure_is_valid(self):
        identifiers = support.validate_identity_document(VECTORS_DOC)
        self.assertGreaterEqual(len(identifiers), 20)
        self.assertEqual(identifiers, sorted(v["id"] for v in VECTORS_DOC["vectors"]))

    def test_golden_bytes_and_hashes_match_the_recorded_rules(self):
        # Independent oracle: proves no stored hash was hand-edited.
        support.assert_golden_consistency(VECTORS_DOC)

    def test_required_case_coverage(self):
        accepted = {v["id"] for v in VECTORS if v["expect"] == "accept"}
        rejected = {v["id"] for v in VECTORS if v["expect"] == "reject"}
        required_accept = {
            "V01-minimal",          # minimal valid
            "V03-max-description",  # maximal valid
            "V05-unicode",          # unicode
            "V26-crlf-file",        # line endings do not matter
        }
        required_reject = {
            "V09-uuid-v1", "V10-uuid-v5", "V11-uuid-malformed",
            "V12-missing-project-name", "V13-unknown-field",
            "V17-timestamp-no-tz", "V18-timestamp-not-iso",
            "V19-secret-like", "V24-oversize-file", "V25-hash-mismatch",
        }
        self.assertTrue(required_accept <= accepted, required_accept - accepted)
        self.assertTrue(required_reject <= rejected, required_reject - rejected)

    def test_only_the_canonical_distribution_claims_identity_vectors(self):
        # P0 records the real distribution gap instead of pretending parity.
        statuses = {name: entry["status"] for name, entry in VECTORS_DOC["implementations"].items()}
        self.assertEqual(statuses["canonical"], "implemented")
        self.assertEqual(statuses["drop-in"], "not-implemented")
        for vector in VECTORS_DOC["vectors"]:
            self.assertEqual(vector["applies_to"], ["canonical"], vector["id"])


class AcceptedVectorTests(unittest.TestCase):
    """Each accepted vector must validate, canonicalize and hash exactly."""

    def test_accept_vectors(self):
        for vector in VECTORS:
            if vector["expect"] != "accept" or vector["check"] != "validate_json":
                continue
            with self.subTest(vector=vector["id"]):
                identity = _identity_from(vector["input"])
                self.assertEqual(
                    identity.to_canonical_json(),
                    vector["canonical_json"].encode("utf-8"),
                )
                self.assertEqual(identity.compute_hash(), vector["identity_hash"])
                self.assertTrue(identity.with_hash().verify_hash())

    def test_identity_hash_is_stable_across_repeated_computation(self):
        for vector in VECTORS:
            if vector["expect"] != "accept" or vector["check"] != "validate_json":
                continue
            with self.subTest(vector=vector["id"]):
                first = _identity_from(vector["input"]).compute_hash()
                second = _identity_from(vector["input"]).compute_hash()
                self.assertEqual(first, second)
                self.assertEqual(first, vector["identity_hash"])


class RejectedVectorTests(unittest.TestCase):
    """Each rejected vector must fail with the recorded error category."""

    def test_validate_json_rejections(self):
        for vector in VECTORS:
            if vector["expect"] != "reject" or vector["check"] != "validate_json":
                continue
            with self.subTest(vector=vector["id"]):
                error_type = getattr(identity_exceptions, vector["error_class"])
                with self.assertRaises(error_type) as caught:
                    validate_identity_json(vector["input"])
                self.assertEqual(getattr(caught.exception, "field", None), vector["error_field"])

    def test_rejected_input_never_produces_a_hash(self):
        for vector in VECTORS:
            if vector["expect"] != "reject" or vector["check"] != "validate_json":
                continue
            with self.subTest(vector=vector["id"]):
                with self.assertRaises(identity_exceptions.IdentityError):
                    _identity_from(vector["input"])


class FileLevelVectorTests(unittest.TestCase):
    """File-size, hash-integrity and line-ending vectors are exercised on disk."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def _vector(self, vector_id: str) -> dict[str, Any]:
        for vector in VECTORS:
            if vector["id"] == vector_id:
                return vector
        raise AssertionError(f"missing vector {vector_id}")

    def test_oversize_file_is_refused(self):
        vector = self._vector("V24-oversize-file")
        text = json.dumps(vector["file_json"]) + " " * vector["trailing_whitespace_bytes"]
        path = _write_identity_file(self.root, text)
        self.assertGreater(path.stat().st_size, 64 * 1024)
        error_type = getattr(identity_exceptions, vector["error_class"])
        with self.assertRaises(error_type):
            read_identity(self.root)

    def test_hash_mismatch_is_refused_without_repair(self):
        vector = self._vector("V25-hash-mismatch")
        text = json.dumps(vector["file_json"], indent=2)
        path = _write_identity_file(self.root, text)
        before = path.read_bytes()
        error_type = getattr(identity_exceptions, vector["error_class"])
        with self.assertRaises(error_type):
            read_identity(self.root)
        self.assertEqual(path.read_bytes(), before, "a corrupted identity must never be rewritten")

    def test_missing_hash_is_refused(self):
        vector = self._vector("V27-missing-hash")
        _write_identity_file(self.root, json.dumps(vector["file_json"], indent=2))
        error_type = getattr(identity_exceptions, vector["error_class"])
        with self.assertRaises(error_type):
            read_identity(self.root)

    def test_crlf_file_reads_with_the_same_identity_hash(self):
        vector = self._vector("V26-crlf-file")
        text = json.dumps(vector["file_json"], indent=vector["indent"]).replace("\n", "\r\n") + "\r\n"
        self.assertIn("\r\n", text)
        path = _write_identity_file(self.root, text)
        before = path.read_bytes()
        identity = read_identity(self.root)
        self.assertEqual(identity.identity_hash, vector["identity_hash"])
        self.assertEqual(identity.to_canonical_json(), vector["canonical_json"].encode("utf-8"))
        self.assertEqual(path.read_bytes(), before, "reading must not rewrite the identity file")

    def test_crlf_and_lf_files_share_one_identity(self):
        crlf_vector = self._vector("V26-crlf-file")
        minimal = self._vector("V01-minimal")
        crlf_text = json.dumps(crlf_vector["file_json"], indent=4).replace("\n", "\r\n")
        _write_identity_file(self.root, crlf_text)
        from_crlf = read_identity(self.root).identity_hash

        second = Path(self._temp.name + "-lf")
        second.mkdir()
        try:
            _write_identity_file(second, json.dumps(crlf_vector["file_json"], indent=2))
            from_lf = read_identity(second).identity_hash
        finally:
            for child in sorted(second.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
            second.rmdir()

        self.assertEqual(from_crlf, from_lf)
        self.assertEqual(from_crlf, minimal["identity_hash"])


class RecordedDivergenceTests(unittest.TestCase):
    """Freeze the canonicalization hazards P0 found, so later phases must decide them."""

    def test_uuid_case_changes_the_identity_hash(self):
        lower = next(v for v in VECTORS if v["id"] == "V01-minimal")
        upper = next(v for v in VECTORS if v["id"] == "V06-uppercase-uuid")
        self.assertEqual(
            lower["input"]["project_id"].lower(), upper["input"]["project_id"].lower()
        )
        self.assertNotEqual(lower["identity_hash"], upper["identity_hash"])

    def test_timestamp_spelling_changes_the_identity_hash(self):
        minimal = next(v for v in VECTORS if v["id"] == "V01-minimal")
        zulu = next(v for v in VECTORS if v["id"] == "V07-zulu-timestamp")
        offset = next(v for v in VECTORS if v["id"] == "V08-offset-timestamp")
        hashes = {minimal["identity_hash"], zulu["identity_hash"], offset["identity_hash"]}
        self.assertEqual(len(hashes), 3, "timestamps are not normalized to UTC today")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
