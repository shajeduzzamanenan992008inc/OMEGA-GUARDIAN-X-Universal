"""Cloud workspace host adapter tests (HOST-11), mocked transport only."""

from __future__ import annotations

import pytest

from ogx.hosts.adapters.cloud_runtime import (
    CloudCredentialMissing,
    CloudProviderUnsupported,
    CloudRequestError,
    CloudResponseError,
    CloudWorkspaceHostAdapter,
    _redact,
)
from ogx.hosts.adapters.runtime_factory import create_host_adapter
from ogx.hosts.capabilities import get_host_capabilities, get_supported_capabilities
from ogx.hosts.lifecycle import HealthState, HostSessionConfig
from ogx.hosts.models import HostType

CODESPACE = {
    "name": "fluffy-space-parakeet",
    "display_name": "Guardian dev",
    "state": "Available",
    "repository": {"full_name": "acme/guardian"},
    "machine": {"name": "standardLinux32gb", "display_name": "4 cores, 16 GB RAM, 32 GB storage",
                "operating_system": "linux"},
    "git_status": {"ref": "refs/heads/main"},
    "location": "WestUs2",
    "web_url": "https://example.github.dev",
    "created_at": "2026-11-01T10:00:00Z",
    "last_used_at": "2026-11-27T09:00:00Z",
    "devcontainer_path": ".devcontainer/devcontainer.json",
    "environment_secrets": ["DO_NOT_LEAK"],
}


class StubResponse:
    def __init__(self, status=200, payload=None, raise_on_json=None):
        self.status = status
        self._payload = payload
        self._raise_on_json = raise_on_json

    def json(self, *, provider=None):
        if self._raise_on_json is not None:
            raise self._raise_on_json
        return self._payload

    def header(self, name, default=""):
        return default

    def text(self):
        return ""


class StubTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


TOKEN = "ghp_" + "a" * 30


def _adapter(responses, *, root="acme/guardian", metadata=None, token=TOKEN, transport=None):
    config = HostSessionConfig(root=root, metadata=dict(metadata or {}),
                               timeout_seconds=5)
    return CloudWorkspaceHostAdapter(
        config,
        transport=transport or StubTransport(responses),
        environ={"OGX_GITHUB_TOKEN": token} if token else {},
    )


def test_validate_requires_a_credential_and_a_known_provider():
    adapter = _adapter([], token=None)
    with pytest.raises(CloudCredentialMissing):
        adapter.validate()

    unsupported = _adapter([], metadata={"provider": "gitpod"})
    with pytest.raises(CloudProviderUnsupported):
        unsupported.validate()

    assert _adapter([], metadata={"provider": "github_codespaces"}).capabilities()[
        "workspace_read"] == "supported"


def test_connect_probes_the_repository_scope_and_lists_codespaces():
    transport = StubTransport([StubResponse(200, {"codespaces": [CODESPACE]}),
                               StubResponse(200, {"codespaces": [CODESPACE]}),
                               StubResponse(200, {"codespaces": [CODESPACE]})])
    adapter = _adapter([], transport=transport)
    adapter.connect()
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/repos/acme/guardian/codespaces")
    assert call["expected_host"] == "api.github.com"
    assert call["headers"]["Authorization"].startswith("Bearer ")
    assert "?" not in call["url"]  # no credentials or query leakage in the URL

    result = adapter.list_workspaces(10)
    assert result["total"] == 1
    item = result["items"][0]
    assert item["workspace_id"] == "fluffy-space-parakeet"
    assert item["repository"] == "acme/guardian"
    assert item["machine"] == "standardLinux32gb"
    assert item["branch"] == "refs/heads/main"
    assert "DO_NOT_LEAK" not in str(result)
    assert item["capabilities"] == ["workspace_read"]
    assert adapter.health().state in {HealthState.HEALTHY, HealthState.UNKNOWN}


def test_user_scope_when_no_repository_is_configured():
    transport = StubTransport([StubResponse(200, {"codespaces": [CODESPACE]})])
    adapter = _adapter([], root="codespaces", transport=transport)
    adapter.connect()
    assert transport.calls[0]["url"].endswith("/user/codespaces")


def test_capabilities_are_truthful_and_never_overclaim():
    adapter = _adapter([])
    capabilities = adapter.capabilities()
    supported = [name for name, state in capabilities.items() if state == "supported"]
    assert supported == ["workspace_read"]
    for unimplemented in ("workspace_exec", "workspace_create", "workspace_delete",
                          "workspace_start", "workspace_stop", "file_read", "file_write"):
        assert capabilities[unimplemented] == "unsupported"
    matrix = get_host_capabilities(HostType.CLOUD_WORKSPACE.value)
    assert matrix["workspace_read"] == "supported"
    assert get_supported_capabilities(HostType.CLOUD_WORKSPACE.value) == ["workspace_read"]
    assert get_supported_capabilities("cloud_workspace") == ["workspace_read"]


def test_factory_builds_the_cloud_adapter():
    config = HostSessionConfig(root="acme/guardian")
    adapter = create_host_adapter("cloud_workspace", config)
    assert isinstance(adapter, CloudWorkspaceHostAdapter)
    assert adapter.host_type == "cloud_workspace"


def test_permission_denial_is_reported_without_leaking_the_token():
    adapter = _adapter([StubResponse(403, {"message": "Resource not accessible"})])
    with pytest.raises(CloudRequestError) as caught:
        adapter.connect()
    assert caught.value.code == "cloud_permission_denied"
    assert TOKEN not in str(caught.value)
    assert "ghp_" not in str(caught.value)


def test_malformed_and_non_json_responses_fail_closed():
    adapter = _adapter([StubResponse(200, {"unexpected": []})])
    with pytest.raises(CloudResponseError):
        adapter.connect()

    adapter = _adapter([StubResponse(200, None, raise_on_json=ValueError("bad json"))])
    with pytest.raises(CloudResponseError):
        adapter.connect()

    adapter = _adapter([StubResponse(200, {"codespaces": ["not-an-object"]})])
    with pytest.raises(CloudResponseError):
        adapter.connect()


def test_transport_failure_is_bounded_and_redacted():
    class Boom:
        def send(self, *args, **kwargs):
            raise OSError("connect to 10.0.0.1 failed")

    adapter = _adapter([], transport=Boom())
    with pytest.raises(CloudRequestError) as caught:
        adapter.connect()
    assert caught.value.code == "cloud_request_failed"
    assert "10.0.0.1" not in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_inspection_requires_connection_and_valid_names():
    adapter = _adapter([
        StubResponse(200, {"codespaces": [CODESPACE]}),
        StubResponse(200, CODESPACE),
    ])
    with pytest.raises(CloudRequestError):
        adapter.list_workspaces(5)
    with pytest.raises(CloudRequestError):
        adapter.inspect_workspace("fluffy-space-parakeet")

    adapter.connect()
    inspected = adapter.inspect_workspace("fluffy-space-parakeet")
    assert inspected["state"] == "Available"
    with pytest.raises(Exception):
        adapter.inspect_workspace("../escape")


def test_connect_rejects_an_empty_credential():
    adapter = _adapter([], token="")
    with pytest.raises(CloudCredentialMissing):
        adapter.connect()


def test_redact_hides_github_tokens():
    text = _redact("failed with ghp_abcdefghijklmnop and github_pat_abcdefghijklmnop")
    assert "ghp_" not in text and "github_pat_" not in text


def test_health_reports_unreachable_when_the_probe_fails():
    adapter = _adapter([StubResponse(200, {"codespaces": []})])
    adapter.connect()
    adapter._transport = StubTransport([StubResponse(500, {})])
    health = adapter.health()
    assert health.state == HealthState.UNREACHABLE
    assert health.issues
