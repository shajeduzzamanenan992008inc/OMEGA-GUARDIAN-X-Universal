"""Consistent, verifiable database backups and restores (ENT-10).

A backup is only useful if it can be restored, so this module implements the
whole cycle rather than a file copy:

* **Consistent snapshot** — SQLite's online backup API copies the database while
  it is in use (WAL included) instead of reading a file that may be mid-write.
* **Integrity proof** — the copy is opened read-only and must pass
  ``PRAGMA integrity_check`` before it is published.
* **Authenticity of bytes** — the manifest records the artifact SHA-256 and its
  size; :func:`verify_backup` recomputes both.
* **Optional encryption** — ``encrypt=True`` seals the snapshot in 64 KiB
  chunks with the existing AEAD through the :class:`~ogx.enterprise.crypto.KMSProvider`
  boundary, so a backup never leaves a plaintext copy behind. The chunk index,
  chunk count and backup id are bound as associated data, so reordering,
  truncating or splicing chunks fails authentication.
* **Atomic restore** — a restore decrypts to a temporary file in the target
  directory, verifies it, and only then ``os.replace``-s it into place, keeping
  the previous database as ``<target>.pre-restore`` when overwriting.

Honest limits (also in ``docs/BACKUP_AND_RESTORE.md``): the manifest is not
signed, so an attacker who can rewrite both artifact and manifest consistently
is not detected; a local keyring is not a hardware root of trust; and this is a
single-node SQLite mechanism, not PostgreSQL point-in-time recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import state_dir
from .crypto import KMSProvider, LocalKeyring

BACKUP_FORMAT = "ogx-enterprise-backup"
CIPHER_FORMAT = "ogx-enterprise-backup-cipher"
BACKUP_VERSION = 1
PURPOSE = "database-backup"
CHUNK_BYTES = 64 * 1024
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_KEEP = 5


class BackupError(RuntimeError):
    """A backup could not be created, verified or restored."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_backup_dir() -> Path:
    return state_dir() / "enterprise" / "backups"


def _sha256_file(path: Path, limit: int = MAX_BACKUP_BYTES) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise BackupError(f"Backup exceeds the {limit} byte safety limit.")
            digest.update(chunk)
    return digest.hexdigest()


def _check_integrity(path: Path) -> dict[str, Any]:
    """Open a database file read-only and prove SQLite accepts it."""

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        verdict = str(result[0]) if result else "unknown"
        if verdict.lower() != "ok":
            raise BackupError(f"Integrity check failed: {verdict[:200]}")
        tables = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    finally:
        connection.close()
    return {"integrity_check": "ok", "tables": int(tables), "pages": int(page_count)}


def _seal_file(source: Path, destination: Path, provider: KMSProvider,
               backup_id: str) -> dict[str, Any]:
    total_bytes = source.stat().st_size
    if total_bytes > MAX_BACKUP_BYTES:
        raise BackupError("Database is too large for the backup safety limit.")
    chunks = max(1, (total_bytes + CHUNK_BYTES - 1) // CHUNK_BYTES)
    key_id: str | None = None
    with source.open("rb") as reader, destination.open("wb") as writer:
        writer.write(json.dumps(
            {"format": CIPHER_FORMAT, "version": BACKUP_VERSION, "chunks": chunks,
             "plaintext_bytes": total_bytes, "backup_id": backup_id},
            sort_keys=True,
        ).encode("utf-8") + b"\n")
        for index in range(chunks):
            block = reader.read(CHUNK_BYTES)
            aad = f"{backup_id}:{index}:{chunks}".encode("utf-8")
            envelope = provider.encrypt(block, purpose=PURPOSE, aad=aad)
            key_id = envelope.get("key_id", key_id)
            writer.write(json.dumps(envelope, sort_keys=True).encode("utf-8") + b"\n")
        writer.flush()
        os.fsync(writer.fileno())
    return {"chunks": chunks, "key_id": key_id, "plaintext_bytes": total_bytes}


def _open_file(source: Path, destination: Path, provider: KMSProvider) -> dict[str, Any]:
    with source.open("rb") as reader:
        header_line = reader.readline()
        try:
            header = json.loads(header_line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BackupError("Backup does not start with a valid header.") from exc
        if header.get("format") != CIPHER_FORMAT or header.get("version") != BACKUP_VERSION:
            raise BackupError("Backup header is not a supported encrypted backup.")
        backup_id = str(header.get("backup_id") or "")
        chunks = int(header.get("chunks") or 0)
        plaintext_bytes = int(header.get("plaintext_bytes") or 0)
        if chunks < 1 or chunks > 1_000_000 or plaintext_bytes < 0:
            raise BackupError("Backup header is out of range.")
        written = 0
        with destination.open("wb") as writer:
            for index in range(chunks):
                line = reader.readline()
                if not line:
                    raise BackupError("Encrypted backup is truncated.")
                try:
                    envelope = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise BackupError("Encrypted chunk is malformed.") from exc
                aad = f"{backup_id}:{index}:{chunks}".encode("utf-8")
                block = provider.decrypt(envelope, purpose=PURPOSE, aad=aad)
                written += len(block)
                writer.write(block)
            if reader.read(1):
                raise BackupError("Encrypted backup has trailing data.")
            writer.flush()
            os.fsync(writer.fileno())
    if written != plaintext_bytes:
        raise BackupError("Encrypted backup size does not match its header.")
    return {"chunks": chunks, "plaintext_bytes": plaintext_bytes}


def create_backup(store: Any, destination: Path | str | None = None, *,
                  encrypt: bool = False, provider: KMSProvider | None = None,
                  label: str | None = None) -> dict[str, Any]:
    """Create a consistent, verified backup and return its manifest."""

    db_path = Path(store.db_path)
    if db_path.is_symlink():
        raise BackupError("Refusing to back up a symlinked database.")
    if not db_path.is_file():
        raise BackupError("The enterprise database does not exist yet.")

    target_dir = Path(destination) if destination is not None else default_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target_dir.is_symlink():
        raise BackupError("Backup directory must not be a symlink.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_id = uuid.uuid4().hex
    suffix = ".sqlite3.ogxbak" if encrypt else ".sqlite3"
    artifact = target_dir / f"enterprise-{stamp}-{backup_id[:8]}{suffix}"
    scratch = target_dir / f".{backup_id}.snapshot"
    manifest_path = artifact.with_name(artifact.name + ".json")

    try:
        source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            copy = sqlite3.connect(scratch)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        details = _check_integrity(scratch)
        plaintext_digest = _sha256_file(scratch)
        plaintext_bytes = scratch.stat().st_size

        if encrypt:
            kms = provider or LocalKeyring()
            sealed = _seal_file(scratch, artifact, kms, backup_id)
            key_id = sealed.get("key_id")
        else:
            shutil.move(str(scratch), str(artifact))
            key_id = None
        os.chmod(artifact, 0o600)
        artifact_digest = _sha256_file(artifact)
        manifest = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "backup_id": backup_id,
            "label": label,
            "created_at": _now(),
            "product_version": __version__,
            "artifact": artifact.name,
            "encrypted": bool(encrypt),
            "key_id": key_id,
            "sha256": artifact_digest,
            "bytes": artifact.stat().st_size,
            "plaintext_sha256": plaintext_digest,
            "plaintext_bytes": plaintext_bytes,
            "details": details,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.chmod(manifest_path, 0o600)
        return {**manifest, "path": str(artifact), "manifest_path": str(manifest_path)}
    except BaseException:
        for leftover in (scratch, artifact, manifest_path):
            try:
                leftover.unlink()
            except OSError:
                pass
        raise


def _load_manifest(backup_path: Path) -> dict[str, Any]:
    manifest_path = backup_path.with_name(backup_path.name + ".json")
    if not manifest_path.is_file():
        raise BackupError("Backup manifest is missing.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupError("Backup manifest is unreadable.") from exc
    if manifest.get("format") != BACKUP_FORMAT or manifest.get("version") != BACKUP_VERSION:
        raise BackupError("Backup manifest format is not supported.")
    return manifest


def verify_backup(backup_path: Path | str, *, provider: KMSProvider | None = None) -> dict[str, Any]:
    """Recompute the artifact digest and prove the snapshot restores cleanly."""

    artifact = Path(backup_path)
    if artifact.is_symlink() or not artifact.is_file():
        raise BackupError("Backup artifact is missing or a symlink.")
    manifest = _load_manifest(artifact)
    if artifact.stat().st_size != int(manifest.get("bytes", -1)):
        raise BackupError("Backup size does not match its manifest.")
    digest = _sha256_file(artifact)
    if digest != manifest.get("sha256"):
        raise BackupError("Backup digest does not match its manifest.")

    scratch = artifact.with_name(f".{uuid.uuid4().hex}.verify")
    try:
        if manifest.get("encrypted"):
            kms = provider or LocalKeyring()
            _open_file(artifact, scratch, kms)
            if _sha256_file(scratch) != manifest.get("plaintext_sha256"):
                raise BackupError("Decrypted backup does not match the recorded plaintext digest.")
        else:
            shutil.copyfile(artifact, scratch)
        details = _check_integrity(scratch)
    finally:
        try:
            scratch.unlink()
        except OSError:
            pass
    return {
        "valid": True,
        "backup_id": manifest.get("backup_id"),
        "encrypted": bool(manifest.get("encrypted")),
        "sha256": digest,
        "bytes": artifact.stat().st_size,
        "created_at": manifest.get("created_at"),
        "details": details,
    }


def restore_backup(backup_path: Path | str, target: Path | str, *,
                   provider: KMSProvider | None = None,
                   overwrite: bool = False) -> dict[str, Any]:
    """Restore a verified backup into ``target`` atomically."""

    artifact = Path(backup_path)
    destination = Path(target)
    if destination.is_symlink():
        raise BackupError("Refusing to restore onto a symlink.")
    if destination.exists() and not overwrite:
        raise BackupError("Target database already exists; pass overwrite to replace it.")
    verify_backup(artifact, provider=provider)
    manifest = _load_manifest(artifact)

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    scratch = destination.with_name(f".{uuid.uuid4().hex}.restore")
    backup_of_previous: Path | None = None
    try:
        if manifest.get("encrypted"):
            _open_file(artifact, scratch, provider or LocalKeyring())
        else:
            shutil.copyfile(artifact, scratch)
        _check_integrity(scratch)
        os.chmod(scratch, 0o600)
        if destination.exists():
            backup_of_previous = destination.with_name(destination.name + ".pre-restore")
            os.replace(destination, backup_of_previous)
        for sidecar in (destination.with_name(destination.name + "-wal"),
                        destination.with_name(destination.name + "-shm")):
            try:
                sidecar.unlink()
            except OSError:
                pass
        os.replace(scratch, destination)
    except BaseException:
        try:
            scratch.unlink()
        except OSError:
            pass
        raise
    return {
        "restored": True,
        "target": str(destination),
        "backup_id": manifest.get("backup_id"),
        "sha256": manifest.get("sha256"),
        "encrypted": bool(manifest.get("encrypted")),
        "previous": str(backup_of_previous) if backup_of_previous else None,
        "restored_at": _now(),
    }


def list_backups(destination: Path | str | None = None) -> list[dict[str, Any]]:
    directory = Path(destination) if destination is not None else default_backup_dir()
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for manifest_path in directory.glob("*.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if manifest.get("format") != BACKUP_FORMAT:
            continue
        artifact = manifest_path.with_name(manifest_path.name[: -len(".json")])
        items.append({
            "backup_id": manifest.get("backup_id"),
            "path": str(artifact),
            "created_at": manifest.get("created_at"),
            "encrypted": bool(manifest.get("encrypted")),
            "bytes": manifest.get("bytes"),
            "label": manifest.get("label"),
            "present": artifact.is_file(),
        })
    return sorted(items, key=lambda item: (str(item.get("created_at")), str(item.get("backup_id"))),
                  reverse=True)


def prune_backups(destination: Path | str | None = None, keep: int = DEFAULT_KEEP) -> dict[str, Any]:
    if int(keep) < 1:
        raise BackupError("keep must be at least 1; refusing to delete every backup.")
    items = list_backups(destination)
    removed: list[str] = []
    for item in items[int(keep):]:
        artifact = Path(item["path"])
        for path in (artifact, artifact.with_name(artifact.name + ".json")):
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                continue
    return {"kept": min(len(items), int(keep)), "removed": removed}


__all__ = [
    "BACKUP_FORMAT",
    "BACKUP_VERSION",
    "BackupError",
    "CHUNK_BYTES",
    "create_backup",
    "default_backup_dir",
    "list_backups",
    "prune_backups",
    "restore_backup",
    "verify_backup",
]
