"""Publisher trust roots, signature envelopes and revocation policy.

Trust is explicit and offline-verifiable:

* A publisher is trusted only when a public key for it exists in the local
  trust store with status ``active`` (or ``retired`` for already-signed
  artifacts still inside their validity window).
* The signature binds the canonical manifest digest, publisher and key id, so
  permissions, file list, compatibility range and version cannot be altered
  after signing.
* Revoked keys, expired keys, unknown publishers, unsigned packages and
  digest mismatches all fail closed.
* Only public keys are ever stored; the project never generates, imports or
  uses private signing keys.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .ed25519 import verify as ed25519_verify
from .manifest import ManifestError, PluginManifest, sha256_hex

ENVELOPE_SCHEMA_VERSION = 1
_ALLOWED_KEY_STATUS = frozenset({"active", "retired", "revoked"})
_ENVELOPE_KEYS = frozenset(
    {"schema_version", "algorithm", "publisher", "key_id", "digest", "signature"}
)


class TrustError(ValueError):
    """Raised when a package cannot be trusted."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrustError(f"Invalid {field} timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True, slots=True)
class TrustedKey:
    publisher: str
    key_id: str
    public_key: bytes
    status: str = "active"
    not_before: str | None = None
    not_after: str | None = None
    revoked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "publisher": self.publisher,
            "key_id": self.key_id,
            "public_key": base64.b64encode(self.public_key).decode("ascii"),
            "status": self.status,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "revoked_reason": self.revoked_reason,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "TrustedKey":
        required = {"publisher", "key_id", "public_key", "status"}
        if not isinstance(payload, Mapping) or not required <= set(payload):
            raise TrustError("Trusted key entry is malformed.")
        if set(payload) - {"publisher", "key_id", "public_key", "status", "not_before", "not_after", "revoked_reason"}:
            raise TrustError("Trusted key entry contains unknown fields.")
        publisher = str(payload["publisher"])
        key_id = str(payload["key_id"])
        if not publisher or not key_id:
            raise TrustError("Trusted key requires publisher and key_id.")
        status = str(payload["status"])
        if status not in _ALLOWED_KEY_STATUS:
            raise TrustError("Trusted key status is invalid.")
        try:
            public_key = base64.b64decode(str(payload["public_key"]), validate=True)
        except Exception as exc:  # noqa: BLE001 - normalize base64 errors
            raise TrustError("Trusted key is not valid base64.") from exc
        if len(public_key) != 32:
            raise TrustError("Trusted Ed25519 public keys must be 32 bytes.")
        return TrustedKey(
            publisher=publisher,
            key_id=key_id,
            public_key=public_key,
            status=status,
            not_before=payload.get("not_before"),
            not_after=payload.get("not_after"),
            revoked_reason=payload.get("revoked_reason"),
        )


class TrustStore:
    """A JSON-backed collection of public publisher keys (never private keys)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._keys: dict[tuple[str, str], TrustedKey] = {}
        if path is not None and path.exists():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        if self.path.is_symlink():
            raise TrustError("Trust store path must not be a symlink.")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrustError("Trust store is unreadable or malformed.") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise TrustError("Unsupported trust store schema.")
        entries = payload.get("keys", [])
        if not isinstance(entries, list):
            raise TrustError("Trust store keys must be a list.")
        self._keys = {}
        for entry in entries:
            key = TrustedKey.from_dict(entry)
            self._keys[(key.publisher, key.key_id)] = key

    def save(self) -> Path:
        if self.path is None:
            raise TrustError("Trust store has no backing path.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "keys": [key.to_dict() for key in sorted(self._keys.values(), key=lambda k: (k.publisher, k.key_id))],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        return self.path

    def add(self, key: TrustedKey) -> None:
        if key.status not in _ALLOWED_KEY_STATUS:
            raise TrustError("Trusted key status is invalid.")
        self._keys[(key.publisher, key.key_id)] = key

    def revoke(self, publisher: str, key_id: str, reason: str) -> TrustedKey:
        existing = self._keys.get((publisher, key_id))
        if existing is None:
            raise TrustError("Cannot revoke an unknown publisher key.")
        if not reason.strip():
            raise TrustError("Revocation requires a reason.")
        revoked = TrustedKey(
            publisher=existing.publisher,
            key_id=existing.key_id,
            public_key=existing.public_key,
            status="revoked",
            not_before=existing.not_before,
            not_after=existing.not_after,
            revoked_reason=reason.strip()[:200],
        )
        self._keys[(publisher, key_id)] = revoked
        return revoked

    def get(self, publisher: str, key_id: str) -> TrustedKey | None:
        return self._keys.get((publisher, key_id))

    def keys_for(self, publisher: str) -> list[TrustedKey]:
        return [key for key in self._keys.values() if key.publisher == publisher]

    def __len__(self) -> int:
        return len(self._keys)


@dataclass(frozen=True, slots=True)
class SignatureEnvelope:
    publisher: str
    key_id: str
    digest: str
    signature: bytes
    algorithm: str = "ed25519"
    schema_version: int = ENVELOPE_SCHEMA_VERSION

    def signing_body(self) -> bytes:
        return _canonical(
            {
                "schema_version": self.schema_version,
                "algorithm": self.algorithm,
                "publisher": self.publisher,
                "key_id": self.key_id,
                "digest": self.digest,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm": self.algorithm,
            "publisher": self.publisher,
            "key_id": self.key_id,
            "digest": self.digest,
            "signature": base64.b64encode(self.signature).decode("ascii"),
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "SignatureEnvelope":
        if not isinstance(payload, Mapping):
            raise TrustError("Signature envelope must be an object.")
        if set(payload) != _ENVELOPE_KEYS:
            raise TrustError("Signature envelope has missing or unknown fields.")
        if payload["schema_version"] != ENVELOPE_SCHEMA_VERSION:
            raise TrustError("Unsupported signature envelope schema.")
        if payload["algorithm"] != "ed25519":
            raise TrustError("Only Ed25519 signatures are supported.")
        digest = payload["digest"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise TrustError("Signature envelope digest is malformed.")
        try:
            signature = base64.b64decode(str(payload["signature"]), validate=True)
        except Exception as exc:  # noqa: BLE001 - normalize base64 errors
            raise TrustError("Signature is not valid base64.") from exc
        if len(signature) != 64:
            raise TrustError("Ed25519 signatures must be 64 bytes.")
        return SignatureEnvelope(
            publisher=str(payload["publisher"]),
            key_id=str(payload["key_id"]),
            digest=digest,
            signature=signature,
        )

    @staticmethod
    def from_json(raw: str | bytes) -> "SignatureEnvelope":
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(data) > 16 * 1024:
            raise TrustError("Signature envelope exceeds the size limit.")
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrustError("Signature envelope is not valid JSON.") from exc
        return SignatureEnvelope.from_dict(payload)


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    publisher: str
    key_id: str
    digest: str
    manifest_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "publisher": self.publisher,
            "key_id": self.key_id,
            "digest": self.digest,
            "manifest_digest": self.manifest_digest,
            "verified": True,
        }


class TrustPolicy:
    """Fail-closed verification of signed plugin packages."""

    def __init__(self, store: TrustStore, *, clock: Any = _now) -> None:
        self.store = store
        self._clock = clock

    def verify(
        self,
        manifest: PluginManifest,
        envelope: SignatureEnvelope | Mapping[str, Any] | None,
        *,
        contents: Mapping[str, bytes] | None = None,
    ) -> VerificationRecord:
        if envelope is None:
            raise TrustError("Plugin package is unsigned; refusing to trust it.")
        if isinstance(envelope, Mapping):
            envelope = SignatureEnvelope.from_dict(envelope)

        if envelope.publisher != manifest.publisher:
            raise TrustError("Signature publisher does not match the manifest publisher.")

        key = self.store.get(envelope.publisher, envelope.key_id)
        if key is None:
            raise TrustError("Signing key is not present in the local trust store.")
        if key.status == "revoked":
            raise TrustError(
                f"Signing key is revoked: {key.revoked_reason or 'no reason recorded'}."
            )
        if key.status not in {"active", "retired"}:
            raise TrustError("Signing key is not usable under the current trust policy.")

        now = self._clock()
        not_before = _parse_time(key.not_before, "not_before")
        not_after = _parse_time(key.not_after, "not_after")
        if not_before is not None and now < not_before:
            raise TrustError("Signing key is not valid yet.")
        if not_after is not None and now > not_after:
            raise TrustError("Signing key has expired.")

        manifest_digest = manifest.artifact_digest()
        if envelope.digest != manifest_digest:
            raise TrustError("Signature digest does not match the plugin manifest.")

        if not ed25519_verify(key.public_key, envelope.signature, envelope.signing_body()):
            raise TrustError("Ed25519 signature verification failed.")

        if contents is not None:
            try:
                from .manifest import verify_files

                verify_files(manifest, contents)
            except ManifestError as exc:
                raise TrustError(str(exc)) from exc

        return VerificationRecord(
            publisher=key.publisher,
            key_id=key.key_id,
            digest=envelope.digest,
            manifest_digest=manifest_digest,
        )


def fingerprint(public_key: bytes) -> str:
    """Stable, non-secret identifier for a public key."""

    return sha256_hex(bytes(public_key))[:32]


def load_revocations(payload: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Read a simple ``{"revoked": [{"publisher","key_id"}]}`` revocation list."""

    revoked: set[tuple[str, str]] = set()
    for item in payload.get("revoked", []) or []:
        if not isinstance(item, Mapping):
            continue
        publisher = item.get("publisher")
        key_id = item.get("key_id")
        if isinstance(publisher, str) and isinstance(key_id, str):
            revoked.add((publisher, key_id))
    return revoked
