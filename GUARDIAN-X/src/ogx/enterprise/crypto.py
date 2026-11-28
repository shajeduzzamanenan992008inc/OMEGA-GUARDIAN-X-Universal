"""Envelope encryption and a key-management boundary (ENT-06).

The product needs encryption at rest for values it persists (tenant secrets, and
any future credential material). Redaction is not encryption, so this module
implements a real AEAD and a key-management interface:

* :class:`LocalKeyring` is the **development/test** provider. It keeps raw key
  material in a 0600 JSON file under ``OGX_HOME`` and is explicitly not a
  production KMS: there is no hardware root of trust, no access policy and no
  remote audit. It exists so that the envelope format, rotation, revocation and
  restore paths are implemented and testable offline.
* :class:`KMSProvider` is the seam a managed KMS (AWS KMS, GCP KMS, Azure Key
  Vault, Vault Transit) plugs into. The interface deliberately exposes only
  ``generate_key``/``encrypt``/``decrypt``/``rotate``/``revoke``/``describe`` so
  a real provider cannot leak key material into application memory.

The AEAD is ChaCha20-Poly1305 as specified by RFC 8439, implemented in portable
Python. ``tests/test_crypto_kms.py`` checks it against the RFC 8439 §2.8.2
vector, so the primitive is not merely "some encryption-shaped code".

Honest limits (documented in ``docs/KMS_AND_ENCRYPTION.md``):

* pure-Python AEAD is **not** constant-time and is unsuitable for hostile local
  attackers; production deployments must use a vetted backend (OpenSSL, a
  managed KMS) through :class:`KMSProvider`;
* a key file readable by the process is readable by an attacker who already has
  that process's privileges;
* key rotation rewrites the purpose pointer, not every stored ciphertext; old
  key versions remain decryptable until explicitly revoked.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import state_dir

ALGORITHM = "chacha20poly1305"
ENVELOPE_VERSION = 1
KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
MAX_PLAINTEXT_BYTES = 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_PLAINTEXT_BYTES + TAG_BYTES + 64

_MASK32 = 0xFFFFFFFF
_CONSTANTS = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
_POLY1305_P = (1 << 130) - 5
_POLY1305_R_MASK = 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF


class CryptoError(Exception):
    """Base error for the encryption subsystem."""


class AuthenticationFailed(CryptoError):
    """The ciphertext, tag, nonce, key or associated data did not verify."""


class KMSError(CryptoError):
    """Base error for key-management operations."""


class UnknownKey(KMSError):
    """The envelope references a key that this provider does not hold."""


class KeyRevoked(KMSError):
    """The envelope references a revoked key; decryption is refused."""


class InvalidEnvelope(CryptoError):
    """The envelope is malformed, oversized or uses an unknown algorithm."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------- ChaCha20
def _rotl32(value: int, count: int) -> int:
    return ((value << count) & _MASK32) | (value >> (32 - count))


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & _MASK32
    state[d] = _rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & _MASK32
    state[b] = _rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & _MASK32
    state[d] = _rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & _MASK32
    state[b] = _rotl32(state[b] ^ state[c], 7)


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise ValueError("ChaCha20 requires a 32-byte key.")
    if len(nonce) != NONCE_BYTES:
        raise ValueError("ChaCha20 requires a 12-byte nonce.")
    if not 0 <= counter <= 0xFFFFFFFF:
        raise ValueError("ChaCha20 block counter must fit in 32 bits.")
    state = list(_CONSTANTS)
    state.extend(int.from_bytes(key[i:i + 4], "little") for i in range(0, 32, 4))
    state.append(counter & _MASK32)
    state.extend(int.from_bytes(nonce[i:i + 4], "little") for i in range(0, 12, 4))
    working = list(state)
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    out = b"".join(
        ((working[i] + state[i]) & _MASK32).to_bytes(4, "little") for i in range(16)
    )
    return out


def chacha20_xor(key: bytes, nonce: bytes, data: bytes, counter: int = 0) -> bytes:
    """Encrypt/decrypt ``data`` with the ChaCha20 stream cipher."""

    if counter + (len(data) + 63) // 64 > 0x100000000:
        raise ValueError("ChaCha20 counter would overflow.")
    chunks: list[bytes] = []
    for offset in range(0, len(data), 64):
        block = _chacha20_block(key, counter + offset // 64, nonce)
        piece = data[offset:offset + 64]
        chunks.append(bytes(a ^ b for a, b in zip(piece, block)))
    return b"".join(chunks)


# --------------------------------------------------------------------- Poly1305
def poly1305_mac(key: bytes, message: bytes) -> bytes:
    """RFC 8439 §2.5 Poly1305 one-time authenticator."""

    if len(key) != 32:
        raise ValueError("Poly1305 requires a 32-byte one-time key.")
    r = int.from_bytes(key[:16], "little") & _POLY1305_R_MASK
    s = int.from_bytes(key[16:], "little")
    accumulator = 0
    for offset in range(0, len(message), 16):
        chunk = message[offset:offset + 16]
        number = int.from_bytes(chunk + b"\x01", "little")
        accumulator = ((accumulator + number) * r) % _POLY1305_P
    accumulator = (accumulator + s) % (1 << 128)
    return accumulator.to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    return b"\x00" * ((16 - len(data) % 16) % 16)


def _mac_data(aad: bytes, ciphertext: bytes) -> bytes:
    return (
        aad
        + _pad16(aad)
        + ciphertext
        + _pad16(ciphertext)
        + len(aad).to_bytes(8, "little")
        + len(ciphertext).to_bytes(8, "little")
    )


def aead_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """ChaCha20-Poly1305 AEAD encryption; returns ``ciphertext || tag``."""

    if len(key) != KEY_BYTES:
        raise ValueError("AEAD requires a 32-byte key.")
    if len(nonce) != NONCE_BYTES:
        raise ValueError("AEAD requires a 12-byte nonce.")
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("Plaintext exceeds the bounded AEAD size limit.")
    one_time_key = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = chacha20_xor(key, nonce, plaintext, counter=1)
    tag = poly1305_mac(one_time_key, _mac_data(aad, ciphertext))
    return ciphertext + tag


def aead_decrypt(key: bytes, nonce: bytes, sealed: bytes, aad: bytes = b"") -> bytes:
    """Verify and decrypt ``ciphertext || tag``; raises AuthenticationFailed."""

    if len(key) != KEY_BYTES:
        raise ValueError("AEAD requires a 32-byte key.")
    if len(nonce) != NONCE_BYTES:
        raise ValueError("AEAD requires a 12-byte nonce.")
    if len(sealed) < TAG_BYTES or len(sealed) > MAX_CIPHERTEXT_BYTES:
        raise InvalidEnvelope("Ciphertext length is outside the accepted bounds.")
    ciphertext, tag = sealed[:-TAG_BYTES], sealed[-TAG_BYTES:]
    one_time_key = _chacha20_block(key, 0, nonce)[:32]
    expected = poly1305_mac(one_time_key, _mac_data(aad, ciphertext))
    if not secrets.compare_digest(tag, expected):
        raise AuthenticationFailed("Ciphertext authentication failed.")
    return chacha20_xor(key, nonce, ciphertext, counter=1)


# ------------------------------------------------------------------- envelopes
def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: Any, *, field: str) -> bytes:
    if not isinstance(value, str):
        raise InvalidEnvelope(f"Envelope field {field} must be a base64 string.")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise InvalidEnvelope(f"Envelope field {field} is not valid base64.") from exc


@dataclass(frozen=True, slots=True)
class KeyInfo:
    key_id: str
    purpose: str
    status: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "purpose": self.purpose,
            "status": self.status,
            "created_at": self.created_at,
        }


class KMSProvider(ABC):
    """Minimal key-management seam. Never exposes raw key material."""

    @abstractmethod
    def encrypt(self, plaintext: bytes, *, purpose: str, aad: bytes = b"") -> dict[str, Any]:
        """Encrypt with the current key for ``purpose`` and return an envelope."""

    @abstractmethod
    def decrypt(self, envelope: dict[str, Any], *, purpose: str, aad: bytes = b"") -> bytes:
        """Verify and decrypt an envelope produced by this provider."""

    @abstractmethod
    def rotate(self, purpose: str) -> str:
        """Create a new current key for ``purpose`` and return its id."""

    @abstractmethod
    def revoke(self, key_id: str) -> None:
        """Revoke a key; future decryptions with it are refused."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe keys and metadata without returning key material."""


class LocalKeyring(KMSProvider):
    """Development/test KMS: a 0600 JSON key file with in-process AEAD.

    Not a production key manager. See the module docstring for the limits that
    are also documented for operators.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else (
            state_dir() / "enterprise" / "kms-keys.json"
        )

    # ------------------------------------------------------------ persistence
    def _read(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise KMSError("KMS key file must not be a symlink.")
        if not self.path.exists():
            return {"schema": self.SCHEMA_VERSION, "keys": {}, "purposes": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KMSError("KMS key file is unreadable or corrupt.") from exc
        if not isinstance(data, dict) or data.get("schema") != self.SCHEMA_VERSION:
            raise KMSError("Unsupported KMS key-file schema.")
        if not isinstance(data.get("keys"), dict) or not isinstance(data.get("purposes"), dict):
            raise KMSError("KMS key file is malformed.")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise KMSError("KMS key directory must not be a symlink.")
        temporary = self.path.with_name(self.path.name + ".tmp")
        payload = json.dumps(data, sort_keys=True, indent=2).encode("utf-8")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - Windows ACLs do not map to POSIX modes
            pass

    # --------------------------------------------------------------- key mgmt
    @staticmethod
    def _new_key_id() -> str:
        return "k_" + secrets.token_hex(8)

    def _ensure_key(self, purpose: str) -> str:
        data = self._read()
        current = data["purposes"].get(purpose)
        if isinstance(current, str):
            entry = data["keys"].get(current)
            if isinstance(entry, dict) and entry.get("status") == "active":
                return current
        return self._create_key(purpose, data)

    def _create_key(self, purpose: str, data: dict[str, Any] | None = None) -> str:
        payload = data if data is not None else self._read()
        key_id = self._new_key_id()
        payload["keys"][key_id] = {
            "material": _b64encode(secrets.token_bytes(KEY_BYTES)),
            "purpose": purpose,
            "status": "active",
            "created_at": utc_now(),
        }
        previous = payload["purposes"].get(purpose)
        if isinstance(previous, str) and previous in payload["keys"]:
            payload["keys"][previous]["status"] = "retired"
        payload["purposes"][purpose] = key_id
        self._write(payload)
        return key_id

    def _material(self, key_id: str) -> tuple[bytes, dict[str, Any]]:
        data = self._read()
        entry = data["keys"].get(key_id)
        if not isinstance(entry, dict):
            raise UnknownKey(f"Unknown key id: {key_id}")
        if entry.get("status") == "revoked":
            raise KeyRevoked(f"Key {key_id} has been revoked.")
        material = _b64decode(entry.get("material"), field="material")
        if len(material) != KEY_BYTES:
            raise KMSError("Stored key material has the wrong length.")
        return material, entry

    def rotate(self, purpose: str) -> str:
        if not str(purpose or "").strip():
            raise ValueError("A key purpose is required.")
        return self._create_key(purpose)

    def revoke(self, key_id: str) -> None:
        data = self._read()
        entry = data["keys"].get(key_id)
        if not isinstance(entry, dict):
            raise UnknownKey(f"Unknown key id: {key_id}")
        entry["status"] = "revoked"
        entry["revoked_at"] = utc_now()
        self._write(data)

    def describe(self) -> dict[str, Any]:
        data = self._read()
        keys = [
            KeyInfo(
                key_id=key_id,
                purpose=str(entry.get("purpose", "")),
                status=str(entry.get("status", "unknown")),
                created_at=str(entry.get("created_at", "")),
            ).to_dict()
            for key_id, entry in sorted(data["keys"].items())
            if isinstance(entry, dict)
        ]
        return {
            "provider": "local_keyring",
            "production_ready": False,
            "path": str(self.path),
            "purposes": dict(data["purposes"]),
            "keys": keys,
        }

    # ------------------------------------------------------------- encryption
    def encrypt(self, plaintext: bytes, *, purpose: str, aad: bytes = b"") -> dict[str, Any]:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        key_id = self._ensure_key(purpose)
        material, _ = self._material(key_id)
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = aead_encrypt(material, nonce, plaintext, aad)
        return {
            "v": ENVELOPE_VERSION,
            "alg": ALGORITHM,
            "key_id": key_id,
            "purpose": purpose,
            "nonce": _b64encode(nonce),
            "ciphertext": _b64encode(sealed),
            "aad_sha256": hashlib.sha256(aad).hexdigest(),
        }

    def decrypt(self, envelope: dict[str, Any], *, purpose: str, aad: bytes = b"") -> bytes:
        if not isinstance(envelope, dict):
            raise InvalidEnvelope("Envelope must be a JSON object.")
        if envelope.get("v") != ENVELOPE_VERSION:
            raise InvalidEnvelope("Unsupported envelope version.")
        if envelope.get("alg") != ALGORITHM:
            raise InvalidEnvelope("Unsupported envelope algorithm.")
        if envelope.get("purpose") != purpose:
            raise InvalidEnvelope("Envelope purpose does not match the requested purpose.")
        expected_aad = hashlib.sha256(aad).hexdigest()
        if envelope.get("aad_sha256") != expected_aad:
            raise AuthenticationFailed("Associated data does not match the envelope.")
        key_id = envelope.get("key_id")
        if not isinstance(key_id, str) or not key_id:
            raise InvalidEnvelope("Envelope is missing a key id.")
        material, _ = self._material(key_id)
        nonce = _b64decode(envelope.get("nonce"), field="nonce")
        sealed = _b64decode(envelope.get("ciphertext"), field="ciphertext")
        if len(nonce) != NONCE_BYTES:
            raise InvalidEnvelope("Envelope nonce has the wrong length.")
        return aead_decrypt(material, nonce, sealed, aad)


class EnvelopeCipher:
    """Convenience wrapper for storing envelope-encrypted values as JSON."""

    def __init__(self, provider: KMSProvider, purpose: str = "tenant-secrets") -> None:
        self.provider = provider
        self.purpose = purpose

    def seal(self, value: str | bytes, *, aad: bytes = b"") -> str:
        plaintext = value.encode("utf-8") if isinstance(value, str) else value
        envelope = self.provider.encrypt(plaintext, purpose=self.purpose, aad=aad)
        return json.dumps(envelope, sort_keys=True)

    def open(self, sealed: str, *, aad: bytes = b"") -> str:
        try:
            envelope = json.loads(sealed)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidEnvelope("Stored ciphertext is not valid JSON.") from exc
        return self.provider.decrypt(envelope, purpose=self.purpose, aad=aad).decode("utf-8")


def default_local_provider() -> LocalKeyring:
    return LocalKeyring()


def describe_provider(provider: KMSProvider) -> dict[str, Any]:
    description = provider.describe()
    if not isinstance(description, dict):  # pragma: no cover - provider contract guard
        raise KMSError("KMS provider describe() must return an object.")
    return description


def key_versions(provider: KMSProvider, purpose: str) -> Iterable[dict]:
    for key in provider.describe().get("keys", []):
        if key.get("purpose") == purpose:
            yield key


__all__ = [
    "ALGORITHM",
    "AuthenticationFailed",
    "CryptoError",
    "ENVELOPE_VERSION",
    "EnvelopeCipher",
    "InvalidEnvelope",
    "KMSProvider",
    "KMSError",
    "KeyInfo",
    "KeyRevoked",
    "LocalKeyring",
    "UnknownKey",
    "aead_decrypt",
    "aead_encrypt",
    "chacha20_xor",
    "default_local_provider",
    "describe_provider",
    "key_versions",
    "poly1305_mac",
]
