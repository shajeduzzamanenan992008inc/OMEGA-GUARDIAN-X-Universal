"""Real backup, tamper-detection and disaster-recovery drill (ENT-10 / PRD-04).

This is a *drill*, not a unit test: it creates a populated enterprise database,
takes an encrypted and signed backup, verifies it, destroys the live database,
restores from the backup into a new location, and proves the tenant data
survived with matching bytes and an intact SQLite image. It also proves that a
single flipped byte in the backup artifact is detected, and that a different
keyring cannot open the backup.

Everything happens under a unique temporary directory; no existing state is
read or modified. The evidence JSON contains paths, counts and digests only -
never credentials or tenant secrets.

Usage:

    python _dev/tools/verify_backup_drill.py \
        --evidence docs/evidence/backup-restore-drill-2026-11-28.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ogx.enterprise.backup import (  # noqa: E402
    create_backup,
    restore_backup,
    verify_backup,
)
from ogx.enterprise.backup_signing import HmacSigningProvider  # noqa: E402
from ogx.enterprise.crypto import LocalKeyring  # noqa: E402
from ogx.enterprise.store import EnterpriseStore  # noqa: E402


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _counts(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            "organizations": int(
                connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
            ),
            "members": int(
                connection.execute("SELECT COUNT(*) FROM memberships").fetchone()[0]
            ),
        }
    finally:
        connection.close()


def _integrity(db_path: Path) -> str:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    started = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="ogx-backup-drill-"))
    result: dict[str, object] = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": sys.platform,
        "python": sys.version.split()[0],
        "checks": {},
        "ok": False,
    }
    try:
        db_path = root / "live" / "enterprise.sqlite3"
        db_path.parent.mkdir(parents=True)
        store = EnterpriseStore(db_path)
        store.initialize()
        org = store.create_organization("drill-org", "Drill Org", "owner@drill.example")
        store.add_member(
            org["organization_id"], "admin@drill.example", "admin", "owner@drill.example"
        )
        store.add_member(
            org["organization_id"], "dev@drill.example", "developer", "owner@drill.example"
        )
        before = _counts(db_path)
        result["checks"]["seeded"] = before

        keyring = LocalKeyring(root / "keys" / "kms.json")
        signer = HmacSigningProvider(root / "keys" / "signing.json")
        backups = root / "backups"
        manifest = create_backup(
            store, backups, encrypt=True, provider=keyring, signer=signer,
            label="drill",
        )
        artifact = Path(manifest["path"])
        verified = verify_backup(
            artifact, provider=keyring, signer=signer, require_signature=True
        )
        result["checks"]["backup"] = {
            "bytes": manifest["bytes"],
            "plaintext_bytes": manifest["plaintext_bytes"],
            "encrypted": manifest["encrypted"],
            "signed": bool(manifest.get("signature")),
            "integrity_check": manifest["details"]["integrity_check"],
            "signature_verified": bool(
                verified.get("signature_verified", verified.get("signature"))
            ),
            "tables": manifest["details"]["tables"],
        }

        # A different keyring must not be able to open the sealed artifact.
        wrong_key_error = None
        try:
            verify_backup(
                artifact,
                provider=LocalKeyring(root / "keys" / "wrong.json"),
                signer=signer,
                require_signature=True,
            )
        except Exception as exc:  # noqa: BLE001 - any refusal is acceptable
            wrong_key_error = type(exc).__name__
        result["checks"]["wrong_key_refused"] = bool(wrong_key_error)

        # Flip one byte in a copy; verification must fail.
        tampered = root / "tampered.sqlite3.ogxbak"
        payload = bytearray(artifact.read_bytes())
        payload[len(payload) // 2] ^= 0x01
        tampered.write_bytes(bytes(payload))
        shutil.copyfile(
            artifact.with_name(artifact.name + ".json"),
            tampered.with_name(tampered.name + ".json"),
        )
        tamper_error = None
        try:
            verify_backup(
                tampered, provider=keyring, signer=signer, require_signature=True
            )
        except Exception as exc:  # noqa: BLE001 - any refusal is acceptable
            tamper_error = type(exc).__name__
        result["checks"]["tamper_refused"] = bool(tamper_error)

        # Disaster: destroy the live database, then restore it elsewhere.
        getattr(store, "close", lambda: None)()
        backup_of_original = root / "destroyed" / db_path.name
        backup_of_original.parent.mkdir(parents=True)
        shutil.move(str(db_path), str(backup_of_original))
        for sidecar in (db_path.with_name(db_path.name + "-wal"),
                        db_path.with_name(db_path.name + "-shm")):
            sidecar.unlink(missing_ok=True)
        result["checks"]["destroyed"] = not db_path.exists()

        restored_path = root / "restored" / "enterprise.sqlite3"
        restored = restore_backup(
            artifact,
            restored_path,
            provider=keyring,
            signer=signer,
            require_signature=True,
        )
        after = _counts(restored_path)
        integrity = _integrity(restored_path)
        result["checks"]["restore"] = {
            "restored": bool(restored.get("restored")),
            "integrity_check": integrity,
            "counts": after,
            "counts_match": after == before,
            "sha256": _digest(restored_path),
        }
        result["checks"]["restore"]["plaintext_sha256_match"] = (
            result["checks"]["restore"]["sha256"] == manifest["plaintext_sha256"]
        )

        result["ok"] = all(
            (
                result["checks"]["backup"]["signature_verified"],
                result["checks"]["backup"]["integrity_check"] == "ok",
                result["checks"]["wrong_key_refused"],
                result["checks"]["tamper_refused"],
                result["checks"]["destroyed"],
                result["checks"]["restore"]["restored"],
                result["checks"]["restore"]["integrity_check"] == "ok",
                result["checks"]["restore"]["counts_match"],
                result["checks"]["restore"]["plaintext_sha256_match"],
            )
        )
    except Exception as exc:  # noqa: BLE001 - evidence tool reports the class
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
            result["cleaned_up"] = True
        else:
            result["cleaned_up"] = False
            result["workdir"] = str(root)

    serialized = json.dumps(result, indent=2, sort_keys=True)
    print(serialized)
    if args.evidence:
        path = Path(args.evidence)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized + "\n", encoding="utf-8")
        print(f"evidence written to {path}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
