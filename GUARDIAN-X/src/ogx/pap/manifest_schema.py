"""Manifest schema and validation for GX-PAP.

Standard-library only: the documented JSON Schema is paired with an explicit
validator rather than importing ``jsonschema``. Commit dbf8874 replaced this
module with documentation-only constants and removed every function that
``manifest_engine.py`` imports, breaking ``import ogx``.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .exceptions import IdentityValidationError

# Maximum manifest file size (64 KB as per design)
MAX_MANIFEST_FILE_SIZE = 64 * 1024

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})
CURRENT_SCHEMA_VERSION = "1.0"

#: JSON Schema for GUARDIAN-X.json v1.0 (documentation; validated explicitly below).
MANIFEST_SCHEMA_V1 = {
    "$schema": "http://json-schema.org/draft-2020-12/schema#",
    "$id": "https://omega-guardian-x.dev/schemas/gx-pap-manifest-1.0.json",
    "title": "GX-PAP Manifest v1.0",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version"],
    "properties": {
        "schema_version": {"type": "string", "const": "1.0"},
        "project": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "project_id": {"type": "string", "format": "uuid"},
                "name": {"type": "string", "minLength": 1, "maxLength": 128},
                "description": {"type": "string", "maxLength": 500},
            },
        },
        "guardian_x": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {"type": "boolean", "default": True},
                "mode": {"type": "string", "enum": ["automatic", "manual"]},
                "capabilities": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "discovery": {"type": "boolean"},
                        "analysis": {"type": "boolean"},
                        "monitoring": {"type": "boolean"},
                        "maintenance": {"type": "boolean"},
                        "engineering": {"type": "boolean"},
                    },
                },
            },
        },
        "policies": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "execution": {
                    "type": "string",
                    "enum": ["never_without_authorization", "requires_approval", "disabled"],
                },
                "modification": {"type": "string", "enum": ["approval_required", "disabled"]},
                "deletion": {"type": "string", "enum": ["approval_required", "disabled"]},
                "deployment": {"type": "string", "enum": ["approval_required", "disabled"]},
                "credentials": {"type": "string", "enum": ["never_store", "external_only"]},
            },
        },
        "hosts": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "supported": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["local", "github", "gitlab", "bitbucket", "gitea", "forgejo"],
                    },
                }
            },
        },
        "trust": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "classification": {
                    "type": "string",
                    "enum": ["unknown", "untrusted", "reviewed", "trusted"],
                }
            },
        },
    },
}

#: Defaults applied to a validated manifest. Deep-copied on every call.
MANIFEST_DEFAULTS: dict[str, Any] = {
    "guardian_x": {
        "enabled": True,
        "mode": "automatic",
        "capabilities": {
            "discovery": True,
            "analysis": True,
            "monitoring": True,
            "maintenance": True,
            "engineering": True,
        },
    },
    "policies": {
        "execution": "never_without_authorization",
        "modification": "approval_required",
        "deletion": "approval_required",
        "deployment": "approval_required",
        "credentials": "never_store",
    },
    "hosts": {"supported": ["local", "github"]},
    "trust": {"classification": "untrusted"},
}

_PROJECT_NAME = re.compile(r"^[\w\s\-.]+$", re.UNICODE)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|auth)[_\-]?\s*[:=]\s*['\"]?[\w\-]{16,}"),
    re.compile(r"(?i)(bearer|token)\s+[\w\-.]+"),
    re.compile(r"gh[ps]_[a-zA-Z0-9]{36,}"),
    re.compile(r"glpat-[a-zA-Z0-9\-_]{20,}"),
    re.compile(r"sk-[a-zA-Z0-9]{48,}"),
)

_ROOT_FIELDS = frozenset({"schema_version", "project", "guardian_x", "policies", "hosts", "trust"})
_PROJECT_FIELDS = frozenset({"project_id", "name", "description"})
_GUARDIAN_FIELDS = frozenset({"enabled", "mode", "capabilities"})
_CAPABILITY_FIELDS = frozenset({"discovery", "analysis", "monitoring", "maintenance", "engineering"})
_POLICY_FIELDS = frozenset({"execution", "modification", "deletion", "deployment", "credentials"})
_HOST_FIELDS = frozenset({"supported"})
_TRUST_FIELDS = frozenset({"classification"})

_POLICY_ENUMS = {
    "execution": frozenset({"never_without_authorization", "requires_approval", "disabled"}),
    "modification": frozenset({"approval_required", "disabled"}),
    "deletion": frozenset({"approval_required", "disabled"}),
    "deployment": frozenset({"approval_required", "disabled"}),
    "credentials": frozenset({"never_store", "external_only"}),
}
_HOST_ENUM = frozenset({"local", "github", "gitlab", "bitbucket", "gitea", "forgejo"})
_TRUST_ENUM = frozenset({"unknown", "untrusted", "reviewed", "trusted"})


def validate_utf8_file(path: Path) -> None:
    """Validate that the manifest is readable as UTF-8."""
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise IdentityValidationError(
            f"Manifest file is not valid UTF-8: {exc}", field="encoding"
        ) from exc


def validate_file_size(path: Path) -> None:
    """Validate that the manifest does not exceed the bounded size."""
    size = path.stat().st_size
    if size > MAX_MANIFEST_FILE_SIZE:
        raise IdentityValidationError(
            f"Manifest file exceeds maximum size of {MAX_MANIFEST_FILE_SIZE} bytes",
            field="file_size",
        )


def validate_no_secrets(data: Mapping[str, Any]) -> None:
    """Reject manifest data that contains credential-like material."""
    text = json.dumps(dict(data), ensure_ascii=False)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise IdentityValidationError(
                "Manifest file contains potential secret/credential material",
                field="security",
            )


def validate_no_path_traversal(data: Mapping[str, Any]) -> None:
    """Reject ``..`` path segments in fields declared as filesystem paths.

    The v1 manifest has no path field; this guard exists so a future path field
    cannot silently accept traversal. Only path-like keys are inspected so a
    description containing ``..`` is not rejected.
    """
    path_keys = {"project_path", "path", "root", "workspace_path", "destination"}

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, key)
        elif isinstance(value, str) and key in path_keys:
            if ".." in re.split(r"[\\/]+", value):
                raise IdentityValidationError(
                    f"Manifest field '{key}' must not contain path traversal segments",
                    field=key,
                )

    walk(data)


def get_schema_for_version(version: str) -> dict[str, Any]:
    """Return the documented schema for a supported manifest version."""
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise IdentityValidationError(
            f"Unsupported manifest schema version: {version}. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            field="schema_version",
        )
    return MANIFEST_SCHEMA_V1


def _object(value: Any, field: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IdentityValidationError(f"{field} must be an object", field=field)
    unknown = set(value) - allowed
    if unknown:
        raise IdentityValidationError(
            f"Unknown field(s) in {field}: {', '.join(sorted(unknown))}", field=field
        )
    return value


def _string(value: Any, field: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str):
        raise IdentityValidationError(f"{field} must be a string", field=field)
    if maximum is not None and len(value) > maximum:
        raise IdentityValidationError(f"{field} exceeds {maximum} characters", field=field)
    return value


def validate_manifest_json(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a manifest against the v1 schema and custom rules.

    Returns a plain ``dict`` copy. Raises :class:`IdentityValidationError`.
    """
    if not isinstance(data, dict):
        raise IdentityValidationError("Manifest must contain a JSON object", field="root")
    unknown = set(data) - _ROOT_FIELDS
    if unknown:
        raise IdentityValidationError(
            f"Unknown manifest field(s): {', '.join(sorted(unknown))}", field="root"
        )
    if "schema_version" not in data:
        raise IdentityValidationError("Missing required field: schema_version", field="schema_version")
    version = data["schema_version"]
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise IdentityValidationError(
            f"Unsupported manifest schema version: {version}. Supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
            field="schema_version",
        )

    if "project" in data:
        project = _object(data["project"], "project", _PROJECT_FIELDS)
        if "project_id" in project:
            project_id = _string(project["project_id"], "project.project_id")
            if not _UUID.fullmatch(project_id):
                raise IdentityValidationError(
                    "project.project_id must be a UUID", field="project.project_id"
                )
        if "name" in project:
            name = _string(project["name"], "project.name", maximum=128)
            if not name:
                raise IdentityValidationError(
                    "project.name must be a non-empty string", field="project.name"
                )
            if not _PROJECT_NAME.fullmatch(name):
                raise IdentityValidationError(
                    "project.name may contain letters, numbers, spaces, dots, dashes and underscores",
                    field="project.name",
                )
        if "description" in project:
            _string(project["description"], "project.description", maximum=500)

    if "guardian_x" in data:
        guardian = _object(data["guardian_x"], "guardian_x", _GUARDIAN_FIELDS)
        if "enabled" in guardian and not isinstance(guardian["enabled"], bool):
            raise IdentityValidationError("guardian_x.enabled must be a boolean", field="guardian_x.enabled")
        if "mode" in guardian:
            mode = _string(guardian["mode"], "guardian_x.mode")
            if mode not in {"automatic", "manual"}:
                raise IdentityValidationError(
                    f"guardian_x.mode must be one of automatic, manual (got {mode!r})",
                    field="guardian_x.mode",
                )
        if "capabilities" in guardian:
            capabilities = _object(guardian["capabilities"], "guardian_x.capabilities", _CAPABILITY_FIELDS)
            for name, enabled in capabilities.items():
                if not isinstance(enabled, bool):
                    raise IdentityValidationError(
                        f"guardian_x.capabilities.{name} must be a boolean",
                        field=f"guardian_x.capabilities.{name}",
                    )

    if "policies" in data:
        policies = _object(data["policies"], "policies", _POLICY_FIELDS)
        for name, allowed in _POLICY_ENUMS.items():
            if name in policies:
                value = _string(policies[name], f"policies.{name}")
                if value not in allowed:
                    raise IdentityValidationError(
                        f"policies.{name} must be one of {sorted(allowed)} (got {value!r})",
                        field=f"policies.{name}",
                    )

    if "hosts" in data:
        hosts = _object(data["hosts"], "hosts", _HOST_FIELDS)
        if "supported" in hosts:
            supported = hosts["supported"]
            if not isinstance(supported, list):
                raise IdentityValidationError(
                    "hosts.supported must be an array", field="hosts.supported"
                )
            for index, host in enumerate(supported):
                value = _string(host, f"hosts.supported[{index}]")
                if value not in _HOST_ENUM:
                    raise IdentityValidationError(
                        f"hosts.supported[{index}] must be one of {sorted(_HOST_ENUM)} (got {value!r})",
                        field=f"hosts.supported[{index}]",
                    )

    if "trust" in data:
        trust = _object(data["trust"], "trust", _TRUST_FIELDS)
        if "classification" in trust:
            value = _string(trust["classification"], "trust.classification")
            if value not in _TRUST_ENUM:
                raise IdentityValidationError(
                    f"trust.classification must be one of {sorted(_TRUST_ENUM)} (got {value!r})",
                    field="trust.classification",
                )

    validate_no_secrets(data)
    validate_no_path_traversal(data)
    return dict(data)


def apply_manifest_defaults(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new manifest dict with documented defaults filled in."""
    result: dict[str, Any] = copy.deepcopy(dict(data))
    for key, default_value in copy.deepcopy(MANIFEST_DEFAULTS).items():
        if key not in result:
            result[key] = default_value
        elif isinstance(default_value, dict) and isinstance(result.get(key), dict):
            merged = default_value
            for child_key, child_value in result[key].items():
                if isinstance(child_value, dict) and isinstance(merged.get(child_key), dict):
                    nested = dict(merged[child_key])
                    nested.update(child_value)
                    merged[child_key] = nested
                else:
                    merged[child_key] = child_value
            result[key] = merged
    return result
