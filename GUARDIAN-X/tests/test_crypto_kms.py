"""AEAD, key management and tenant-secret tests (ENT-06)."""

from __future__ import annotations

import json
import os
import secrets

import pytest

from ogx.enterprise.authz import PolicyEngine, Principal, Resource
from ogx.enterprise.crypto import (
    AuthenticationFailed,
    EnvelopeCipher,
    InvalidEnvelope,
    KeyRevoked,
    LocalKeyring,
    UnknownKey,
    aead_decrypt,
    aead_encrypt,
    chacha20_xor,
    poly1305_mac,
)
from ogx.enterprise.secrets import SecretsVault
from ogx.enterprise.store import EnterpriseStore

# RFC 8439 §2.3.2: zero key, zero nonce, counter 0 -> first keystream block.
CHACHA20_ZERO_KEY_BLOCK = bytes.fromhex(
    "76b8e0ada0f13d90405d6ae55386bd28"
    "bdd219b8a08ded1aa836efcc8b770dc7"
    "da41597c5157488d7724e03fb8d84a37"
    "6a43b8f41518a11cc387b669b2ee6586"
)

# RFC 8439 §2.5.2 Poly1305 test vector.
POLY1305_KEY = bytes.fromhex(
    "85d6be7857556d337f4452fe42d506a8"
    "0103808afb0db2fd4abff6af4149f51b"
)
POLY1305_TAG = bytes.fromhex("a8061dc1305136c6c22b8baf0c0127a9")


def test_chacha20_block_matches_rfc8439_zero_key_vector():
    key = bytes(32)
    nonce = bytes(12)
    keystream = chacha20_xor(key, nonce, bytes(64), counter=0)
    assert keystream == CHACHA20_ZERO_KEY_BLOCK


def test_chacha20_is_a_stream_cipher_roundtrip():
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    message = b"attack at dawn" * 9
    sealed = chacha20_xor(key, nonce, message, counter=7)
    assert sealed != message
    assert chacha20_xor(key, nonce, sealed, counter=7) == message


def test_poly1305_matches_rfc8439_vector():
    message = b"Cryptographic Forum Research Group"
    assert poly1305_mac(POLY1305_KEY, message) == POLY1305_TAG


def test_aead_roundtrip_and_authentication():
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    aad = b"tenant-a:secret"
    sealed = aead_encrypt(key, nonce, b"top secret", aad)
    assert aead_decrypt(key, nonce, sealed, aad) == b"top secret"

    with pytest.raises(AuthenticationFailed):
        aead_decrypt(key, nonce, sealed, b"tenant-b:secret")

    tampered = bytearray(sealed)
    tampered[0] ^= 0x01
    with pytest.raises(AuthenticationFailed):
        aead_decrypt(key, nonce, bytes(tampered), aad)

    with pytest.raises(AuthenticationFailed):
        aead_decrypt(secrets.token_bytes(32), nonce, sealed, aad)

    with pytest.raises(InvalidEnvelope):
        aead_decrypt(key, nonce, b"short", aad)


def _keyring(tmp_path) -> LocalKeyring:
    return LocalKeyring(tmp_path / "kms" / "keys.json")


def test_local_keyring_encrypts_decrypts_and_hides_material(tmp_path):
    keyring = _keyring(tmp_path)
    envelope = keyring.encrypt(b"value", purpose="tenant-secrets")
    assert envelope["alg"] == "chacha20poly1305"
    assert envelope["ciphertext"] != "value"
    assert keyring.decrypt(envelope, purpose="tenant-secrets") == b"value"

    description = keyring.describe()
    assert description["provider"] == "local_keyring"
    assert description["production_ready"] is False
    serialized = json.dumps(description)
    assert "material" not in serialized.lower()
    assert envelope["key_id"] in serialized

    stored = (tmp_path / "kms" / "keys.json").read_text(encoding="utf-8")
    assert "material" in stored  # the provider needs the key, the API surface never shows it
    if os.name != "nt":
        assert (tmp_path / "kms" / "keys.json").stat().st_mode & 0o777 == 0o600


def test_key_rotation_keeps_old_ciphertext_readable_and_new_keys_active(tmp_path):
    keyring = _keyring(tmp_path)
    old = keyring.encrypt(b"one", purpose="tenant-secrets")
    new_key_id = keyring.rotate("tenant-secrets")
    assert new_key_id != old["key_id"]
    assert keyring.decrypt(old, purpose="tenant-secrets") == b"one"
    fresh = keyring.encrypt(b"two", purpose="tenant-secrets")
    assert fresh["key_id"] == new_key_id
    statuses = {item["key_id"]: item["status"] for item in keyring.describe()["keys"]}
    assert statuses[old["key_id"]] == "retired"
    assert statuses[new_key_id] == "active"


def test_revoked_key_is_refused_and_unknown_key_is_rejected(tmp_path):
    keyring = _keyring(tmp_path)
    envelope = keyring.encrypt(b"value", purpose="tenant-secrets")
    keyring.revoke(envelope["key_id"])
    with pytest.raises(KeyRevoked):
        keyring.decrypt(envelope, purpose="tenant-secrets")
    with pytest.raises(UnknownKey):
        keyring.revoke("k_does_not_exist")


def test_envelope_binds_purpose_and_aad(tmp_path):
    keyring = _keyring(tmp_path)
    envelope = keyring.encrypt(b"value", purpose="tenant-secrets", aad=b"tenant-a:name")
    with pytest.raises(InvalidEnvelope):
        keyring.decrypt(envelope, purpose="other-purpose", aad=b"tenant-a:name")
    with pytest.raises(AuthenticationFailed):
        keyring.decrypt(envelope, purpose="tenant-secrets", aad=b"tenant-b:name")
    tampered = dict(envelope)
    tampered["v"] = 99
    with pytest.raises(InvalidEnvelope):
        keyring.decrypt(tampered, purpose="tenant-secrets", aad=b"tenant-a:name")


def test_envelope_cipher_roundtrip(tmp_path):
    cipher = EnvelopeCipher(_keyring(tmp_path))
    sealed = cipher.seal("hello", aad=b"tenant-a:greeting")
    assert "hello" not in sealed
    assert cipher.open(sealed, aad=b"tenant-a:greeting") == "hello"
    with pytest.raises(InvalidEnvelope):
        cipher.open("not-json")


def _vault(tmp_path):
    store = EnterpriseStore(tmp_path / "enterprise.sqlite3")
    store.initialize()
    org = store.create_organization("acme", "Acme", "owner@example.com")
    store.add_member(org["organization_id"], "dev@example.com", "developer", "owner@example.com")
    store.add_member(org["organization_id"], "auditor@example.com", "auditor", "owner@example.com")
    vault = SecretsVault(store, provider=_keyring(tmp_path))
    return store, org, vault


def test_tenant_secret_is_encrypted_at_rest_and_audited(tmp_path):
    store, org, vault = _vault(tmp_path)
    org_id = org["organization_id"]
    result = vault.set(org_id, "mail-api-key", "s3cr3t-value", "owner@example.com")
    assert result["stored"] is True

    record = store.get_tenant_secret(org_id, "mail-api-key")
    assert record is not None
    assert "s3cr3t-value" not in record["envelope_json"]
    assert store.list_tenant_secret_names(org_id)[0]["name"] == "mail-api-key"

    assert vault.get(org_id, "mail-api-key", "owner@example.com") == "s3cr3t-value"
    actions = [event["action"] for event in store.list_audit_events(org_id, "owner@example.com")]
    assert "secret.write" in actions and "secret.read" in actions


def test_secret_access_is_denied_for_roles_without_the_action(tmp_path):
    _, org, vault = _vault(tmp_path)
    org_id = org["organization_id"]
    vault.set(org_id, "token", "value", "owner@example.com")
    with pytest.raises(PermissionError):
        vault.set(org_id, "token2", "value", "dev@example.com")
    with pytest.raises(PermissionError):
        vault.get(org_id, "token", "dev@example.com")
    with pytest.raises(PermissionError):
        vault.get(org_id, "token", "auditor@example.com")  # auditors cannot read secret values
    with pytest.raises(PermissionError):
        vault.set(org_id, "token3", "value", "stranger@example.com")


def test_secret_is_bound_to_tenant_and_name(tmp_path):
    store, org, vault = _vault(tmp_path)
    org_id = org["organization_id"]
    vault.set(org_id, "key", "value", "owner@example.com")
    record = store.get_tenant_secret(org_id, "key")
    # Moving the ciphertext to another name must fail authentication.
    store.upsert_tenant_secret(org_id, "other-key", record["envelope_json"], record["key_id"])
    with pytest.raises(AuthenticationFailed):
        vault.get(org_id, "other-key", "owner@example.com")


def test_rotation_reencrypts_every_secret(tmp_path):
    store, org, vault = _vault(tmp_path)
    org_id = org["organization_id"]
    vault.set(org_id, "one", "alpha", "owner@example.com")
    vault.set(org_id, "two", "beta", "owner@example.com")
    before = {name["name"]: name["key_id"] for name in store.list_tenant_secret_names(org_id)}
    result = vault.rotate(org_id, "owner@example.com")
    assert result["rotated"] == 2 and result["failed"] == 0
    after = {name["name"]: name["key_id"] for name in store.list_tenant_secret_names(org_id)}
    assert after["one"] == result["new_key_id"] == after["two"]
    assert before["one"] != after["one"]
    assert vault.get(org_id, "one", "owner@example.com") == "alpha"
    assert vault.get(org_id, "two", "owner@example.com") == "beta"


def test_secret_name_validation(tmp_path):
    _, org, vault = _vault(tmp_path)
    for bad in ("", " ", "../escape", "a" * 65):
        with pytest.raises(ValueError):
            vault.set(org["organization_id"], bad, "value", "owner@example.com")


def test_cross_tenant_secret_access_is_denied(tmp_path):
    store, org, vault = _vault(tmp_path)
    other = store.create_organization("other", "Other", "owner@other.example")
    store.add_member(other["organization_id"], "foreign@example.com", "owner",
                     "owner@other.example")
    vault.set(org["organization_id"], "key", "value", "owner@example.com")
    with pytest.raises(PermissionError):
        vault.get(org["organization_id"], "key", "foreign@example.com")
    decision = PolicyEngine().decide(
        Principal(subject="owner@other.example", roles=("owner",),
                  tenant_id=other["organization_id"]),
        "secret.read",
        Resource(kind="secret", id="key", tenant_id=org["organization_id"]),
    )
    assert decision.allowed is False
    assert decision.rule == "deny.tenant_mismatch"
