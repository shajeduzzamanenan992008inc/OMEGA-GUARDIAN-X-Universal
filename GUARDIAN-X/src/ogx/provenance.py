"""Artifact provenance and signing boundaries (REL-04).

The project can build and *verify* provenance statements without holding any
signing key. Signing is deliberately not implemented here:

* :func:`build_statement` produces a deterministic in-toto/SLSA-style statement
  (no timestamps, sorted subjects) that is explicitly marked ``unsigned``;
* :func:`attach_signature` embeds an externally produced detached signature
  (the owner's provenance tooling owns the key);
* :func:`verify_statement` re-hashes the artifacts and re-verifies an attached
  Ed25519 signature against a supplied public key.

No key material is created, stored or read by this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifacts import sha256_file
from .plugins.ed25519 import SignatureError, verify_strict

PROVENANCE_SCHEMA = "1.0"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
# Builder identities are labels, never secrets; refuse anything that looks like one.
_SECRET_MARKERS = ("token", "secret", "password", "bearer ", "-----begin")


class ProvenanceError(ValueError):
    """A provenance statement is unsafe, malformed or unverifiable."""


class SigningNotPermitted(ProvenanceError):
    """Raised when signing is requested: signing keys are owner-controlled."""


def _clean(value: str, field: str, limit: int = 300) -> str:
    text = str(value).strip()
    if not text or len(text) > limit:
        raise ProvenanceError(f"{field} must be a non-empty string of at most {limit} characters.")
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ProvenanceError(f"{field} looks like key material; pass an identifier instead.")
    return text


def build_statement(
    artifacts: Iterable[Path | str],
    *,
    builder_id: str,
    build_type: str,
    source_uri: str,
    source_digest: str,
    invocation: Mapping[str, Any] | None = None,
    materials: Iterable[Mapping[str, str]] = (),
    reproducible_command: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, explicitly unsigned provenance statement."""

    subjects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in artifacts:
        path = Path(item)
        if path.is_symlink() or not path.is_file():
            raise ProvenanceError(f"Refusing a non-regular provenance subject: {path.name}")
        if not _SAFE_KEY.match(path.name):
            raise ProvenanceError(f"Unsafe subject name: {path.name!r}")
        if path.name in seen:
            raise ProvenanceError(f"Duplicate provenance subject: {path.name}")
        seen.add(path.name)
        subjects.append(
            {
                "name": path.name,
                "digest": {"sha256": sha256_file(path)},
                "size": path.stat().st_size,
            }
        )
    if not subjects:
        raise ProvenanceError("A provenance statement needs at least one subject.")
    if not _HEX64.match(str(source_digest)):
        raise ProvenanceError("source_digest must be a lowercase SHA-256 hex digest.")

    normalized_materials = []
    for material in materials:
        uri = _clean(str(material.get("uri", "")), "material uri")
        digest = str(material.get("sha256", ""))
        if not _HEX64.match(digest):
            raise ProvenanceError(f"Material {uri!r} has an invalid SHA-256 digest.")
        normalized_materials.append({"uri": uri, "digest": {"sha256": digest}})
    normalized_materials.sort(key=lambda item: item["uri"])

    predicate: dict[str, Any] = {
        "buildDefinition": {
            "buildType": _clean(build_type, "build_type"),
            "externalParameters": {
                "source": {"uri": _clean(source_uri, "source_uri")},
                "invocation": dict(invocation or {}),
            },
            "internalParameters": {
                "reproducibleCommand": reproducible_command
                or "python _dev/tools/repro_check.py"
            },
            "resolvedDependencies": normalized_materials,
        },
        "runDetails": {
            "builder": {"id": _clean(builder_id, "builder_id")},
            "metadata": {"completeness": {"parameters": True, "environment": False}},
        },
    }
    return {
        "_type": STATEMENT_TYPE,
        "subject": sorted(subjects, key=lambda item: item["name"]),
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
        "signature": None,
        "schema_version": PROVENANCE_SCHEMA,
    }


def render_statement(statement: Mapping[str, Any]) -> str:
    """Stable JSON bytes: no timestamp, sorted keys, trailing newline."""

    return json.dumps(statement, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def attach_signature(
    statement: Mapping[str, Any],
    *,
    key_id: str,
    signature_hex: str,
    public_key_hex: str,
) -> dict[str, Any]:
    """Embed an externally produced Ed25519 signature (never signs here)."""

    if not _SAFE_KEY.match(str(key_id)):
        raise ProvenanceError("key_id must be a short identifier, not key material.")
    signature = str(signature_hex).strip().lower()
    public = str(public_key_hex).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{128}", signature):
        raise ProvenanceError("signature_hex must be a 64-byte Ed25519 signature in hex.")
    if not re.fullmatch(r"[0-9a-f]{64}", public):
        raise ProvenanceError("public_key_hex must be a 32-byte Ed25519 public key in hex.")
    signed = {key: value for key, value in statement.items() if key != "signature"}
    return {
        **signed,
        "signature": {
            "algorithm": "ed25519",
            "key_id": key_id,
            "public_key": public,
            "signature": signature,
        },
    }


def signing_payload(statement: Mapping[str, Any]) -> bytes:
    """The exact bytes a signer must sign (signature block excluded)."""

    body = {key: value for key, value in statement.items() if key != "signature"}
    return render_statement(body).encode("utf-8")


def sign_statement(*_args: Any, **_kwargs: Any) -> None:
    """Signing requires an owner-controlled key and is intentionally absent."""

    raise SigningNotPermitted(
        "This project does not sign artifacts: no signing key is present and signing requires "
        "explicit owner authorization. Produce the detached Ed25519 signature with the owner's "
        "tooling over `render_statement(statement)` and attach it with `attach_signature`."
    )


def verify_statement(
    statement: Mapping[str, Any],
    base_dir: Path | str,
    *,
    expected_public_key: str | None = None,
) -> dict[str, Any]:
    """Verify subjects against disk and, when present, the attached signature."""

    if statement.get("_type") != STATEMENT_TYPE:
        raise ProvenanceError("Unsupported provenance statement type.")
    if statement.get("predicateType") != PREDICATE_TYPE:
        raise ProvenanceError("Unsupported provenance predicate type.")
    base = Path(base_dir).resolve()
    report: dict[str, Any] = {
        "checked": 0,
        "mismatched": [],
        "missing": [],
        "unsigned": statement.get("signature") is None,
        "signature_valid": None,
        "all_ok": False,
    }
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or not subjects:
        raise ProvenanceError("A provenance statement must list at least one subject.")
    for subject in subjects:
        if not isinstance(subject, Mapping):
            raise ProvenanceError("Each provenance subject must be an object.")
        name = str(subject.get("name", ""))
        if not _SAFE_KEY.match(name):
            raise ProvenanceError(f"Unsafe subject name in statement: {name!r}")
        digest = str((subject.get("digest") or {}).get("sha256", ""))
        if not _HEX64.match(digest):
            raise ProvenanceError(f"Subject {name!r} has an invalid digest.")
        target = (base / name).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ProvenanceError(f"Subject {name!r} escapes the artifact directory.") from exc
        if target.is_symlink() or not target.is_file():
            report["missing"].append(name)
            continue
        actual = sha256_file(target)
        report["checked"] += 1
        if actual != digest:
            report["mismatched"].append({"name": name, "expected": digest, "actual": actual})

    signature = statement.get("signature")
    if signature is not None:
        if not isinstance(signature, Mapping):
            raise ProvenanceError("The signature block must be an object.")
        if signature.get("algorithm") != "ed25519":
            raise ProvenanceError("Only detached Ed25519 signatures are supported.")
        public_hex = str(signature.get("public_key", "")).lower()
        signature_hex = str(signature.get("signature", "")).lower()
        if expected_public_key is not None and public_hex != expected_public_key.strip().lower():
            raise ProvenanceError("The statement was signed by an unexpected public key.")
        try:
            verify_strict(
                bytes.fromhex(public_hex),
                bytes.fromhex(signature_hex),
                signing_payload(statement),
            )
            report["signature_valid"] = True
        except (ValueError, SignatureError):
            report["signature_valid"] = False

    report["all_ok"] = (
        report["checked"] > 0
        and not report["mismatched"]
        and not report["missing"]
        and report["signature_valid"] is not False
    )
    return report
