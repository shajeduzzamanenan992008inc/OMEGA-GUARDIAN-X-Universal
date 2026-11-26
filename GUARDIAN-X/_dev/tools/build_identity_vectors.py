"""Build the shared identity golden vectors used by both distributions.

Provenance tool for ``_dev/fixtures/identity_vectors.json``. The fixture is
*frozen*: it is the single source of truth for identity canonicalization, and a
failing vector must be investigated, never regenerated to match new behavior.

Run from ``GUARDIAN-X``::

    python _dev/tools/build_identity_vectors.py

The expected ``canonical_json`` and ``identity_hash`` values are computed with
the shipped canonical implementation so the frozen file cannot contain a
hand-transcribed hash.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ogx.pap.identity import ProjectIdentity  # noqa: E402
from ogx.pap.identity_schema import validate_identity_json  # noqa: E402

PID = "3f2a1c4e-9b7d-4a15-8c3e-2d6f5a1b0c94"
BASE = {
    "schema_version": "1.0",
    "project_id": PID,
    "project_name": "Guardian Sample",
    "created_at": "2026-01-02T03:04:05+00:00",
    "created_by": "owner@example.com",
    "ogx_version": "0.4.0",
}


def golden(data):
    """Return (canonical_json, identity_hash) for one accepted identity object."""
    validated = validate_identity_json(data)
    identity = ProjectIdentity(
        schema_version=validated["schema_version"],
        project_id=validated["project_id"],
        project_name=validated["project_name"],
        description=validated.get("description", ""),
        created_at=validated["created_at"],
        created_by=validated["created_by"],
        ogx_version=validated["ogx_version"],
        identity_hash=validated.get("identity_hash", ""),
    )
    return identity.to_canonical_json().decode("utf-8"), identity.compute_hash()


def accept(vector_id, title, data, note=None):
    canonical_json, identity_hash = golden(data)
    entry = {
        "id": vector_id,
        "title": title,
        "check": "validate_json",
        "expect": "accept",
        "applies_to": ["canonical"],
        "input": data,
        "canonical_json": canonical_json,
        "identity_hash": identity_hash,
    }
    if note:
        entry["note"] = note
    return entry


def reject(vector_id, title, data, error_field):
    return {
        "id": vector_id,
        "title": title,
        "check": "validate_json",
        "expect": "reject",
        "applies_to": ["canonical"],
        "input": data,
        "error_class": "IdentityValidationError",
        "error_field": error_field,
    }


def build():
    base_canonical, base_hash = golden(BASE)
    vectors = [
        accept(
            "V01-minimal",
            "Minimal valid identity: required fields only",
            dict(BASE),
            "description is absent in the input and defaults to an empty string in the canonical form.",
        ),
        accept(
            "V02-with-description",
            "Valid identity with a short description",
            {**BASE, "description": "Sample project."},
        ),
        accept(
            "V03-max-description",
            "Description at the 500-character limit",
            {**BASE, "description": "d" * 500},
        ),
        accept(
            "V04-name-128",
            "project_name at the 128-character limit",
            {**BASE, "project_name": "a" * 128},
        ),
        accept(
            "V05-unicode",
            "Unicode project name and description",
            {**BASE, "project_name": "Проект Guardian ガーディアン", "description": "Ünïcødé — ok ✓"},
        ),
        accept(
            "V06-uppercase-uuid",
            "Uppercase hexadecimal UUID v4 is accepted and preserved verbatim",
            {**BASE, "project_id": PID.upper()},
            "Recorded divergence: the uppercase form is accepted but canonicalizes to a different "
            "PIH than the lowercase form of the same UUID, so UUID case is currently part of the "
            "identity. See D5/T5 in the design.",
        ),
        accept(
            "V07-zulu-timestamp",
            "Timestamp with a Z suffix is accepted and preserved verbatim",
            {**BASE, "created_at": "2026-01-02T03:04:05Z"},
        ),
        accept(
            "V08-offset-timestamp",
            "Timestamp with a non-UTC offset is accepted and preserved verbatim",
            {**BASE, "created_at": "2026-01-02T03:04:05+05:30"},
            "Recorded divergence: timestamps are validated but not normalized to UTC, so timestamp "
            "spelling is part of the identity.",
        ),
        reject("V09-uuid-v1", "UUID v1 is rejected",
               {**BASE, "project_id": "f47ac10b-58cc-1372-a567-0e02b2c3d479"}, "project_id"),
        reject("V10-uuid-v5", "UUID v5 is rejected",
               {**BASE, "project_id": "21b4b4f2-8d1e-5b1a-9f2c-3d4e5f6a7b8c"}, "project_id"),
        reject("V11-uuid-malformed", "Malformed UUID is rejected",
               {**BASE, "project_id": "not-a-uuid"}, "project_id"),
        reject("V12-missing-project-name", "Missing required field is rejected",
               {k: v for k, v in BASE.items() if k != "project_name"}, "project_name"),
        reject("V13-unknown-field", "Unknown field is rejected (strict schema)",
               {**BASE, "relocation_hint": "/tmp/x"}, "root"),
        reject("V14-description-501", "Description over 500 characters is rejected",
               {**BASE, "description": "d" * 501}, "description"),
        reject("V15-name-illegal-char", "project_name with an illegal character is rejected",
               {**BASE, "project_name": "bad/name"}, "project_name"),
        reject("V16-name-129", "project_name over 128 characters is rejected",
               {**BASE, "project_name": "a" * 129}, "project_name"),
        reject("V17-timestamp-no-tz", "Timestamp without a timezone is rejected",
               {**BASE, "created_at": "2026-01-02T03:04:05"}, "created_at"),
        reject("V18-timestamp-not-iso", "Non-ISO timestamp is rejected",
               {**BASE, "created_at": "02/01/2026 03:04"}, "created_at"),
        reject("V19-secret-like", "Credential-like content is rejected",
               {**BASE, "description": "api_key = SUPERSECRETVALUE1234567890"}, "security"),
        reject("V20-schema-version-2", "Unsupported schema version is rejected",
               {**BASE, "schema_version": "2.0"}, "schema_version"),
        reject("V21-identity-hash-bad", "Malformed identity_hash is rejected",
               {**BASE, "identity_hash": "sha256:ABC"}, "identity_hash"),
        reject("V22-created-by-invalid", "created_by with whitespace is rejected",
               {**BASE, "created_by": "two words"}, "created_by"),
        reject("V23-root-not-object", "A non-object root is rejected", [], "root"),
        {
            "id": "V24-oversize-file",
            "title": "Identity file above 64 KiB is rejected before parsing",
            "check": "file_size",
            "expect": "reject",
            "applies_to": ["canonical"],
            "file_json": dict(BASE),
            "trailing_whitespace_bytes": 66560,
            "error_class": "IdentityFileTooLargeError",
        },
        {
            "id": "V25-hash-mismatch",
            "title": "Stored hash that does not match the content is rejected",
            "check": "read_identity",
            "expect": "reject",
            "applies_to": ["canonical"],
            "file_json": {**BASE, "identity_hash": "sha256:" + "f" * 64},
            "error_class": "IdentityCorruptedError",
        },
        {
            "id": "V26-crlf-file",
            "title": "CRLF line endings and indentation do not change the PIH",
            "check": "read_identity",
            "expect": "accept",
            "applies_to": ["canonical"],
            "file_json": {**BASE, "identity_hash": base_hash},
            "file_text_style": "crlf",
            "indent": 4,
            "canonical_json": base_canonical,
            "identity_hash": base_hash,
            "note": "Canonicalization hashes the parsed object, never the raw file bytes.",
        },
        {
            "id": "V27-missing-hash",
            "title": "Identity without a stored hash is rejected on read",
            "check": "read_identity",
            "expect": "reject",
            "applies_to": ["canonical"],
            "file_json": dict(BASE),
            "error_class": "IdentityCorruptedError",
        },
    ]

    return {
        "schema_version": "1.0",
        "description": (
            "Shared identity golden vectors for the canonical and drop-in distributions. "
            "Built from the schema in GUARDIAN-X/docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md "
            "section 6.1 and frozen; never regenerate these values to make a failing test pass."
        ),
        "design_reference": (
            "GUARDIAN-X/docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md#114-shared-golden-vectors"
        ),
        "implementations": {
            "canonical": {
                "identity_module": "ogx.pap.identity",
                "status": "implemented",
                "state_key_hex_length": 16,
            },
            "drop-in": {
                "identity_module": None,
                "status": "not-implemented",
                "state_key_hex_length": 12,
            },
        },
        "canonicalization": {
            "encoding": "utf-8",
            "sort_keys": True,
            "ensure_ascii": False,
            "separators": [",", ":"],
            "excluded_fields": ["identity_hash"],
            "defaulted_fields": {"description": ""},
            "canonical_fields": [
                "schema_version",
                "project_id",
                "project_name",
                "description",
                "created_at",
                "created_by",
                "ogx_version",
            ],
            "hash_algorithm": "sha256",
            "hash_prefix": "sha256:",
        },
        "vectors": vectors,
    }


def main() -> int:
    path = ROOT / "_dev" / "fixtures" / "identity_vectors.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(build(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
