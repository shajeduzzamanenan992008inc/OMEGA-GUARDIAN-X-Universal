"""Identity schema and validation for GX-PAP.

The core is standard-library only, so the documented JSON Schema is paired with
an explicit validator here instead of importing the third-party ``jsonschema``
package. This module is the single place that decides what a valid
``.guardian-x/identity.json`` looks like.

Commit dbf8874 replaced this module with documentation-only constants while
``identity.py`` still imported the validators below, which made ``ogx``
unimportable. Keep every symbol in ``__all__`` present and covered by tests.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .exceptions import IdentityFileTooLargeError, IdentityValidationError

# Maximum identity file size (64 KB as per design)
MAX_IDENTITY_FILE_SIZE = 64 * 1024

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})
CURRENT_SCHEMA_VERSION = "1.0"

#: JSON Schema for identity.json v1.0 (documentation; validated explicitly below).
IDENTITY_SCHEMA_V1 = {
    "$schema": "http://json-schema.org/draft-2020-12/schema#",
    "$id": "https://omega-guardian-x.dev/schemas/gx-pap-identity-1.0.json",
    "title": "GX-PAP Identity v1.0",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "project_id",
        "project_name",
        "created_at",
        "created_by",
        "ogx_version",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "project_id": {"type": "string", "format": "uuid"},
        "project_name": {"type": "string", "minLength": 1, "maxLength": 128},
        "description": {"type": "string", "maxLength": 500},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "string", "maxLength": 256},
        "ogx_version": {"type": "string"},
        "identity_hash": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
    },
}

_REQUIRED_FIELDS = (
    "schema_version",
    "project_id",
    "project_name",
    "created_at",
    "created_by",
    "ogx_version",
)
_ALLOWED_FIELDS = frozenset(_REQUIRED_FIELDS) | {"description", "identity_hash"}

_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_CREATED_BY = re.compile(r"^(?:[\w.\-+]+@[\w.\-]+|[\w.\-]+)$", re.UNICODE)
_PROJECT_NAME = re.compile(r"^[\w\s\-.]+$", re.UNICODE)
_IDENTITY_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|auth)[_\-]?\s*[:=]\s*['\"]?[\w\-]{16,}"),
    re.compile(r"(?i)(bearer|token)\s+[\w\-.]+"),
    re.compile(r"gh[ps]_[a-zA-Z0-9]{36,}"),
    re.compile(r"glpat-[a-zA-Z0-9\-_]{20,}"),
    re.compile(r"sk-[a-zA-Z0-9]{48,}"),
)


def validate_utf8_file(path: Path) -> None:
    """Validate that a file is readable as UTF-8."""
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise IdentityValidationError(
            f"Identity file is not valid UTF-8: {exc}", field="encoding"
        ) from exc


def validate_file_size(path: Path) -> None:
    """Validate that a file does not exceed the identity size limit."""
    size = path.stat().st_size
    if size > MAX_IDENTITY_FILE_SIZE:
        raise IdentityFileTooLargeError(
            f"Identity file exceeds maximum size of {MAX_IDENTITY_FILE_SIZE} bytes",
            size=size,
            max_size=MAX_IDENTITY_FILE_SIZE,
        )


def validate_no_secrets(data: Mapping[str, Any]) -> None:
    """Reject identity data that contains credential-like material."""
    text = json.dumps(dict(data), ensure_ascii=False)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise IdentityValidationError(
                "Identity file contains potential secret/credential material",
                field="security",
            )


def validate_created_by(value: str) -> None:
    """Validate the creator identity: an email address or a local identifier."""
    if not isinstance(value, str) or not _CREATED_BY.fullmatch(value):
        raise IdentityValidationError(
            f"Invalid created_by format: {value!r}. Use an email or a local identifier.",
            field="created_by",
        )


def validate_timestamp(value: str) -> datetime:
    """Validate an ISO 8601 timestamp and require an explicit timezone."""
    if not isinstance(value, str):
        raise IdentityValidationError("created_at must be a string", field="created_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IdentityValidationError(
            f"Invalid timestamp format (must be ISO 8601): {value}", field="created_at"
        ) from exc
    if parsed.tzinfo is None:
        raise IdentityValidationError(
            f"Timestamp must include a timezone: {value}", field="created_at"
        )
    return parsed


def validate_uuid_v4(value: str) -> None:
    """Validate a UUID v4 string (the stable project identifier)."""
    if not isinstance(value, str) or not _UUID_V4.fullmatch(value):
        raise IdentityValidationError(
            f"Invalid UUID v4 format: {value}", field="project_id"
        )


def get_schema_for_version(version: str) -> dict[str, Any]:
    """Return the documented schema for a supported identity version."""
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise IdentityValidationError(
            f"Unsupported schema version: {version}. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            field="schema_version",
        )
    return IDENTITY_SCHEMA_V1


def _require_string(data: Mapping[str, Any], field: str, *, maximum: int | None = None) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        raise IdentityValidationError(f"{field} must be a string", field=field)
    if maximum is not None and len(value) > maximum:
        raise IdentityValidationError(
            f"{field} exceeds {maximum} characters", field=field
        )
    return value


def validate_identity_json(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate identity JSON against the v1 schema and the custom rules.

    Returns a plain ``dict`` copy on success and raises
    :class:`IdentityValidationError` (or :class:`IdentityFileTooLargeError` for
    size checks performed by the caller) on failure.
    """
    if not isinstance(data, dict):
        raise IdentityValidationError("Identity file must contain a JSON object", field="root")

    unknown = set(data) - _ALLOWED_FIELDS
    if unknown:
        raise IdentityValidationError(
            f"Unknown identity field(s): {', '.join(sorted(unknown))}", field="root"
        )
    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise IdentityValidationError(f"Missing required field: {field}", field=field)

    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise IdentityValidationError(
            f"Unsupported schema version: {version}. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            field="schema_version",
        )

    validate_uuid_v4(data.get("project_id"))

    project_name = _require_string(data, "project_name", maximum=128)
    if not project_name:
        raise IdentityValidationError("project_name must be a non-empty string", field="project_name")
    if not _PROJECT_NAME.fullmatch(project_name):
        raise IdentityValidationError(
            "project_name may contain letters, numbers, spaces, dots, dashes and underscores",
            field="project_name",
        )

    if "description" in data:
        _require_string(data, "description", maximum=500)

    validate_timestamp(data.get("created_at"))
    validate_created_by(_require_string(data, "created_by", maximum=256))
    _require_string(data, "ogx_version", maximum=128)

    identity_hash = data.get("identity_hash")
    if identity_hash is not None:
        if not isinstance(identity_hash, str) or not _IDENTITY_HASH.fullmatch(identity_hash):
            raise IdentityValidationError(
                "identity_hash must be 'sha256:' followed by 64 lowercase hex characters",
                field="identity_hash",
            )

    validate_no_secrets(data)
    return dict(data)


def load_and_validate_identity(path: Path) -> dict[str, Any]:
    """Load identity.json from disk and validate it."""
    validate_file_size(path)
    validate_utf8_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        from .exceptions import IdentityCorruptedError

        raise IdentityCorruptedError(
            f"Invalid JSON in identity file: {exc}", path=str(path)
        ) from exc
    if not isinstance(data, dict):
        from .exceptions import IdentityCorruptedError

        raise IdentityCorruptedError(
            "Identity file must contain a JSON object", path=str(path)
        )
    return validate_identity_json(data)
