"""Shared, distribution-neutral support for the identity fixtures.

This module is deliberately *not* part of either shipped distribution. Both test
suites load it from the fixtures directory:

* ``tests/test_identity_vectors.py`` (canonical, ``pythonpath = src``)
* ``_dev/tests/test_identity_parity.py`` (drop-in, ``PYTHONPATH=core``)

It contains only the standard library so the drop-in suite can use it while
remaining stdlib-only. ``canonical_bytes`` is a *specification oracle*: it
re-derives the canonical form from the rules recorded in the design document and
in ``identity_vectors.json``, independently of either implementation. Its only
job is to prove that the frozen ``canonical_json``/``identity_hash`` pairs were
not hand-edited or silently corrupted. It is not a third production
implementation and must never be imported by shipped code.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent
IDENTITY_VECTORS_PATH = FIXTURES_DIR / "identity_vectors.json"
RELOCATION_SCENARIOS_PATH = FIXTURES_DIR / "relocation_scenarios.json"

CHECK_KINDS = frozenset({"validate_json", "read_identity", "file_size"})
EXPECTATIONS = frozenset({"accept", "reject"})
STATE_TOKENS = frozenset({"same", "new", "retain", "rebind", "none", "reject", "unchanged"})
PROBE_KINDS = frozenset(
    {
        "identity_preserved",
        "identity_absent",
        "identity_minted_on_confirm",
        "repository_url_normalized",
        "state_isolation",
        "not_implemented",
        "real_media",
        "duplicate_pid_two_locations",
        "location_reused_by_other_pid",
        "host_registered_per_call",
        "collision_case_fold",
    }
)


def load_json(path: Path) -> dict[str, Any]:
    """Load one fixture document from disk."""
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise AssertionError(f"{path.name} must contain a JSON object")
    return document


def canonical_bytes(data: dict[str, Any], rules: dict[str, Any]) -> bytes:
    """Re-derive the canonical identity bytes from the recorded rules.

    The rules come from ``identity_vectors.json`` so the oracle follows the
    documented specification rather than either implementation.
    """
    fields = list(rules["canonical_fields"])
    excluded = set(rules["excluded_fields"])
    defaulted = dict(rules["defaulted_fields"])
    payload: dict[str, Any] = {}
    for name in fields:
        if name in excluded:
            continue
        if name in data:
            payload[name] = data[name]
        elif name in defaulted:
            payload[name] = defaulted[name]
        else:
            raise AssertionError(f"vector is missing canonical field {name!r}")
    separators = tuple(rules["separators"])
    text = json.dumps(
        payload,
        ensure_ascii=rules["ensure_ascii"],
        sort_keys=rules["sort_keys"],
        separators=separators,
    )
    return text.encode(rules["encoding"])


def expected_hash(data: dict[str, Any], rules: dict[str, Any]) -> str:
    """Return the expected ``sha256:`` identity hash for one identity object."""
    digest = hashlib.sha256(canonical_bytes(data, rules)).hexdigest()
    return f"{rules['hash_prefix']}{digest}"


def validate_identity_document(document: dict[str, Any]) -> list[str]:
    """Check the shape of ``identity_vectors.json``; return the vector ids."""
    if document.get("schema_version") != "1.0":
        raise AssertionError("identity_vectors.json must declare schema_version 1.0")
    rules = document.get("canonicalization")
    if not isinstance(rules, dict):
        raise AssertionError("identity_vectors.json is missing canonicalization rules")
    for key in ("encoding", "canonical_fields", "excluded_fields", "separators",
                "sort_keys", "ensure_ascii", "hash_algorithm", "hash_prefix"):
        if key not in rules:
            raise AssertionError(f"canonicalization rule {key!r} is missing")
    if rules["hash_algorithm"] != "sha256":
        raise AssertionError("only sha256 identity hashes are supported")

    implementations = document.get("implementations")
    if not isinstance(implementations, dict):
        raise AssertionError("identity_vectors.json is missing implementations")
    for name in ("canonical", "drop-in"):
        entry = implementations.get(name)
        if not isinstance(entry, dict) or "status" not in entry:
            raise AssertionError(f"implementations.{name} is incomplete")

    vectors = document.get("vectors")
    if not isinstance(vectors, list) or not vectors:
        raise AssertionError("identity_vectors.json must contain vectors")

    seen: set[str] = set()
    for vector in vectors:
        vector_id = vector.get("id")
        if not isinstance(vector_id, str) or not vector_id:
            raise AssertionError("every vector needs a non-empty id")
        if vector_id in seen:
            raise AssertionError(f"duplicate vector id {vector_id}")
        seen.add(vector_id)
        if vector.get("check") not in CHECK_KINDS:
            raise AssertionError(f"{vector_id}: unknown check {vector.get('check')!r}")
        if vector.get("expect") not in EXPECTATIONS:
            raise AssertionError(f"{vector_id}: unknown expect {vector.get('expect')!r}")
        applies_to = vector.get("applies_to")
        if not isinstance(applies_to, list) or not applies_to:
            raise AssertionError(f"{vector_id}: applies_to must list implementations")
        unknown = set(applies_to) - set(implementations)
        if unknown:
            raise AssertionError(f"{vector_id}: unknown implementation(s) {sorted(unknown)}")
        if vector["expect"] == "accept":
            if "canonical_json" not in vector or "identity_hash" not in vector:
                raise AssertionError(f"{vector_id}: accepted vectors need canonical_json and identity_hash")
        else:
            if not vector.get("error_class"):
                raise AssertionError(f"{vector_id}: rejected vectors need error_class")
    return sorted(seen)


def assert_golden_consistency(document: dict[str, Any]) -> None:
    """Prove every frozen canonical form and hash matches the recorded rules."""
    rules = document["canonicalization"]
    for vector in document["vectors"]:
        if vector["expect"] != "accept":
            continue
        source = vector.get("input", vector.get("file_json"))
        if not isinstance(source, dict):
            raise AssertionError(f"{vector['id']}: accepted vector needs an object input")
        recomputed_bytes = canonical_bytes(source, rules)
        stored_bytes = vector["canonical_json"].encode(rules["encoding"])
        if recomputed_bytes != stored_bytes:
            raise AssertionError(
                f"{vector['id']}: canonical_json does not match the recorded rules\n"
                f"  stored   : {stored_bytes!r}\n  recomputed: {recomputed_bytes!r}"
            )
        recomputed_hash = expected_hash(source, rules)
        if recomputed_hash != vector["identity_hash"]:
            raise AssertionError(
                f"{vector['id']}: identity_hash does not match the canonical bytes\n"
                f"  stored   : {vector['identity_hash']}\n  recomputed: {recomputed_hash}"
            )


def validate_relocation_document(document: dict[str, Any]) -> dict[str, Any]:
    """Check the shape of ``relocation_scenarios.json`` and return it."""
    if document.get("schema_version") != "1.0":
        raise AssertionError("relocation_scenarios.json must declare schema_version 1.0")
    probes = document.get("probes")
    if not isinstance(probes, dict) or not probes:
        raise AssertionError("relocation_scenarios.json is missing probe definitions")
    for probe_id, probe in probes.items():
        if probe.get("kind") not in PROBE_KINDS:
            raise AssertionError(f"{probe_id}: unknown probe kind {probe.get('kind')!r}")

    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise AssertionError("relocation_scenarios.json must contain scenarios")

    seen_ids: set[str] = set()
    matrix_rows: set[int] = set()
    for scenario in scenarios:
        sid = scenario.get("id")
        if not isinstance(sid, str) or not sid:
            raise AssertionError("every scenario needs a non-empty id")
        if sid in seen_ids:
            raise AssertionError(f"duplicate scenario id {sid}")
        seen_ids.add(sid)
        row = scenario.get("matrix_row")
        if not isinstance(row, int) or not 1 <= row <= len(scenarios):
            raise AssertionError(f"{sid}: matrix_row must be an integer index")
        if row in matrix_rows:
            raise AssertionError(f"{sid}: duplicate matrix_row {row}")
        matrix_rows.add(row)
        probe_id = scenario.get("probe")
        if probe_id not in probes:
            raise AssertionError(f"{sid}: unknown probe {probe_id!r}")
        expect = scenario.get("expect")
        if not isinstance(expect, dict):
            raise AssertionError(f"{sid}: expect must be an object")
        for key in ("project_id", "host_id", "workspace_id", "state", "repository_link"):
            if expect.get(key) not in STATE_TOKENS:
                raise AssertionError(f"{sid}: expect.{key} must be one of {sorted(STATE_TOKENS)}")
        for key in ("automatic", "confirmation"):
            if not isinstance(expect.get(key), bool):
                raise AssertionError(f"{sid}: expect.{key} must be a boolean")

    conflicts = document.get("conflict_scenarios")
    if not isinstance(conflicts, list) or not conflicts:
        raise AssertionError("relocation_scenarios.json must contain conflict_scenarios")
    for conflict in conflicts:
        cid = conflict.get("id")
        if not isinstance(cid, str) or not cid:
            raise AssertionError("every conflict scenario needs a non-empty id")
        if conflict.get("probe") not in probes:
            raise AssertionError(f"{cid}: unknown probe {conflict.get('probe')!r}")
        for key, value in conflict.get("expect", {}).items():
            if key == "conflict_detected":
                if not isinstance(value, bool):
                    raise AssertionError(f"{cid}: expect.conflict_detected must be a boolean")
            elif value not in STATE_TOKENS:
                raise AssertionError(f"{cid}: expect.{key} must be one of {sorted(STATE_TOKENS)}")

    invariants = document.get("invariants")
    if not isinstance(invariants, list) or len(invariants) < 5:
        raise AssertionError("relocation_scenarios.json must record the relocation invariants")

    return document
