"""Backup, verification and restore tests (ENT-10).

These are real restore tests: every backup is verified and restored into a new
database that is then queried through :class:`EnterpriseStore`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3

import pytest

from ogx.enterprise.backup import (
    BackupError,
    create_backup,
    list_backups,
    prune_backups,
    restore_backup,
    verify_backup,
)
from ogx.enterprise.crypto import CryptoError
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


def test_plain_backup_verifies_and_restores_real_data(store, tmp_path):
    instance, org = store
    backups = tmp_path / "backups"
    manifest = create_backup(instance, backups, label="nightly")

    artifact = tmp_path / "backups" / manifest["artifact"]
    assert artifact.is_file()
    if os.name == "posix":
        assert artifact.stat().st_mode & 0o777 == 0o600
    assert manifest["encrypted"] is False
    assert manifest["details"]["integrity_check"] == "ok"

    verified = verify_backup(artifact)
    assert verified["valid"] is True
    assert verified["sha256"] == manifest["sha256"]

    target = tmp_path / "restored.sqlite3"
    result = restore_backup(artifact, target)
    assert result["restored"] is True
    restored = EnterpriseStore(target)
    projects = restored.list_projects(org["organization_id"], "owner@example.com")
    assert [item["name"] for item in projects] == ["alpha"]


def test_encrypted_backup_round_trip_and_tamper_detection(store, tmp_path):
    instance, org = store
    backups = tmp_path / "backups"
    manifest = create_backup(instance, backups, encrypt=True, label="sealed")
    artifact = backups / manifest["artifact"]

    assert manifest["encrypted"] is True
    assert manifest["key_id"]
    raw = artifact.read_bytes()
    assert raw.startswith(b'{"backup_id"')
    assert b"SQLite format 3" not in raw
    assert verify_backup(artifact)["valid"] is True

    target = tmp_path / "restored-enc.sqlite3"
    restore_backup(artifact, target)
    restored = EnterpriseStore(target)
    assert restored.get_organization(org["organization_id"])["slug"] == "acme"

    # Flip one byte inside a copy of the ciphertext: the digest check catches it
    # before any decryption is attempted.
    corrupt = tmp_path / "corrupt.ogxbak"
    corrupt.write_bytes(raw)
    shutil.copyfile(artifact.with_name(artifact.name + ".json"),
                    corrupt.with_name(corrupt.name + ".json"))
    with corrupt.open("r+b") as handle:
        handle.seek(len(raw) // 2)
        byte = handle.read(1)
        handle.seek(len(raw) // 2)
        handle.write(bytes([byte[0] ^ 0xFF]))
    with pytest.raises(BackupError):
        verify_backup(corrupt)

    # An attacker who can also rewrite the manifest still cannot forge the AEAD:
    # a valid-JSON chunk with a mutated ciphertext passes the digest check only
    # after the manifest is re-hashed, and chunk authentication must reject it.
    lines = artifact.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    chunk = json.loads(lines[1])
    sealed = bytearray(chunk["ciphertext"].encode("ascii"))
    sealed[-2] = ord("A") if sealed[-2] != ord("A") else ord("B")
    chunk["ciphertext"] = sealed.decode("ascii")
    lines[1] = json.dumps(chunk, sort_keys=True)
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert header["chunks"] >= 1

    manifest_path = artifact.with_name(artifact.name + ".json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered = artifact.read_bytes()
    payload["sha256"] = hashlib.sha256(tampered).hexdigest()
    payload["bytes"] = len(tampered)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CryptoError):
        verify_backup(artifact)


def test_digest_mismatch_is_rejected(store, tmp_path):
    instance, _ = store
    backups = tmp_path / "backups"
    manifest = create_backup(instance, backups)
    artifact = backups / manifest["artifact"]
    manifest_path = artifact.with_name(artifact.name + ".json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BackupError):
        verify_backup(artifact)


def test_restore_refuses_overwrite_and_keeps_previous_copy(store, tmp_path):
    instance, _ = store
    manifest = create_backup(instance, tmp_path / "backups")
    artifact = tmp_path / "backups" / manifest["artifact"]
    target = tmp_path / "live.sqlite3"
    target.write_bytes(b"not a database")

    with pytest.raises(BackupError):
        restore_backup(artifact, target)

    result = restore_backup(artifact, target, overwrite=True)
    assert result["previous"] and os.path.isfile(result["previous"])
    assert sqlite3.connect(target).execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_of_missing_database_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "home"))
    instance = EnterpriseStore(tmp_path / "absent.sqlite3")
    with pytest.raises(BackupError):
        create_backup(instance, tmp_path / "backups")


def test_list_and_prune_keep_newest(store, tmp_path):
    instance, _ = store
    backups = tmp_path / "backups"
    for _ in range(3):
        create_backup(instance, backups)
    items = list_backups(backups)
    assert len(items) == 3

    result = prune_backups(backups, keep=2)
    assert len(result["removed"]) == 2  # artifact + manifest of the oldest
    assert len(list_backups(backups)) == 2
    with pytest.raises(BackupError):
        prune_backups(backups, keep=0)


def test_restore_rejects_symlink_target(store, tmp_path):
    instance, _ = store
    manifest = create_backup(instance, tmp_path / "backups")
    artifact = tmp_path / "backups" / manifest["artifact"]
    link = tmp_path / "link.sqlite3"
    try:
        os.symlink(tmp_path / "elsewhere.sqlite3", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(BackupError):
        restore_backup(artifact, link, overwrite=True)
