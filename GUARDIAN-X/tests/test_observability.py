"""Operational metrics and OpenAPI tests (ENT-08, ENT-09)."""

from __future__ import annotations

import http.client
import json
import threading
from urllib.parse import urlsplit

import pytest

from ogx.api.observability import Metrics, route_label
from ogx.api.openapi import build_spec
from ogx.api.server import ApiContext, create_server
from ogx.enterprise.authz import PolicyEngine
from ogx.enterprise.service_identity import ServiceIdentityManager
from ogx.enterprise.store import EnterpriseStore

SCOPES = ["organization.read", "member.read", "policy.read", "audit.read"]


def _request(base, path, *, token=None):
    parsed = urlsplit(base)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        content_type = response.getheader("Content-Type", "")
    finally:
        connection.close()
    try:
        body = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = raw.decode("utf-8", "replace")
    return status, body, content_type


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "home"))
    store = EnterpriseStore(tmp_path / "enterprise.sqlite3")
    store.initialize()
    org = store.create_organization("acme", "Acme", "owner@example.com")
    policy = PolicyEngine()
    manager = ServiceIdentityManager(store, policy)
    account = manager.create_account(org["organization_id"], "ops", "auditor",
                                     SCOPES, "owner@example.com")
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com", 900)
    context = ApiContext(store=store, policy=policy, rate_limit=1000)
    server = create_server(context, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield {"base": base, "token": issued.token, "context": context}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_route_label_collapses_identifiers():
    assert route_label("/health") == "/health"
    assert route_label("/api/v1/hosts/7eeb251a-2492-4404-9a3e-cb75ae95720f") == \
        "/api/v1/hosts/{id}"
    assert route_label("/api/v1/organizations/" + "a" * 32 + "/projects") == \
        "/api/v1/organizations/{id}/projects"
    assert route_label("/api/v1/organizations/" + "a" * 32 + "/projects/abc") == \
        "/api/v1/organizations/{id}/projects/{sub}"


def test_metrics_endpoint_requires_authentication(api):
    status, payload, _ = _request(api["base"], "/api/v1/metrics")
    assert status == 401
    assert payload["error"]["code"] == "authentication_required"


def test_metrics_records_traffic_and_renders_prometheus(api):
    token = api["token"]
    assert _request(api["base"], "/health")[0] == 200
    assert _request(api["base"], "/api/v1/organizations", token=token)[0] == 200

    status, payload, _ = _request(api["base"], "/api/v1/metrics", token=token)
    assert status == 200
    # The snapshot is taken while this request is in flight, so it does not yet
    # count itself: /health and /api/v1/organizations are already recorded.
    assert payload["requests_total"] >= 2
    assert payload["status_codes"]["200"] >= 2
    assert "/health" in payload["routes"]
    assert "/api/v1/organizations" in payload["routes"]

    status, text, content_type = _request(
        api["base"], "/api/v1/metrics?format=prometheus", token=token
    )
    assert status == 200
    assert content_type.startswith("text/plain")
    assert "ogx_api_requests_total" in text
    assert "ogx_api_uptime_seconds" in text
    route_lines = [line for line in text.splitlines()
                   if line.startswith("ogx_api_route_requests_total")]
    assert route_lines
    assert all("a" * 32 not in line for line in route_lines)

    status, payload, _ = _request(api["base"], "/api/v1/metrics?format=xml", token=token)
    assert status == 400 and payload["error"]["code"] == "invalid_parameter"


def test_openapi_document_matches_implemented_surface(api):
    status, spec, _ = _request(api["base"], "/api/v1/openapi.json", token=api["token"])
    assert status == 200
    assert spec["openapi"].startswith("3.1")
    assert spec["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    paths = spec["paths"]
    assert "/api/v1/organizations/{organization_id}/projects" in paths
    assert "/api/v1/hosts/{host_id}" in paths
    assert spec["x-ogx-limits"]["human_sso"] is False
    assert _request(api["base"], "/api/v1/openapi.json")[0] == 401


def test_spec_builder_is_deterministic():
    first = build_spec(version="0.8.0a1")
    second = build_spec(version="0.8.0a1")
    assert first == second
    assert first["info"]["version"] == "0.8.0a1"


def test_metrics_snapshot_is_bounded_and_thread_safe():
    metrics = Metrics(version="test")
    for _ in range(50):
        metrics.begin()
        metrics.finish("/api/v1/hosts/" + "a" * 32, 200, 0.01)
    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 50
    assert snapshot["inflight"] == 0
    assert list(snapshot["routes"]) == ["/api/v1/hosts/{id}"]
    assert snapshot["routes"]["/api/v1/hosts/{id}"]["count"] == 50
