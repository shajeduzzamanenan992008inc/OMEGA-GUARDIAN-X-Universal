from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from .exceptions import (
    IdentityCorruptedError,
    IdentityFileTooLargeError,
    IdentityValidationError,
)

# Maximum identity file size (64 KB as per design)
MAX_IDENTITY_FILE_SIZE = 64 * 1024

# JSON Schema for identity.json v1.0
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
        "schema_version": {
            "type": "string",
            "const": "1.0",
            "description": "Identity schema version"
        },
        "project_id": {
            "type": "string",
            "format": "uuid",
            "description": "Stable UUID v4 project identity"
        },
        "project_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": "^[\\w\\s\\-\\.]+$",
            "description": "Human-readable project name"
        },
        "description": {
            "type": "string",
            "maxLength": 500,
            "description": "Optional project description"
        },
        "created_at": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 UTC timestamp when identity was created"
        },
        "created_by": {
            "type": "string",
            "maxLength": 256,
            "description": "Actor identity (email or subject)"
        },
        "ogx_version": {
            "type": "string",
            "description": "OGX version that created this identity"
        },
        "identity_hash": {
            "type": "string",
            "pattern": "^sha256:[a-f0-9]{64}$",
            "description": "SHA-256 hash of canonical identity content (excludes this field)"
        }
    }
}

# Supported schema versions
SUPPORTED_SCHEMA_VERSIONS = {"1.0"}
CURRENT_SCHEMA_VERSION = "1.0"

# Email-like pattern for created_by (permissive for offline/local users)
CREATED_BY_PATTERN = re.compile(r"^[\w\.\-\+]+@[\w\.\-]+$|^[\w\-\.]+$")

# Secret detection patterns (tokens, keys, etc.)
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|auth)[_\-]?[:=]\s*['\"]?[\w\-]{16,}"),
    re.compile(r"(?i)(bearer|token)\s+[\w\-\.]{20,}"),
    re.compile(r"gh[ps]_[a-zA-Z0-9]{36,}"),  # GitHub tokens
    re.compile(r"glpat-[a-zA-Z0-9\-_]{20,}"),  # GitLab tokens
    re.compile(r"sk-[a-zA-Z0-9]{48,}"),  # OpenAI keys
]


def validate_utf8_file(path: Path) -> None:
    """Validate file is valid UTF-8."""
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise IdentityValidationError(
            f"Identity file is not valid UTF-8: {e}",
            field="encoding"
        ) from e


def validate_file_size(path: Path) -> None:
    """Validate file size does not exceed maximum."""
    size = path.stat().st_size
    if size > MAX_IDENTITY_FILE_SIZE:
        raise IdentityFileTooLargeError(
            f"Identity file exceeds maximum size of {MAX_IDENTITY_FILE_SIZE} bytes",
            size=size,
            max_size=MAX_IDENTITY_FILE_SIZE
        )


def validate_no_secrets(data: dict[str, Any]) -> None:
    """Check for potential secrets in identity data."""
    text = json.dumps(data, ensure_ascii=False)
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise IdentityValidationError(
                "Identity file contains potential secret/credential material",
                field="security"
            )


def validate_created_by(value: str) -> None:
    """Validate created_by field format."""
    if not CREATED_BY_PATTERN.match(value):
        raise IdentityValidationError(
            f"Invalid created_by format: {value}. Use email or alphanumeric identifier.",
            field="created_by"
        )


def validate_timestamp(value: str) -> datetime:
    """Validate and parse ISO 8601 UTC timestamp."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware")
        return dt.astimezone(datetime.now(timezone.utc).tzinfo).replace(tzinfo=None)
    except (ValueError, AttributeError) as e:
        raise IdentityValidationError(
            f"Invalid timestamp format (must be ISO 8601 UTC): {value}",
            field="created_at"
        ) from e


def validate_uuid_v4(value: str) -> None:
    """Validate UUID v4 format."""
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        re.IGNORECASE
    )
    if not uuid_pattern.match(value):
        raise IdentityValidationError(
            f"Invalid UUID v4 format: {value}",
            field="project_id"
        )


def get_schema_for_version(version: str) -> dict[str, Any]:
    """Get JSON schema for a specific version."""
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise IdentityValidationError(
            f"Unsupported schema version: {version}. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            field="schema_version"
        )
    if version == "1.0":
        return IDENTITY_SCHEMA_V1
    raise IdentityValidationError(f"No schema defined for version: {version}")


def validate_identity_json(data: dict[str, Any]) -> dict[str, Any]:
    """
    Validate identity JSON against schema and custom rules.
    
    Returns the validated data dict.
    Raises IdentityValidationError on failure.
    """
    # Check schema_version first
    schema_version = data.get("schema_version")
    if not schema_version:
        raise IdentityValidationError("Missing required field: schema_version", field="schema_version")
    
    schema = get_schema_for_version(schema_version)
    
    # JSON Schema validation
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        field = ".".join(str(p) for p in e.path) if e.path else "root"
        raise IdentityValidationError(
            f"Schema validation failed: {e.message}",
            field=field
        ) from e
    
    # Custom validations
    validate_uuid_v4(data["project_id"])
    validate_timestamp(data["created_at"])
    validate_created_by(data["created_by"])
    validate_no_secrets(data)
    
    return data


def load_and_validate_identity(path: Path) -> dict[str, Any]:
    """Load identity.json from file and validate."""
    validate_file_size(path)
    validate_utf8_file(path)
    
    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise IdentityCorruptedError(
            f"Invalid JSON in identity file: {e}",
            path=str(path)
        ) from e
    
    if not isinstance(data, dict):
        raise IdentityCorruptedError(
            "Identity file must contain a JSON object",
            path=str(path)
        )
    
    return validate_identity_json(data)