"""Tests for stable host identity (host.json) and machine binding."""

from __future__ import annotations

import json

import pytest

from ogx.pap.exceptions import IdentityCorruptedError, IdentityValidationError
from ogx.pap.host_identity import (
    HOST_ID_NAMESPACE,
    HostIdentity,
    adopt_host_identity,
    create_host_identity,
    derive_host_id,
    host_identity_path,
    load_host_identity,
    load_or_create_host_identity,
    machine_binding,
    verify_host_identity,
)

STRONG_BINDING = {
    "kind": "hostname+user",
    "node": "workstation-alpha",
    "user": "developer",
    "system": "Windows",
    "machine": "AMD64",
    "binding_hash": "sha256:" + "a" * 64,
}

OTHER_BINDING = {
    "kind": "hostname+user",
    "node": "workstation-beta",
    "user": "someone-else",
    "system": "Linux",
    "machine": "x86_64",
    "binding_hash": "sha256:" + "b" * 64,
}

WEAK_BINDING = {
    "kind": "weak",
    "node": "",
    "user": "",
    "system": "Windows",
    "machine": "AMD64",
    "binding_hash": "sha256:" + "c" * 64,
}


def test_derive_host_id_is_deterministic_and_distinct():
    first = derive_host_id(STRONG_BINDING["binding_hash"])
    second = derive_host_id(STRONG_BINDING["binding_hash"])
    other = derive_host_id(OTHER_BINDING["binding_hash"])
    assert first == second
    assert first != other


def test_derive_host_id_uses_dedicated_namespace():
    assert HOST_ID_NAMESPACE.version == 5
    # uuid5 derived ids are deterministic for the same binding.
    assert derive_host_id(STRONG_BINDING["binding_hash"]).count("-") == 4


def test_create_then_load_is_idempotent(tmp_path):
    created = create_host_identity(tmp_path, display_name="Box", binding=STRONG_BINDING)
    again = load_or_create_host_identity(tmp_path)
    assert again.host_id == created.host_id
    assert again.binding_hash == created.binding_hash
    assert host_identity_path(tmp_path).exists()


def test_host_json_does_not_store_raw_hostname_or_user(tmp_path):
    create_host_identity(tmp_path, binding=STRONG_BINDING)
    raw = host_identity_path(tmp_path).read_text(encoding="utf-8")
    assert STRONG_BINDING["node"] not in raw
    assert STRONG_BINDING["user"] not in raw
    data = json.loads(raw)
    assert data["binding_hash"] == STRONG_BINDING["binding_hash"]
    assert data["platform"] == {"system": "Windows", "machine": "AMD64"}


def test_verify_verified_with_matching_binding(tmp_path):
    identity = create_host_identity(tmp_path, binding=STRONG_BINDING)
    report = verify_host_identity(identity, binding=STRONG_BINDING)
    assert report["state"] == "verified"
    assert report["access"] == "read_write"


def test_verify_weak_binding_limits_ownership_operations(tmp_path):
    identity = create_host_identity(tmp_path, binding=WEAK_BINDING)
    report = verify_host_identity(identity, binding=WEAK_BINDING)
    assert report["state"] == "weak"
    assert report["access"] == "read_only_for_ownership_operations"


def test_verify_foreign_binding_is_read_only(tmp_path):
    identity = create_host_identity(tmp_path, binding=STRONG_BINDING)
    report = verify_host_identity(identity, binding=OTHER_BINDING)
    assert report["state"] == "foreign_or_renamed"
    assert report["access"] == "read_only"
    assert report["expected_host_id"] != identity.host_id


def test_verify_missing_identity(tmp_path):
    report = verify_host_identity(None, state_root=tmp_path, binding=STRONG_BINDING)
    assert report["state"] == "missing"
    assert report["access"] == "read_only"


def test_corrupted_host_json_raises(tmp_path):
    path = host_identity_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json }", encoding="utf-8")
    with pytest.raises(IdentityCorruptedError):
        load_host_identity(tmp_path)


def test_invalid_host_schema_raises(tmp_path):
    path = host_identity_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "9.9",
                "host_id": "00000000-0000-4000-8000-000000000000",
                "binding_hash": "sha256:" + "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IdentityValidationError):
        load_host_identity(tmp_path)


def test_create_refuses_overwrite_without_confirm(tmp_path):
    create_host_identity(tmp_path, binding=STRONG_BINDING)
    with pytest.raises(IdentityValidationError):
        create_host_identity(tmp_path, binding=OTHER_BINDING)


def test_adopt_requires_explicit_confirm(tmp_path):
    create_host_identity(tmp_path, binding=STRONG_BINDING)
    with pytest.raises(IdentityValidationError):
        adopt_host_identity(tmp_path, binding=OTHER_BINDING, confirm=False)
    adopted = adopt_host_identity(tmp_path, binding=OTHER_BINDING, confirm=True)
    assert adopted.binding_hash == OTHER_BINDING["binding_hash"]


def test_machine_binding_contains_no_serial_or_mac():
    binding = machine_binding()
    keys = set(binding) - {"kind", "node", "user", "system", "machine", "binding_hash"}
    assert keys == set()
    assert binding["binding_hash"].startswith("sha256:")
    assert len(binding["binding_hash"]) == len("sha256:") + 64


def test_host_identity_round_trip_dict():
    original = HostIdentity(
        host_id="00000000-0000-4000-8000-000000000000",
        created_at="2026-01-01T00:00:00+00:00",
        display_name="Box",
        binding_kind="hostname+user",
        binding_hash="sha256:" + "d" * 64,
        platform={"system": "Windows", "machine": "AMD64"},
    )
    rebuilt = HostIdentity.from_dict(original.to_dict())
    assert rebuilt == original
