"""Generated OpenAPI description of the implemented API (ENT-08).

The document is built from the same constants the server routes with, so it
cannot silently drift into describing endpoints that do not exist. It documents
only what is implemented today: bearer service-identity authentication, the
tenant-scoped read/write routes, pagination, idempotency and the error schema.
Human SSO, TLS termination and webhook callbacks are explicitly absent.
"""

from __future__ import annotations

from typing import Any

from . import API_VERSION
from .server import MAX_BODY_BYTES, MAX_PAGE_LIMIT

_ERROR_SCHEMA = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message", "request_id"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": "string"},
                "details": {"type": "object", "additionalProperties": True},
            },
        }
    },
}

_PAGE_PARAMS = [
    {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1,
                                                 "maximum": MAX_PAGE_LIMIT, "default": 50}},
    {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0,
                                                  "default": 0}},
]

_ORG = [
    {"name": "organization_id", "in": "path", "required": True,
     "schema": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"}},
]


def _ok(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "200": {
            "description": "Success",
            "content": {"application/json": {"schema": schema or {"type": "object"}}},
        },
        "401": {"description": "Authentication required or token invalid",
                "content": {"application/json": {"schema": _ERROR_SCHEMA}}},
        "403": {"description": "Denied by policy",
                "content": {"application/json": {"schema": _ERROR_SCHEMA}}},
    }


def _operation(summary: str, *, tag: str, params: list[dict[str, Any]] | None = None,
               body: dict[str, Any] | None = None, extra: dict[str, Any] | None = None,
               status: str = "200") -> dict[str, Any]:
    responses = _ok()
    if status != "200":
        responses = {status: responses.pop("200"), **responses}
    operation: dict[str, Any] = {"summary": summary, "tags": [tag], "responses": responses}
    if params:
        operation["parameters"] = params
    if body is not None:
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": body}},
        }
        operation["responses"]["201"] = {"description": "Created"}
        operation["responses"]["400"] = {"description": "Invalid request"}
    if extra:
        operation.update(extra)
    return operation


def build_spec(*, server_url: str | None = None, version: str) -> dict[str, Any]:
    prefix = f"/api/{API_VERSION}"
    project_body = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "maxLength": 128},
            "provider": {"type": "string"},
            "repository_ref": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    }
    governance_body = {
        "type": "object",
        "required": ["record_type", "title", "owner_subject"],
        "properties": {
            "record_type": {"type": "string"},
            "title": {"type": "string"},
            "owner_subject": {"type": "string"},
            "priority": {"type": "string"},
            "status": {"type": "string"},
            "due_date": {"type": ["string", "null"]},
            "details": {"type": "object"},
        },
        "additionalProperties": False,
    }
    paths: dict[str, Any] = {
        "/health": {"get": {"summary": "Liveness probe", "tags": ["operations"],
                            "responses": {"200": {"description": "Alive"}}}},
        "/ready": {"get": {"summary": "Readiness probe", "tags": ["operations"],
                           "responses": {"200": {"description": "Ready"},
                                         "503": {"description": "Not ready"}}}},
        f"{prefix}/meta": {"get": {"summary": "Product and API metadata",
                                   "tags": ["operations"],
                                   "responses": {"200": {"description": "Metadata"}}}},
        f"{prefix}/openapi.json": {"get": {
            "summary": "This document", "tags": ["operations"],
            "responses": {"200": {"description": "OpenAPI description"}},
        }},
        f"{prefix}/metrics": {"get": {
            "summary": "Process metrics (JSON, or Prometheus text with format=prometheus)",
            "tags": ["operations"],
            "parameters": [{"name": "format", "in": "query",
                            "schema": {"type": "string", "enum": ["json", "prometheus"],
                                       "default": "json"}}],
            "responses": _ok(),
        }},
        f"{prefix}/policy/matrix": {"get": {
            "summary": "Role/action permission matrix", "tags": ["policy"],
            "responses": _ok(),
        }},
        f"{prefix}/authorization/check": {"post": _operation(
            "Decide one action for the authenticated principal", tag="policy",
            body={"type": "object", "required": ["action"],
                  "properties": {"action": {"type": "string"},
                                 "resource_kind": {"type": "string"},
                                 "resource_id": {"type": "string"}}},
        )},
        f"{prefix}/organizations": {"get": {
            "summary": "Organizations visible to the caller", "tags": ["organizations"],
            "responses": _ok(),
        }},
        f"{prefix}/organizations/{{organization_id}}": {"get": {
            "summary": "One organization", "tags": ["organizations"],
            "parameters": _ORG, "responses": _ok(),
        }},
        f"{prefix}/organizations/{{organization_id}}/projects": {
            "get": {
                "summary": "List projects (paginated)", "tags": ["projects"],
                "parameters": _ORG + _PAGE_PARAMS, "responses": _ok(),
            },
            "post": _operation(
                "Register a project", tag="projects", params=_ORG, body=project_body,
                extra={"parameters": _ORG + [
                    {"name": "Idempotency-Key", "in": "header", "required": False,
                     "schema": {"type": "string", "minLength": 8, "maxLength": 128}},
                ]},
                status="201",
            ),
        },
        f"{prefix}/organizations/{{organization_id}}/members": {"get": {
            "summary": "List members (paginated)", "tags": ["members"],
            "parameters": _ORG + _PAGE_PARAMS, "responses": _ok(),
        }},
        f"{prefix}/organizations/{{organization_id}}/audit": {"get": {
            "summary": "List audit events (paginated)", "tags": ["audit"],
            "parameters": _ORG + _PAGE_PARAMS, "responses": _ok(),
        }},
        f"{prefix}/organizations/{{organization_id}}/audit/export": {"get": {
            "summary": "Export audit events as NDJSON", "tags": ["audit"],
            "parameters": _ORG, "responses": {
                "200": {"description": "NDJSON stream",
                        "content": {"application/x-ndjson": {"schema": {"type": "string"}}}},
                "403": {"description": "Denied"},
            },
        }},
        f"{prefix}/organizations/{{organization_id}}/governance/records": {
            "get": {
                "summary": "List governance records", "tags": ["governance"],
                "parameters": _ORG + _PAGE_PARAMS + [
                    {"name": "type", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": _ok(),
            },
            "post": _operation("Create a governance record", tag="governance",
                               params=_ORG, body=governance_body, status="201"),
        },
        f"{prefix}/hosts": {"get": {
            "summary": "Registered hosts with capabilities", "tags": ["hosts"],
            "responses": _ok(),
        }},
        f"{prefix}/hosts/{{host_id}}": {"get": {
            "summary": "One host", "tags": ["hosts"],
            "parameters": [{"name": "host_id", "in": "path", "required": True,
                            "schema": {"type": "string"}}],
            "responses": _ok(),
        }},
        f"{prefix}/jobs/stats": {"get": {
            "summary": "Local job queue statistics", "tags": ["jobs"],
            "responses": _ok(),
        }},
        f"{prefix}/plugins": {"get": {
            "summary": "Installed plugins and capability risk", "tags": ["plugins"],
            "responses": _ok(),
        }},
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Omega Guardian X API",
            "version": version,
            "description": (
                "Authenticated multi-tenant control-plane API. Authentication is a "
                "service-identity bearer token; there is no human SSO in this build. "
                "The service binds to loopback and must be published through a TLS "
                "reverse proxy."
            ),
        },
        "servers": [{"url": server_url or "http://127.0.0.1:8765"}],
        "security": [{"bearerAuth": []}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer",
                               "bearerFormat": "ogxst.<id>.<secret>"},
            },
            "schemas": {"Error": _ERROR_SCHEMA},
        },
        "paths": paths,
        "x-ogx-limits": {
            "max_body_bytes": MAX_BODY_BYTES,
            "page_limit": MAX_PAGE_LIMIT,
            "human_sso": False,
        },
    }


__all__ = ["build_spec"]
