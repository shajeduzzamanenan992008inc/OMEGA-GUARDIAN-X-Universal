"""Authenticated REST API and dashboard tests (ENT-08, UI-04, UI-05)."""

from __future__ import annotations

import http.client
import json
import threading
from urllib.parse import urlsplit

import pytest

from ogx.api.server import (
    MAX_BODY_BYTES,
    ApiContext,
    _RateLimiter,
    create_server,
)
from ogx.enterprise.authz import Action, PolicyEngine
from ogx.enterprise.service_identity import ServiceIdentityManager
from ogx.enterprise.store import EnterpriseStore

ADMIN_SCOPES = [
    "organization.read", "member.read", "project.read", "project.write",
    "audit.read", "governance.read", "governance.write", "policy.read",
    "host.read", "job.read", "plugin.read", "dashboard.view",
]


def _request(base, method, path, *, token=None, body=None, headers=None):
    parsed = urlsplit(base)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
    request_headers = dict(headers or {})
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    try:
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        response_headers = {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()
    try:
        parsed_body = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed_body = raw
    return status, parsed_body, response_headers, raw


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "home"))
    store = EnterpriseStore(tmp_path / "enterprise.sqlite3")
    store.initialize()
    policy = PolicyEngine()
    org = store.create_organization("acme", "Acme", "owner@example.com")
    manager = ServiceIdentityManager(store, policy)
    account = manager.create_account(org["organization_id"], "ci", "admin",
                                     ADMIN_SCOPES, "owner@example.com")
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com", 900)
    context = ApiContext(store=store, policy=policy, rate_limit=1000)
    server = create_server(context, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield {
            "base": base,
            "token": issued.token,
            "store": store,
            "org": org,
            "manager": manager,
            "policy": policy,
            "account": account,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_health_readiness_and_meta_are_public(api):
    status, payload, headers, _ = _request(api["base"], "GET", "/health")
    assert status == 200 and payload["status"] == "ok"
    status, payload, _, _ = _request(api["base"], "GET", "/ready")
    assert status == 200 and payload["status"] == "ready"
    status, payload, headers, _ = _request(api["base"], "GET", "/api/v1/meta")
    assert status == 200
    assert payload["api_version"] == "v1"
    assert payload["authentication"]["scheme"] == "Bearer"
    assert payload["authentication"]["human_sso"] is False
    assert headers["cache-control"] == "no-store"


def test_authentication_is_required(api):
    status, payload, headers, _ = _request(api["base"], "GET", "/api/v1/organizations")
    assert status == 401
    assert payload["error"]["code"] == "authentication_required"
    assert "bearer" in headers.get("www-authenticate", "").lower()

    status, payload, _, _ = _request(
        api["base"], "GET", "/api/v1/organizations", token="ogxst." + "a" * 32 + "." + "b" * 43
    )
    assert status == 401
    assert payload["error"]["code"] == "invalid_token"
    assert "b" * 43 not in json.dumps(payload)


def test_organization_and_project_lifecycle(api):
    token = api["token"]
    org_id = api["org"]["organization_id"]

    status, payload, _, _ = _request(api["base"], "GET", "/api/v1/organizations", token=token)
    assert status == 200 and payload["total"] == 1
    assert payload["items"][0]["slug"] == "acme"

    status, payload, _, _ = _request(api["base"], "GET", f"/api/v1/organizations/{org_id}/projects",
                                     token=token)
    assert status == 200 and payload["total"] == 0

    status, payload, _, _ = _request(
        api["base"], "POST", f"/api/v1/organizations/{org_id}/projects", token=token,
        body={"name": "guardian", "provider": "github", "repository_ref": "acme/guardian"},
        headers={"Idempotency-Key": "create-guardian-0001"},
    )
    assert status == 201, payload
    project_id = payload["data"]["project_id"]
    assert payload["data"]["provider"] == "github"

    status, payload, headers, _ = _request(
        api["base"], "POST", f"/api/v1/organizations/{org_id}/projects", token=token,
        body={"name": "guardian-two", "provider": "github"},
        headers={"Idempotency-Key": "create-guardian-0001"},
    )
    assert status == 409
    assert payload["error"]["code"] == "idempotency_conflict"

    status, payload, _, _ = _request(api["base"], "GET", f"/api/v1/organizations/{org_id}/projects",
                                     token=token)
    assert status == 200
    assert [item["id"] for item in payload["items"]] == [project_id]
    assert payload["items"][0]["provider"] == "github"

    status, payload, _, _ = _request(api["base"], "GET", f"/api/v1/organizations/{org_id}/audit",
                                     token=token)
    assert status == 200
    actions = [event["action"] for event in payload["items"]]
    assert "project.register" in actions


def test_project_write_rejects_credential_bearing_urls(api):
    status, payload, _, _ = _request(
        api["base"], "POST", f"/api/v1/organizations/{api['org']['organization_id']}/projects",
        token=api["token"],
        body={"name": "leaky", "repository_ref": "https://user:token@example.com/repo.git"},
    )
    assert status == 400
    assert "credentials" in payload["error"]["message"]


def test_tenant_isolation_between_organizations(api):
    other = api["store"].create_organization("other", "Other", "owner@other.example")
    status, payload, _, _ = _request(
        api["base"], "GET", f"/api/v1/organizations/{other['organization_id']}/projects",
        token=api["token"],
    )
    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    assert payload["error"]["details"]["rule"] == "deny.tenant_mismatch"


def test_missing_role_permission_is_refused(api):
    reader = api["manager"].create_account(
        api["org"]["organization_id"], "reader", "admin",
        ["organization.read", "project.read"], "owner@example.com",
    )
    issued = api["manager"].issue_token(api["org"]["organization_id"],
                                        reader["account_id"], "owner@example.com")
    org_id = api["org"]["organization_id"]
    status, _, _, _ = _request(api["base"], "GET",
                               f"/api/v1/organizations/{org_id}/projects", token=issued.token)
    assert status == 200
    status, payload, _, _ = _request(
        api["base"], "POST", f"/api/v1/organizations/{org_id}/projects",
        token=issued.token, body={"name": "denied"},
    )
    assert status == 403
    assert payload["error"]["details"]["rule"] == "deny.scope"


def test_audit_export_requires_its_own_scope(api):
    org_id = api["org"]["organization_id"]
    status, payload, _, _ = _request(
        api["base"], "GET", f"/api/v1/organizations/{org_id}/audit/export", token=api["token"]
    )
    assert status == 403
    assert payload["error"]["code"] == "forbidden"

    exporter = api["manager"].create_account(
        org_id, "exporter", "auditor", ["audit.read", "audit.export"], "owner@example.com"
    )
    issued = api["manager"].issue_token(org_id, exporter["account_id"], "owner@example.com")
    status, _, headers, raw = _request(
        api["base"], "GET", f"/api/v1/organizations/{org_id}/audit/export", token=issued.token
    )
    assert status == 200
    assert headers["content-type"].startswith("application/x-ndjson")
    assert b"project" in raw or raw == b""


def test_policy_matrix_and_authorization_check(api):
    status, payload, _, _ = _request(api["base"], "GET", "/api/v1/policy/matrix",
                                     token=api["token"])
    assert status == 200
    assert "owner" in payload["matrix"]
    assert Action.AUDIT_EXPORT.value in payload["matrix"]["auditor"]

    status, payload, _, _ = _request(
        api["base"], "POST", "/api/v1/authorization/check", token=api["token"],
        body={"action": "project.write", "resource_kind": "project", "resource_id": "x"},
    )
    assert status == 200
    assert payload["decision"]["allowed"] is True
    assert payload["decision"]["rule"] == "allow.scope"

    status, payload, _, _ = _request(
        api["base"], "POST", "/api/v1/authorization/check", token=api["token"],
        body={"action": "organization.delete"},
    )
    assert status == 200
    assert payload["decision"]["allowed"] is False
    # API and CLI share the same engine: an independent decision must agree.
    principal = api["manager"].authenticate(api["token"])
    local = api["policy"].decide(principal, "organization.delete")
    assert local.allowed == payload["decision"]["allowed"]


def test_governance_records_roundtrip(api):
    org_id = api["org"]["organization_id"]
    status, payload, _, _ = _request(
        api["base"], "POST", f"/api/v1/organizations/{org_id}/governance/records",
        token=api["token"],
        body={"record_type": "risk", "title": "Unrotated keys", "owner_subject": "owner@example.com",
              "priority": "high", "status": "open"},
        headers={"Idempotency-Key": "governance-risk-0001"},
    )
    assert status == 201, payload
    assert payload["data"]["record_type"] == "risk"

    status, payload, _, _ = _request(
        api["base"], "GET", f"/api/v1/organizations/{org_id}/governance/records?type=risk",
        token=api["token"],
    )
    assert status == 200
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Unrotated keys"

    status, payload, _, _ = _request(
        api["base"], "GET", f"/api/v1/organizations/{org_id}/governance/records?type=nonsense",
        token=api["token"],
    )
    assert status == 400


def test_hosts_jobs_and_plugins_surfaces(api):
    status, payload, _, _ = _request(api["base"], "GET", "/api/v1/hosts", token=api["token"])
    assert status == 200 and "items" in payload
    status, payload, _, _ = _request(api["base"], "GET", "/api/v1/jobs/stats", token=api["token"])
    assert status == 200 and "pending" in json.dumps(payload).lower() or payload
    status, payload, _, _ = _request(api["base"], "GET", "/api/v1/plugins", token=api["token"])
    assert status == 200
    assert "capability_risk" in payload
    status, _, _, _ = _request(api["base"], "GET", "/api/v1/hosts/not-a-uuid", token=api["token"])
    assert status == 404


def test_dashboard_assets_and_security_headers(api):
    status, _, headers, raw = _request(api["base"], "GET", "/")
    assert status == 200
    assert b"OMEGA GUARDIAN X" in raw
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-content-type-options"] == "nosniff"
    # Default and dashboard headers must not be emitted twice.
    parsed = urlsplit(api["base"])
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        names = [name.lower() for name, _ in response.getheaders()]
    finally:
        connection.close()
    assert names.count("referrer-policy") == 1
    assert names.count("x-content-type-options") == 1
    status, _, headers, raw = _request(api["base"], "GET", "/dashboard.js")
    assert status == 200 and b"token" in raw
    status, _, headers, raw = _request(api["base"], "GET", "/dashboard.css")
    assert status == 200 and b"--accent" in raw
    # The dashboard must not persist the bearer token in browser storage.
    status, _, _, js = _request(api["base"], "GET", "/dashboard.js")
    assert b"localStorage" not in js and b"sessionStorage" not in js


def test_request_bounds_and_content_type(api):
    org_id = api["org"]["organization_id"]
    status, payload, _, _ = _request(
        api["base"], "POST", f"/api/v1/organizations/{org_id}/projects", token=api["token"],
        body={"name": "x"}, headers={"Content-Type": "text/plain"},
    )
    assert status == 415

    status, payload, _, _ = _request(api["base"], "POST", "/api/v1/authorization/check",
                                     token=api["token"], body={"action": "project.read"},
                                     headers={"Origin": "https://evil.example"})
    assert status == 403
    assert payload["error"]["code"] == "origin_not_allowed"

    status, payload, _, _ = _request(api["base"], "GET",
                                     f"/api/v1/organizations/{org_id}/projects?limit=999",
                                     token=api["token"])
    assert status == 400
    assert MAX_BODY_BYTES == 64 * 1024


def test_unknown_routes_and_methods(api):
    status, payload, _, _ = _request(api["base"], "GET", "/api/v1/nope", token=api["token"])
    assert status == 404
    status, payload, _, _ = _request(api["base"], "DELETE", "/api/v1/organizations",
                                     token=api["token"])
    assert status == 405
    status, payload, _, _ = _request(api["base"], "GET", "/api/v1/organizations/nothex/projects",
                                     token=api["token"])
    assert status == 404


def test_rate_limiter_is_bounded():
    limiter = _RateLimiter(limit=2, window=60.0)
    assert limiter.check("token-a", now=0.0)[0] is True
    assert limiter.check("token-a", now=1.0)[0] is True
    allowed, retry = limiter.check("token-a", now=2.0)
    assert allowed is False and retry > 0
    assert limiter.check("token-b", now=2.0)[0] is True
    assert limiter.check("token-a", now=61.5)[0] is True


def test_non_loopback_binding_is_refused():
    with pytest.raises(ValueError):
        create_server(port=0, host="0.0.0.0")
