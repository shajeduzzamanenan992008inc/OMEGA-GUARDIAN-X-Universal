"""Backup security tests: manifest signing, path safety, recovery (ENT-10)."""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from ogx.enterprise.backup import (
    BackupError,
    create_backup,
    prune_backups,
    recover_restore,
    restore_backup,
    validate_manifest,
    verify_backup,
)
from ogx.enterprise.backup_signing import HmacSigningProvider, SigningError
from ogx.enterprise.store import EnterpriseStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "home"))
    instance = EnterpriseStore(tmp_path / "enterprise.sqlite3")
    instance.initialize()
    org = instance.create_organization("acme", "Acme", "owner@example.com")
    instance.register_project(org["organization_id"], "alpha", "local", None,
                              "owner@example.com")
    return instance, org


@pytest.fixture()
def signer(tmp_path):
    return HmacSigningProvider(tmp_path / "signing-keys.json")


def _manifest(backup, tmp_path):
    return tmp_path / "backups" / (backup["artifact"] + ".json")


def test_signed_backup_verifies_and_reports_signature_state(store, signer, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups", signer=signer)
    assert backup["version"] == 2
    assert backup["signature"]["algorithm"] == "hmac-sha256"

    result = verify_backup(tmp_path / "backups" / backup["artifact"], signer=signer,
                           require_signature=True)
    assert result["signature"]["status"] == "verified"
    assert result["signature"]["verified"] is True

    unsigned = create_backup(instance, tmp_path / "backups")
    state = verify_backup(tmp_path / "backups" / unsigned["artifact"], signer=signer)
    assert state["signature"]["status"] == "unsigned"
    with pytest.raises(BackupError):
        verify_backup(tmp_path / "backups" / unsigned["artifact"], signer=signer,
                      require_signature=True)
    with pytest.raises(BackupError):
        verify_backup(tmp_path / "backups" / backup["artifact"], require_signature=True)


def test_tampered_manifest_content_breaks_signature(store, signer, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups", signer=signer, label="original")
    manifest_path = _manifest(backup, tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["label"] = "tampered"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BackupError):
        verify_backup(tmp_path / "backups" / backup["artifact"], signer=signer)
    # Without a signer the hash still matches, so verification stays "unverified"
    # instead of pretending the manifest is authentic.
    result = verify_backup(tmp_path / "backups" / backup["artifact"])
    assert result["valid"] is True
    assert result["signature"]["status"] == "unverified"


def test_replaced_artifact_is_rejected(store, signer, tmp_path):
    instance, org = store
    first = create_backup(instance, tmp_path / "backups", signer=signer)
    # Change the database so the second backup is genuinely different content.
    instance.register_project(org["organization_id"], "beta", "local", None,
                              "owner@example.com")
    second = create_backup(instance, tmp_path / "backups", signer=signer)
    first_artifact = tmp_path / "backups" / first["artifact"]
    second_artifact = tmp_path / "backups" / second["artifact"]
    assert first["sha256"] != second["sha256"]
    first_artifact.write_bytes(second_artifact.read_bytes())
    with pytest.raises(BackupError):
        verify_backup(first_artifact, signer=signer)


def test_missing_or_wrong_signing_key_fails_closed(store, signer, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups", signer=signer)
    artifact = tmp_path / "backups" / backup["artifact"]

    stranger = HmacSigningProvider(tmp_path / "other-keys.json")
    with pytest.raises(BackupError):
        verify_backup(artifact, signer=stranger)

    signer.revoke(backup["signature"]["key_id"])
    with pytest.raises(BackupError):
        verify_backup(artifact, signer=signer)


def test_key_rotation_keeps_old_manifests_verifiable(store, signer, tmp_path):
    instance, _ = store
    old = create_backup(instance, tmp_path / "backups", signer=signer)
    new_key = signer.rotate()
    assert new_key != old["signature"]["key_id"]

    assert verify_backup(tmp_path / "backups" / old["artifact"],
                         signer=signer)["signature"]["status"] == "verified"
    fresh = create_backup(instance, tmp_path / "backups", signer=signer)
    assert fresh["signature"]["key_id"] == new_key
    assert verify_backup(tmp_path / "backups" / fresh["artifact"],
                         signer=signer)["signature"]["status"] == "verified"


def test_unknown_signature_algorithm_is_rejected(store, signer, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups", signer=signer)
    manifest_path = _manifest(backup, tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["signature"]["algorithm"] = "ed25519-forged"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupError):
        verify_backup(tmp_path / "backups" / backup["artifact"], signer=signer)


def test_manifest_schema_rejects_unsafe_and_malformed_input():
    base = {
        "format": "ogx-enterprise-backup", "version": 2, "backup_id": "a" * 32,
        "created_at": "2026-01-01T00:00:00+00:00", "product_version": "0.8.0a1",
        "artifact": "enterprise-x.sqlite3", "encrypted": False, "key_id": None,
        "sha256": "0" * 64, "bytes": 10, "plaintext_sha256": "1" * 64,
        "plaintext_bytes": 10, "details": {"integrity_check": "ok"}, "label": None,
    }
    assert validate_manifest(dict(base))["artifact"] == "enterprise-x.sqlite3"

    for bad_artifact in ("../escape.sqlite3", "C:/escape.sqlite3", "/etc/passwd",
                         "sub/dir.sqlite3", ".hidden.sqlite3", "x.sqlite3.exe", ""):
        with pytest.raises(BackupError):
            validate_manifest({**base, "artifact": bad_artifact})

    for bad_bytes in (-1, 10 ** 13, "10", True):
        with pytest.raises(BackupError):
            validate_manifest({**base, "bytes": bad_bytes})

    with pytest.raises(BackupError):
        validate_manifest({**base, "sha256": "zz" * 32})
    with pytest.raises(BackupError):
        validate_manifest({**base, "frontmatter": "extra"})
    with pytest.raises(BackupError):
        validate_manifest({k: v for k, v in base.items() if k != "sha256"})
    with pytest.raises(BackupError):
        validate_manifest({**base, "version": 1,
                           "signature": {"algorithm": "hmac-sha256", "key_id": "k",
                                         "value": "0" * 64}})
    with pytest.raises(BackupError):
        validate_manifest("not a manifest")


def test_restore_target_path_safety(store, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups")
    artifact = tmp_path / "backups" / backup["artifact"]

    with pytest.raises(BackupError):
        restore_backup(artifact, "relative.sqlite3")
    with pytest.raises(BackupError):
        restore_backup(artifact, pathlib.Path(str(tmp_path / ".." / "escape.sqlite3")))
    with pytest.raises(BackupError):
        restore_backup(artifact, tmp_path)


def test_interrupted_restore_is_recoverable(store, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups")
    target = tmp_path / "live.sqlite3"
    previous = tmp_path / "live.sqlite3.pre-restore"
    previous.write_bytes(b"previous-database-bytes")
    journal = tmp_path / "live.sqlite3.restore-journal"
    journal.write_text(json.dumps({"version": 1, "target": str(target),
                                   "backup_id": backup["backup_id"],
                                   "stage": "verified"}), encoding="utf-8")
    stray = tmp_path / ".deadbeef.restore"
    stray.write_bytes(b"half-written")

    result = recover_restore(target)
    assert result["recovered"] is True
    assert result["rolled_back"] is True
    assert target.read_bytes() == b"previous-database-bytes"
    assert not journal.exists() and not stray.exists()
    assert recover_restore(target)["recovered"] is False


def test_restore_removes_journal_on_success(store, tmp_path):
    instance, org = store
    backup = create_backup(instance, tmp_path / "backups")
    target = tmp_path / "restored.sqlite3"
    restore_backup(tmp_path / "backups" / backup["artifact"], target)
    assert not (tmp_path / "restored.sqlite3.restore-journal").exists()
    restored = EnterpriseStore(target)
    assert restored.get_organization(org["organization_id"])["slug"] == "acme"


def test_prune_refuses_when_no_retained_backup_verifies(store, tmp_path):
    instance, _ = store
    backups = tmp_path / "backups"
    for _ in range(2):
        create_backup(instance, backups)
    newest = sorted(backups.glob("*.sqlite3"))[-1]
    raw = bytearray(newest.read_bytes())
    raw[-1] ^= 0xFF
    newest.write_bytes(bytes(raw))

    with pytest.raises(BackupError):
        prune_backups(backups, keep=1)


def test_signing_provider_never_exposes_secret_material(tmp_path):
    provider = HmacSigningProvider(tmp_path / "keys.json")
    key_id = provider.active_key_id()
    description = provider.describe()
    assert description["active_key_id"] == key_id
    assert description["asymmetric"] is False
    assert "secret" not in json.dumps(description)
    if os.name == "posix":
        assert (tmp_path / "keys.json").stat().st_mode & 0o777 == 0o600

    with pytest.raises(SigningError):
        provider.revoke("sig_does_not_exist")
    provider.rotate()
    assert len(provider.describe()["keys"]) == 2
