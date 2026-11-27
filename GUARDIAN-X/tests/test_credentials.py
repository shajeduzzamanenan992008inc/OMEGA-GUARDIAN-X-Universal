"""Tests for PROV-07 token refresh and rotation (offline only)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ogx.platform.credentials import (
    EXPIRY_SKEW_SECONDS,
    PROVIDER_REFRESH_SUPPORT,
    CredentialHandle,
    CredentialRotationError,
    RefreshClient,
    RefreshKind,
    RefreshUnsupported,
    RotationStore,
    fingerprint,
    refresh_support,
)
from ogx.platform.errors import TransportError
from tests._helpers import response

PROVIDERS = (
    "github",
    "gitlab",
    "bitbucket",
    "gitea",
    "forgejo",
    "azure_devops",
    "aws_codecommit",
)


class FakeTransport:
    def __init__(self, *, payload=None, body=None, status=200, raise_exc=None):
        self.payload = payload
        self.body = body
        self.status = status
        self.raise_exc = raise_exc
        self.calls: list[tuple] = []

    def send(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.raise_exc:
            raise self.raise_exc
        if self.body is not None:
            return response(status=self.status, body=self.body)
        return response(status=self.status, body=json.dumps(self.payload or {}).encode())


def test_every_supported_provider_is_declared_exactly_once():
    assert set(PROVIDER_REFRESH_SUPPORT) == set(PROVIDERS)
    for provider_id, support in PROVIDER_REFRESH_SUPPORT.items():
        assert support.provider_id == provider_id
        assert support.credential_policy.provider_id == provider_id
        assert support.notes
        if support.supports_refresh:
            assert support.token_endpoint
        else:
            assert support.token_endpoint is None
            assert support.kind == RefreshKind.MANUAL


def test_refresh_capability_matches_documented_provider_behaviour():
    assert refresh_support("gitlab").kind == RefreshKind.OAUTH2
    assert refresh_support("bitbucket").kind == RefreshKind.OAUTH2
    assert refresh_support("github").kind == RefreshKind.GITHUB_APP
    assert refresh_support("aws_codecommit").kind == RefreshKind.AWS_STS
    for manual in ("gitea", "forgejo", "azure_devops"):
        assert refresh_support(manual).supports_refresh is False
    with pytest.raises(CredentialRotationError, match="Unknown provider"):
        refresh_support("sourcehut-not-registered")


def test_support_declaration_never_contains_a_secret():
    for support in PROVIDER_REFRESH_SUPPORT.values():
        payload = json.dumps(support.to_dict())
        assert "sk-" not in payload and "Bearer " not in payload
        assert all(name.isupper() for name in support.credential_policy.env_vars)


@pytest.mark.parametrize("provider", ["gitea", "forgejo", "azure_devops", "github", "aws_codecommit"])
def test_refresh_is_refused_where_no_oauth2_flow_exists(provider):
    client = RefreshClient(transport=FakeTransport(payload={}), provider_id=provider)
    with pytest.raises(RefreshUnsupported) as excinfo:
        client.refresh(client_id="cid", refresh_token="rt")
    assert "no OAuth2 refresh" in str(excinfo.value)


def test_gitlab_refresh_uses_the_documented_form_and_bounds_the_result():
    transport = FakeTransport(payload={"access_token": "new-token", "expires_in": 7200})
    client = RefreshClient(transport=transport, provider_id="gitlab")
    handle = client.refresh(client_id="cid", refresh_token="rt", client_secret="cs", now=1000.0)
    assert handle.secret == "new-token"
    assert handle.expires_at == 1000.0 + 7200
    method, url, kwargs = transport.calls[0]
    assert method == "POST"
    assert url == "https://gitlab.com/oauth/token"
    assert kwargs["write"] is True
    assert b"grant_type=refresh_token" in kwargs["raw_body"]
    assert b"refresh_token=rt" in kwargs["raw_body"]
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert handle.is_expired(now=1000.0) is False
    assert handle.is_expired(now=1000.0 + 7200 - EXPIRY_SKEW_SECONDS + 1) is True


def test_bitbucket_refresh_uses_its_own_endpoint():
    transport = FakeTransport(payload={"access_token": "bb-token", "expires_in": 3600})
    handle = RefreshClient(transport=transport, provider_id="bitbucket").refresh(
        client_id="id", refresh_token="rt"
    )
    assert handle.provider_id == "bitbucket"
    assert transport.calls[0][1] == "https://bitbucket.org/site/oauth2/access_token"


def test_refresh_errors_never_echo_credentials():
    transport = FakeTransport(
        raise_exc=TransportError("oauth failed: rt-super-secret cs-secret")
    )
    client = RefreshClient(transport=transport, provider_id="gitlab")
    with pytest.raises(CredentialRotationError) as excinfo:
        client.refresh(client_id="cid", refresh_token="rt-super-secret", client_secret="cs-secret")
    message = str(excinfo.value)
    assert "rt-super-secret" not in message
    assert "cs-secret" not in message


def test_refresh_rejects_malformed_responses():
    for payload in ({"expires_in": 10}, {"access_token": ""}):
        client = RefreshClient(transport=FakeTransport(payload=payload), provider_id="gitlab")
        with pytest.raises(CredentialRotationError, match="no access token"):
            client.refresh(client_id="cid", refresh_token="rt")
    client = RefreshClient(transport=FakeTransport(body=b"<html>nope"), provider_id="gitlab")
    with pytest.raises(CredentialRotationError, match="not valid JSON"):
        client.refresh(client_id="cid", refresh_token="rt")


def test_handle_never_reveals_the_secret_in_repr_or_dict():
    handle = CredentialHandle(
        provider_id="gitlab", secret="sk-live-secret", issued_at=0.0, expires_at=None
    )
    assert "sk-live-secret" not in repr(handle)
    payload = json.dumps(handle.to_dict())
    assert "sk-live-secret" not in payload
    assert handle.to_dict()["fingerprint"] == fingerprint("sk-live-secret")


def test_rotation_store_records_only_fingerprints(tmp_path: Path):
    store = RotationStore(tmp_path)
    handle = CredentialHandle(
        provider_id="gitlab", secret="rotated-secret", issued_at=time.time(), expires_at=None
    )
    record = store.record(handle)
    assert record.fingerprint == fingerprint("rotated-secret")
    assert store.current("gitlab") is not None
    assert store.is_current(handle) is True
    raw = (tmp_path / "credentials" / "rotations.json").read_text(encoding="utf-8")
    assert "rotated-secret" not in raw
    if hasattr(Path, "stat"):
        try:
            mode = (tmp_path / "credentials" / "rotations.json").stat().st_mode & 0o777
        except OSError:
            mode = 0o600
        assert mode in {0o600, 0o666, 0o644}  # Windows ignores chmod; POSIX must be 0600

    stale = CredentialHandle(
        provider_id="gitlab", secret="older-secret", issued_at=0.0, expires_at=None
    )
    assert store.is_current(stale) is False

    assert store.revoke("gitlab") is True
    assert store.current("gitlab") is None
    assert store.revoke("gitlab") is False


def test_rotation_store_rejects_a_malformed_file(tmp_path: Path):
    path = tmp_path / "credentials" / "rotations.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CredentialRotationError, match="unreadable"):
        RotationStore(tmp_path).current("gitlab")
