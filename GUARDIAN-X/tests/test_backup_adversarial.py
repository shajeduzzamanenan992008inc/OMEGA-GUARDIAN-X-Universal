from __future__ import annotations

import json
import threading

import pytest

from ogx.enterprise.backup import BackupError, create_backup, recover_restore, restore_backup, validate_manifest, verify_backup
from ogx.enterprise.backup_signing import HmacSigningProvider
from ogx.enterprise.store import EnterpriseStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "home"))
    instance = EnterpriseStore(tmp_path / "enterprise.sqlite3")
    instance.initialize()
    org = instance.create_organization("acme", "Acme", "owner@example.com")
    instance.register_project(org["organization_id"], "alpha", "local", None, "owner@example.com")
    return instance, org


def test_journal_tampering_raises(store, tmp_path):
    instance, _ = store
    target = tmp_path / "live.sqlite3"
    journal = tmp_path / "live.sqlite3.restore-journal"
    journal.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(BackupError):
        recover_restore(target)


def test_journal_wrong_version_raises(store, tmp_path):
    instance, _ = store
    target = tmp_path / "live.sqlite3"
    journal = tmp_path / "live.sqlite3.restore-journal"
    journal.write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(BackupError):
        recover_restore(target)


def test_symlink_artifact_rejected(store, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups")
    artifact = tmp_path / "backups" / backup["artifact"]
    link = tmp_path / "link.sqlite3"
    try:
        link.symlink_to(artifact)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    with pytest.raises(BackupError):
        verify_backup(link)


def test_oversized_manifest_rejected(store, tmp_path):
    instance, _ = store
    backup = create_backup(instance, tmp_path / "backups")
    manifest_path = tmp_path / "backups" / (backup["artifact"] + ".json")
    manifest_path.write_text(" " * (65 * 1024), encoding="utf-8")
    with pytest.raises(BackupError):
        verify_backup(tmp_path / "backups" / backup["artifact"])


def test_algorithm_substitution_signature(store, tmp_path):
    instance, _ = store
    signer = HmacSigningProvider(tmp_path / "keys.json")
    backup = create_backup(instance, tmp_path / "backups", signer=signer)
    manifest_path = tmp_path / "backups" / (backup["artifact"] + ".json")
    payload = json.loads(manifest_path.read_text())
    payload["signature"]["algorithm"] = "sha256"
    manifest_path.write_text(json.dumps(payload))
    with pytest.raises(BackupError):
        verify_backup(tmp_path / "backups" / backup["artifact"], signer=signer)


def test_concurrent_backup_create(store, tmp_path):
    instance, _ = store
    errors = []

    def worker():
        try:
            create_backup(instance, tmp_path / "backups")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_restore_does_not_destroy_previous_on_verify_failure(store, tmp_path):
    instance, org = store
    backup = create_backup(instance, tmp_path / "backups")
    target = tmp_path / "live.sqlite3"
    target.write_bytes(b"precious")
    artifact = tmp_path / "backups" / backup["artifact"]
    raw = bytearray(artifact.read_bytes())
    raw[0] ^= 0xFF
    artifact.write_bytes(bytes(raw))
    with pytest.raises(BackupError):
        restore_backup(artifact, target, overwrite=True)
    assert target.read_bytes() == b"precious"


def test_duplicate_unknown_field_rejected():
    base = {
        "format": "ogx-enterprise-backup", "version": 2, "backup_id": "a" * 32,
        "created_at": "2026-01-01T00:00:00+00:00", "product_version": "0.8.0a1",
        "artifact": "enterprise-x.sqlite3", "encrypted": False, "key_id": None,
        "sha256": "0" * 64, "bytes": 10, "plaintext_sha256": "1" * 64,
        "plaintext_bytes": 10, "details": {"integrity_check": "ok"}, "label": None,
    }
    with pytest.raises(BackupError):
        validate_manifest({**base, "signature": "not-a-dict"})
