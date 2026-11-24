"""Identity schema definitions for GX-PAP (Passive Analysis Plugin).

These JSON Schema definitions are provided for reference and documentation.
Runtime validation is not performed at the schema level; schemas are used
only as documentation of the expected identity format.
"""

# JSON Schema for GX-PAP identity v1.0 (documentation only)
IDENTITY_SCHEMA_V1 = {
    "$schema": "http://json-schema.org/draft-2020-12/schema#",
    "$id": "https://omega-guardian-x.dev/schemas/gx-pap-identity-1.0.json",
    "title": "GX-PAP Identity v1.0",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "type",
        "value",
    ],
    "properties": {
        "type": {"type": "string"},
        "value": {"type": "string"},
        "source": {"type": "string"},
        "verified": {"type": "boolean"},
    },
}

# JSON Schema for GX-PAP identity v1.1 (documentation only)
IDENTITY_SCHEMA_V1_1 = {
    "$schema": "http://json-schema.org/draft-2020-12/schema#",
    "$id": "https://omega-guardian-x.dev/schemas/gx-pap-identity-1.1.json",
    "title": "GX-PAP Identity v1.1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "type",
        "value",
        "source",
    ],
    "properties": {
        "type": {"type": "string"},
        "value": {"type": "string"},
        "source": {"type": "string"},
        "verified": {"type": "boolean"},
        "expires_at": {"type": "string", "format": "date-time"},
    },
}