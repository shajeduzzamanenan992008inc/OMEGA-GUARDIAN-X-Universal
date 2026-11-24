"""Repository identity normalization: ports, case, credentials, IPv6."""

from __future__ import annotations

import uuid

import pytest

from ogx.platform.identity import (
    deterministic_repository_id,
    normalize_repository_url,
)


def test_repository_key_has_no_scheme_or_dot_git():
    assert (
        normalize_repository_url("https://Git.Example.com/Acme/Repo.git/")
        == "git.example.com/acme/repo"
    )
    assert (
        normalize_repository_url("https://git.example.com/acme/repo")
        == "git.example.com/acme/repo"
    )


def test_scp_style_remote_is_normalized():
    assert (
        normalize_repository_url("git@github.com:Acme/Demo.git")
        == "github.com/acme/demo"
    )


def test_custom_port_is_part_of_identity():
    plain = normalize_repository_url("https://git.example.com/acme/repo.git")
    ported = normalize_repository_url("https://git.example.com:8443/acme/repo.git")
    assert ported == "git.example.com:8443/acme/repo"
    assert plain != ported
    assert deterministic_repository_id("gitlab", plain) != deterministic_repository_id(
        "gitlab", ported
    )


def test_explicit_default_port_matches_implicit_port():
    assert normalize_repository_url(
        "https://git.example.com:443/acme/repo"
    ) == normalize_repository_url("https://git.example.com/acme/repo")
    assert normalize_repository_url(
        "http://git.example.com:80/acme/repo"
    ) == normalize_repository_url("http://git.example.com/acme/repo")


def test_ipv6_endpoint_keeps_the_port():
    assert (
        normalize_repository_url("https://[2001:db8::1]:8443/acme/repo")
        == "[2001:db8::1]:8443/acme/repo"
    )


def test_credentials_in_url_are_rejected():
    with pytest.raises(ValueError):
        normalize_repository_url("https://user:secret@example.com/acme/repo")


def test_invalid_identifiers_fail_explicitly():
    with pytest.raises(ValueError):
        normalize_repository_url("")
    with pytest.raises(ValueError):
        normalize_repository_url("https://git.example.com")
    with pytest.raises(ValueError):
        normalize_repository_url("not-a-url")


def test_deterministic_ids_are_stable_and_provider_scoped():
    key = normalize_repository_url("https://github.com/acme/demo")
    first = deterministic_repository_id("github", key)
    second = deterministic_repository_id("github", key)
    assert first == second
    assert isinstance(first, uuid.UUID)
    assert first != deterministic_repository_id("gitlab", key)
