"""Inbound webhook authenticity: constant-time comparison, bounds and replay."""

from __future__ import annotations

import json

import pytest

from ogx.platform.webhooks import (
    DeliveryReplayGuard,
    WebhookVerificationError,
    WebhookVerifier,
    sign_payload,
)

SECRET = "a-very-long-webhook-secret-value"


def _verify(provider="github", body=b'{"ref":"refs/heads/main"}', secret=SECRET, **kwargs):
    headers = kwargs.pop("headers", None)
    verifier = WebhookVerifier(provider, secret, **kwargs)
    return verifier.verify(headers if headers is not None else _headers(provider, body, secret), body)


def _headers(provider, body, secret=SECRET, *, event="push", delivery="delivery-1"):
    signature = sign_payload(provider, secret, body)
    mapping = {
        "github": {"X-Hub-Signature-256": signature, "X-GitHub-Event": event, "X-GitHub-Delivery": delivery},
        "gitlab": {"X-Gitlab-Token": signature, "X-Gitlab-Event": event, "X-Gitlab-Event-UUID": delivery},
        "bitbucket": {"X-Hub-Signature": signature, "X-Event-Key": event, "X-Request-UUID": delivery},
        "gitea": {"X-Gitea-Signature": signature, "X-Gitea-Event": event, "X-Gitea-Delivery": delivery},
        "forgejo": {"X-Forgejo-Signature": signature, "X-Forgejo-Event": event, "X-Forgejo-Delivery": delivery},
    }
    return mapping[provider]


@pytest.mark.parametrize("provider", ["github", "gitlab", "bitbucket", "gitea", "forgejo"])
def test_valid_delivery_is_accepted(provider):
    result = _verify(provider)
    assert result.provider_id == provider
    assert result.event == "push"
    assert result.delivery_id == "delivery-1"
    assert result.json() == {"ref": "refs/heads/main"}
    assert len(result.digest) == 64


@pytest.mark.parametrize("provider", ["github", "bitbucket", "gitea", "forgejo"])
def test_tampered_body_is_rejected(provider):
    headers = _headers(provider, b'{"ref":"refs/heads/main"}')
    verifier = WebhookVerifier(provider, SECRET)
    with pytest.raises(WebhookVerificationError) as error:
        verifier.verify(headers, b'{"ref":"refs/heads/main","extra":1}')
    assert error.value.code == "invalid_signature"


def test_gitlab_token_mismatch_is_rejected():
    headers = _headers("gitlab", b"{}")
    headers["X-Gitlab-Token"] = "x" * len(SECRET)
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("gitlab", SECRET).verify(headers, b"{}")
    assert error.value.code == "invalid_signature"


def test_missing_signature_header_is_rejected():
    headers = _headers("github", b"{}")
    headers.pop("X-Hub-Signature-256")
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", SECRET).verify(headers, b"{}")
    assert error.value.code == "missing_signature"


def test_wrong_algorithm_prefix_is_rejected():
    body = b"{}"
    headers = _headers("github", body)
    headers["X-Hub-Signature-256"] = "sha1=" + sign_payload("github", SECRET, body).split("=", 1)[1]
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", SECRET).verify(headers, body)
    assert error.value.code == "invalid_signature_format"


def test_truncated_hex_signature_is_rejected():
    body = b"{}"
    headers = _headers("gitea", body)
    headers["X-Gitea-Signature"] = headers["X-Gitea-Signature"][:32]
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("gitea", SECRET).verify(headers, body)
    assert error.value.code == "invalid_signature_format"


def test_body_size_limit_is_enforced_before_hashing():
    body = b"x" * 64
    headers = _headers("github", body)
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", SECRET, max_body_bytes=32).verify(headers, body)
    assert error.value.code == "body_too_large"


def test_text_bodies_are_refused():
    headers = _headers("github", b"{}")
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", SECRET).verify(headers, "{}")
    assert error.value.code == "invalid_body"


def test_event_allowlist_is_enforced():
    headers = _headers("github", b"{}", event="push")
    verifier = WebhookVerifier("github", SECRET, allowed_events={"pull_request"})
    with pytest.raises(WebhookVerificationError) as error:
        verifier.verify(headers, b"{}")
    assert error.value.code == "event_not_allowed"


def test_missing_event_header_is_rejected():
    headers = _headers("github", b"{}")
    headers.pop("X-GitHub-Event")
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", SECRET).verify(headers, b"{}")
    assert error.value.code == "missing_event"


def test_short_secret_is_refused():
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", "short")
    assert error.value.code == "secret_too_short"


def test_missing_secret_is_refused():
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", None)
    assert error.value.code == "secret_required"


@pytest.mark.parametrize("provider", ["azure_devops", "aws_codecommit"])
def test_unsigned_providers_are_not_advertised(provider):
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier(provider, SECRET)
    assert error.value.code == "unsupported_provider"


def test_unknown_provider_is_refused():
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("no-such-provider", SECRET)
    assert error.value.code == "unknown_provider"


def test_errors_never_contain_the_secret():
    headers = _headers("github", b"{}")
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    with pytest.raises(WebhookVerificationError) as error:
        WebhookVerifier("github", SECRET).verify(headers, b"{}")
    assert SECRET not in str(error.value)
    assert "0" * 64 not in str(error.value)


def test_replay_guard_rejects_a_repeated_delivery():
    body = b"{}"
    headers = _headers("github", body, delivery="same-id")
    verifier = WebhookVerifier("github", SECRET, replay_guard=DeliveryReplayGuard())
    verifier.verify(headers, body)
    with pytest.raises(WebhookVerificationError) as error:
        verifier.verify(headers, body)
    assert error.value.code == "replay_detected"


def test_replay_guard_allows_after_the_window():
    now = [1000.0]
    guard = DeliveryReplayGuard(window_seconds=60, clock=lambda: now[0])
    assert guard.check_and_remember("github:one") is True
    assert guard.check_and_remember("github:one") is False
    now[0] += 61
    assert guard.check_and_remember("github:one") is True


def test_replay_guard_is_bounded():
    guard = DeliveryReplayGuard(window_seconds=3600, max_entries=2)
    assert guard.check_and_remember("a")
    assert guard.check_and_remember("b")
    assert guard.check_and_remember("c")
    # The oldest surviving entry was evicted, so it is accepted again.
    assert guard.check_and_remember("a") is True


def test_require_delivery_id_is_enforced():
    body = b"{}"
    headers = _headers("github", body)
    headers.pop("X-GitHub-Delivery")
    verifier = WebhookVerifier("github", SECRET, require_delivery_id=True)
    with pytest.raises(WebhookVerificationError) as error:
        verifier.verify(headers, body)
    assert error.value.code == "missing_delivery_id"


def test_authenticated_non_json_body_fails_cleanly():
    body = b"not json"
    headers = _headers("github", body)
    result = WebhookVerifier("github", SECRET).verify(headers, body)
    with pytest.raises(WebhookVerificationError) as error:
        result.json()
    assert error.value.code == "invalid_json"


def test_signed_payload_helper_matches_scheme():
    body = json.dumps({"ok": True}).encode()
    assert sign_payload("github", SECRET, body).startswith("sha256=")
    assert len(sign_payload("gitea", SECRET, body)) == 64
