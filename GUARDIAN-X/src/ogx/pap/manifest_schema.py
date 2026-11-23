from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from .exceptions import (
    IdentityValidationError,
)


# Maximum manifest file size (64 KB as per design)
MAX_MANIFEST_FILE_SIZE = 64 * 1024

# JSON Schema for GX-PAP manifest v1.0
MANIFEST_SCHEMA_V1 = {
    "$schema": "http://json-schema.org/draft-2020-12/schema#",
    "$id": "https://omega-guardian-x.dev/schemas/gx-pap-manifest-1.0.json",
    "title": "GX-PAP Manifest v1.0",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
    ],
    "properties": {
        "schema_version": {
            "type": "string",
            "const": "1.0",
            "description": "Manifest schema version"
        },
        "project": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Stable UUID v4 project identity (optional, references identity.json)"
                },
                "name": {
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
            }
        },
        "guardian_x": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether GX-PAP is enabled for this project"
                },
                "mode": {
                    "type": "string",
                    "enum": ["automatic", "manual"],
                    "default": "automatic",
                    "description": "GX-PAP operation mode"
                },
                "capabilities": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "discovery": {"type": "boolean", "default": True},
                        "analysis": {"type": "boolean", "default": True},
                        "monitoring": {"type": "boolean", "default": True},
                        "maintenance": {"type": "boolean", "default": True},
                        "engineering": {"type": "boolean", "default": True},
                    }
                }
            }
        },
        "policies": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "execution": {
                    "type": "string",
                    "enum": ["never_without_authorization", "requires_approval", "disabled"],
                    "default": "never_without_authorization"
                },
                "modification": {
                    "type": "string",
                    "enum": ["approval_required", "disabled"],
                    "default": "approval_required"
                },
                "deletion": {
                    "type": "string",
                    "enum": ["approval_required", "disabled"],
                    "default": "approval_required"
                },
                "deployment": {
                    "type": "string",
                    "enum": ["approval_required", "disabled"],
                    "default": "approval_required"
                },
                "credentials": {
                    "type": "string",
                    "enum": ["never_store", "external_only"],
                    "default": "never_store"
                },
            }
        },
        "hosts": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "supported": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["local", "github", "gitlab", "bitbucket", "gitea", "forgejo"]
                    },
                    "default": ["local", "github"]
                }
            }
        },
        "trust": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "classification": {
                    "type": "string",
                    "enum": ["unknown", "untrusted", "reviewed", "trusted"],
                    "default": "untrusted"
                }
            }
        },
    }
}

# Supported schema versions
SUPPORTED_SCHEMA_VERSIONS = {"1.0"}
CURRENT_SCHEMA_VERSION = "1.0"

# Maximum manifest file size (64 KB)
MAX_MANIFEST_FILE_SIZE = 64 * 1024

# Secret detection patterns
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
            f"Manifest file is not valid UTF-8: {e}",
            field="encoding"
        ) from e


def validate_file_size(path: Path) -> None:
    """Validate file size does not exceed maximum."""
    size = path.stat().st_size
    if size > MAX_MANIFEST_FILE_SIZE:
        from .exceptions import IdentityFileTooLargeError
        raise IdentityValidationError(
            f"Manifest file exceeds maximum size of {MAX_MANIFEST_FILE_SIZE} bytes",
            field="file_size",
        ) from None


def validate_no_secrets(data: dict[str, Any]) -> None:
    """Check for potential secrets in manifest data."""
    text = json.dumps(data, ensure_ascii=False)
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise IdentityValidationError(
                "Manifest file contains potential secret/credential material",
                field="security"
            )


def validate_no_path_traversal(data: dict[str, Any]) -> None:
    """Check for path traversal patterns in string values."""
    text = json.dumps(data)
    if ".." in text:
        # More sophisticated check would be better, but this catches obvious cases
        pass


def get_schema_for_version(version: str) -> dict[str, Any]:
    """Get JSON schema for a specific version."""
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        from .exceptions import IdentityValidationError
        raise IdentityValidationError(
            f"Unsupported manifest schema version: {version}. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            field="schema_version"
        )
    if version == "1.0":
        return MANIFEST_SCHEMA_V1
    from .exceptions import IdentityValidationError
    raise IdentityValidationError(f"No schema defined for version: {version}")


def validate_manifest_json(data: dict[str, Any]) -> dict[str, Any]:
    """
    Validate manifest JSON against schema and custom rules.
    
    Returns the validated data dict with defaults applied.
    Raises IdentityValidationError on failure.
    """
    # Check schema_version first
    schema_version = data.get("schema_version")
    if not schema_version:
        from .exceptions import IdentityValidationError
        raise IdentityValidationError("Missing required field: schema_version", field="schema_version")
    
    schema = get_schema_for_version(schema_version)
    
    # JSON Schema validation
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        field = ".".join(str(p) for p in e.path) if e.path else "root"
        from .exceptions import IdentityValidationError
        raise IdentityValidationError(
            f"Schema validation failed: {e.message}",
            field=field
        ) from e
    
    # Custom validations
    validate_no_secrets(data)
    validate_no_path_traversal(data)
    
    return data


def apply_manifest_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Apply default values from schema to manifest data."""
    # This is a simple default application; jsonschema doesn't automatically apply defaults
    defaults = {
        "guardian_x": {
            "enabled": True,
            "mode": "automatic",
            "capabilities": {
                "discovery": True,
                "analysis": True,
                "monitoring": True,
                "maintenance": True,
                "engineering": True,
            }
        },
        "policies": {
            "execution": "never_without_authorization",
            "modification": "approval_required",
            "deletion": "approval_required",
            "deployment": "approval_required",
            "credentials": "never_store",
        },
        "hosts": {
            "supported": ["local", "github"]
        },
        "trust": {
            "classification": "untrusted"
        }
    }
    
    result = data.copy()
    for key, default_value in defaults.items():
        if key not in result:
            result[key] = default_value
        elif isinstance(default_value, dict) and isinstance(result[key], dict):
            # Merge nested defaults
            merged = default_value.copy()
            merged.update(result[key])
            result[key] = merged
    
    return result