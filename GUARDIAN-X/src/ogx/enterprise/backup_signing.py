"""Manifest signing boundary for backups (ENT-10 hardening).

A backup manifest is only trustworthy if an attacker who can rewrite the
artifact cannot also rewrite the manifest. Three properties are deliberately
kept separate:

* **hash integrity** — ``sha256`` in the manifest proves the artifact was not
  corrupted; it proves nothing about who wrote the manifest.
* **AEAD authentication** — sealed backups refuse modified ciphertext chunks.
* **manifest authenticity** — a signature over the canonical manifest bytes.

This module implements the signing *boundary* so a managed KMS or HSM signing
key can be plugged in. It ships one concrete provider, :class:`HmacSigningProvider`,
which is real cryptographic authentication (HMAC-SHA256, constant-time compare,
key file 0600) but **symmetric**: anyone who can read the key file can forge a
manifest, and there is no third-party verifiability. It is therefore a
development/operator-local provider, not a production release-signing service.
No production signing key is generated here and none is implied.

Key lifecycle: one key is ``active`` (used to sign). Rotation marks the previous
key ``retired`` — still trusted for verification, so old manifests keep
verifying — while a new key signs future manifests. ``revoke`` makes a key stop
verifying immediately, which is how a compromised key is retired. The key file
never leaves ``OGX_HOME`` unless the operator copies it, and no method returns
secret material.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import state_dir

SIGNING_ALGORITHM = "hmac-sha256"
KEYS_FORMAT = "ogx-backup-signing-keys"
KEYS_VERSION = 1
KEY_BYTES = 32
MAX_KEYS = 32


class SigningError(RuntimeError):
    """A manifest signature could not be produced or verified."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@runtime_checkable
class SigningProvider(Protocol):
    """The boundary a managed KMS/HSM signing key implements."""

    algorithm: str

    def active_key_id(self) -> str: ...

    def sign(self, payload: bytes) -> dict[str, Any]: ...

    def verify(self, payload: bytes, signature: dict[str, Any]) -> bool: ...

    def rotate(self) -> str: ...

    def revoke(self, key_id: str) -> None: ...

    def describe(self) -> dict[str, Any]: ...


class HmacSigningProvider:
    """Operator-local symmetric signing provider (development/test)."""

    algorithm = SIGNING_ALGORITHM

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else (
            state_dir() / "enterprise" / "backup-signing-keys.json"
        )

    # ------------------------------------------------------------- persistence
    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"format": KEYS_FORMAT, "version": KEYS_VERSION,
                    "active_key_id": None, "keys": {}}
        raw = self.path.read_bytes()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SigningError("Signing key file is not valid JSON.") from exc
        if not isinstance(payload, dict) or payload.get("format") != KEYS_FORMAT:
            raise SigningError("Signing key file has an unknown format.")
        if payload.get("version") != KEYS_VERSION:
            raise SigningError("Signing key file version is not supported.")
        keys = payload.get("keys")
        if not isinstance(keys, dict) or len(keys) > MAX_KEYS:
            raise SigningError("Signing key file is malformed.")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        scratch = self.path.with_name(f".{uuid.uuid4().hex}.keys")
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        with scratch.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(scratch, 0o600)
        os.replace(scratch, self.path)

    @staticmethod
    def _secret(key: dict[str, Any]) -> bytes:
        try:
            return bytes.fromhex(str(key["secret"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SigningError("Stored signing key is malformed.") from exc

    # --------------------------------------------------------------- lifecycle
    def active_key_id(self) -> str:
        state = self._load()
        key_id = state.get("active_key_id")
        if not key_id:
            return self.rotate()
        return str(key_id)

    def rotate(self) -> str:
        state = self._load()
        keys = state["keys"]
        if len(keys) >= MAX_KEYS:
            raise SigningError("Too many signing keys; revoke one before rotating.")
        previous = state.get("active_key_id")
        if previous and previous in keys:
            keys[previous]["status"] = "retired"
        key_id = "sig_" + secrets.token_hex(8)
        keys[key_id] = {
            "secret": secrets.token_bytes(KEY_BYTES).hex(),
            "status": "active",
            "created_at": _now(),
        }
        state["active_key_id"] = key_id
        self._save(state)
        return key_id

    def revoke(self, key_id: str) -> None:
        state = self._load()
        key = state["keys"].get(key_id)
        if key is None:
            raise SigningError("Unknown signing key.")
        key["status"] = "revoked"
        state["keys"][key_id] = key
        if state.get("active_key_id") == key_id:
            state["active_key_id"] = None
        self._save(state)

    # ------------------------------------------------------------ sign / verify
    def sign(self, payload: bytes) -> dict[str, Any]:
        if not isinstance(payload, (bytes, bytearray)):
            raise SigningError("Signing payload must be bytes.")
        state = self._load()
        key_id = self.active_key_id()
        state = self._load()
        key = state["keys"].get(key_id)
        if key is None or key.get("status") != "active":
            raise SigningError("No active signing key is available.")
        signature = hmac.new(self._secret(key), bytes(payload), hashlib.sha256).hexdigest()
        return {"algorithm": SIGNING_ALGORITHM, "key_id": key_id, "value": signature}

    def verify(self, payload: bytes, signature: dict[str, Any]) -> bool:
        if not isinstance(signature, dict):
            return False
        if signature.get("algorithm") != SIGNING_ALGORITHM:
            return False
        key_id = signature.get("key_id")
        value = signature.get("value")
        if not isinstance(key_id, str) or not isinstance(value, str):
            return False
        state = self._load()
        key = state["keys"].get(key_id)
        if key is None or key.get("status") == "revoked":
            return False
        expected = hmac.new(self._secret(key), bytes(payload), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, value)

    def describe(self) -> dict[str, Any]:
        state = self._load()
        return {
            "algorithm": SIGNING_ALGORITHM,
            "asymmetric": False,
            "active_key_id": state.get("active_key_id"),
            "keys": [
                {"key_id": key_id, "status": key.get("status"),
                 "created_at": key.get("created_at")}
                for key_id, key in sorted(state["keys"].items())
            ],
        }


__all__ = [
    "HmacSigningProvider",
    "KEYS_FORMAT",
    "SIGNING_ALGORITHM",
    "SigningError",
    "SigningProvider",
]
