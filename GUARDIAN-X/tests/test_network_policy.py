"""Connection-time destination policy tests (no real network access)."""

from __future__ import annotations

import socket

import pytest

from ogx.platform.errors import InvalidEndpointError, ProviderError
from ogx.platform.security import (
    address_is_forbidden,
    is_loopback_hostname,
    resolve_pinned_address,
)
from ogx.platform.transport import HttpTransport, HttpResponse


def _answers(monkeypatch, *addresses: str):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        results = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            results.append((family, socket.SOCK_STREAM, 6, "", (address, port or 0)))
        return results

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_public_address_is_pinned(monkeypatch):
    _answers(monkeypatch, "93.184.216.34")
    assert resolve_pinned_address("api.example.com", 443) == "93.184.216.34"


def test_private_answer_is_rejected(monkeypatch):
    _answers(monkeypatch, "10.0.0.5")
    with pytest.raises(InvalidEndpointError):
        resolve_pinned_address("api.example.com", 443)


def test_mixed_public_and_private_answers_fail_closed(monkeypatch):
    _answers(monkeypatch, "93.184.216.34", "10.0.0.5")
    with pytest.raises(InvalidEndpointError):
        resolve_pinned_address("api.example.com", 443)


def test_link_local_metadata_address_is_rejected(monkeypatch):
    _answers(monkeypatch, "169.254.169.254")
    with pytest.raises(InvalidEndpointError):
        resolve_pinned_address("metadata.example.com", 80)


def test_public_name_resolving_to_loopback_is_rejected(monkeypatch):
    _answers(monkeypatch, "127.0.0.1")
    with pytest.raises(InvalidEndpointError):
        # allow_loopback only applies to a literal loopback hostname.
        resolve_pinned_address("api.example.com", 443, allow_loopback=True)


def test_literal_loopback_requires_explicit_permission(monkeypatch):
    with pytest.raises(InvalidEndpointError):
        resolve_pinned_address("127.0.0.1", 8080)
    assert (
        resolve_pinned_address("127.0.0.1", 8080, allow_loopback=True) == "127.0.0.1"
    )


def test_ipv6_loopback_and_unique_local(monkeypatch):
    with pytest.raises(InvalidEndpointError):
        resolve_pinned_address("::1", 443)
    assert resolve_pinned_address("::1", 443, allow_loopback=True) == "::1"
    _answers(monkeypatch, "fd00::1")
    with pytest.raises(InvalidEndpointError):
        resolve_pinned_address("api.example.com", 443)


def test_private_opt_in_allows_private_answer(monkeypatch):
    _answers(monkeypatch, "10.0.0.5")
    assert (
        resolve_pinned_address("internal.example.com", 443, allow_private=True)
        == "10.0.0.5"
    )


def test_resolution_failure_fails_closed(monkeypatch):
    def explode(*args, **kwargs):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", explode)
    with pytest.raises(InvalidEndpointError):
        resolve_pinned_address("api.example.com", 443)


def test_classification_helpers():
    assert is_loopback_hostname("localhost")
    assert is_loopback_hostname("::1")
    assert not is_loopback_hostname("example.com")
    assert address_is_forbidden("10.0.0.1")
    assert address_is_forbidden("0.0.0.0")
    assert address_is_forbidden("not-an-ip")
    assert not address_is_forbidden("93.184.216.34")


def test_transport_refuses_redirects():
    calls = []

    def fake_request(method, url, headers, body, timeout):
        calls.append(url)
        return HttpResponse(
            status=302,
            headers={"location": "https://attacker.example/steal"},
            body=b"",
            url=url,
        )

    transport = HttpTransport(request_fn=fake_request, max_retries=2)
    with pytest.raises(ProviderError):
        transport.send("GET", "https://api.example.com/repos")
    assert len(calls) == 1
