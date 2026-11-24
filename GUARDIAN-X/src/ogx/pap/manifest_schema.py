"""Manifest schema definitions for GX-PAP (Passive Analysis Plugin).

These JSON Schema definitions are provided for reference and documentation.
Runtime validation is not performed at the schema level; schemas are used
only as documentation of the expected manifest format.
"""

# Maximum manifest file size (64 KB as per design)
MAX_MANIFEST_FILE_SIZE = 64 * 1024

# JSON Schema for GX-PAP manifest v1.0 (documentation only)
MANIFEST_SCHEMA_V1 = {
    "$schema": "http://json-schema.org/draft-2020-12/schema#",
    "$id": "https://omega-guardian-x.dev/schemas/gx-pap-manifest-1.0.json",
    "title": "GX-PAP Manifest v1.0",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "project_path",
        "manifest_hash",
    ],
    "properties": {
        "schema_version": {"type": "string", "pattern": "^v?1\\.0$"},
        "project_path": {"type": "string"},
        "manifest_hash": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "metadata": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "version": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}

# JSON Schema for GX-PAP manifest v1.1 (documentation only)
MANIFEST_SCHEMA_V1_1 = {
    "$schema": "http://json-schema.org/draft-2020-12/schema#",
    "$id": "https://omega-guardian-x.dev/schemas/gx-pap-manifest-1.1.json",
    "title": "GX-PAP Manifest v1.1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "project_path",
        "manifest_hash",
        "risk_score",
    ],
    "properties": {
        "schema_version": {"type": "string", "pattern": "^v?1\\.1$"},
        "project_path": {"type": "string"},
        "manifest_hash": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "metadata": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "version": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}