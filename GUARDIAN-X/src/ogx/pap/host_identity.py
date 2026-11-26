"""Stable host identity (HID) for one machine/user install.

The host identity answers "which machine/user install is this?" and never
travels with a project. It is stored at ``<state>/host.json`` and is verified
against a privacy-conscious machine binding.

Design rules:

* No hardware serial numbers, MAC addresses, disk ids or other sensitive
  hardware identifiers are read or stored.
* The binding is derived from ``platform.node()``, the current user name and
  coarse platform facts. Only a salted-free SHA-256 digest of that evidence is
  persisted; raw host/user names are not written to ``host.json``.
* A weak binding (no hostname or no user) is recorded as ``weak`` and must not
  be used to claim strong ownership.
* A binding mismatch is reported as ``foreign_or_renamed``. GUARDIAN-X never
  silently adopts another machine's state directory: the caller must pass
  ``adopt=True`` explicitly.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import socket
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid5

from .exceptions import IdentityCorruptedError, IdentityValidationError

HOST_IDENTITY_FILENAME = "host.json"
HOST_SCHEMA_VERSION = "1.0"
HOST_STATE_DIRNAME = "host"

#: Deterministic namespace for machine-level host ids.
HOST_ID_NAMESPACE = uuid5(NAMESPACE_OID, "omega-guardian-x/host-identity/v1")


@dataclass(frozen=True, slots=True)
class HostIdentity:
    """Machine/user host identity.

    ``binding_hash`` is the only persisted machine evidence. ``platform``
    carries coarse, non-identifying facts used for diagnostics.
    """

    schema_version: str = HOST_SCHEMA_VERSION
    host_id: str = ""
    created_at: str = ""
    display_name: str = ""
    binding_kind: str = "weak"
    binding_hash: str = ""
    platform: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostIdentity":
        if not isinstance(data, dict):
            raise IdentityValidationError("host.json must contain a JSON object", field="root")
        version = data.get("schema_version")
        if version != HOST_SCHEMA_VERSION:
            raise IdentityValidationError(
                f"Unsupported host identity schema: {version!r}",
                field="schema_version",
            )
        host_id = data.get("host_id")
        try:
            UUID(str(host_id))
        except (TypeError, ValueError) as exc:
            raise IdentityValidationError(
                f"Invalid host_id: {host_id!r}", field="host_id"
            ) from exc
        binding_hash = data.get("binding_hash")
        if not isinstance(binding_hash, str) or not binding_hash.startswith("sha256:"):
            raise IdentityValidationError(
                "binding_hash must be 'sha256:<hex>'", field="binding_hash"
            )
        return cls(
            schema_version=version,
            host_id=str(host_id),
            created_at=str(data.get("created_at", "")),
            display_name=str(data.get("display_name", "")),
            binding_kind=str(data.get("binding_kind", "weak")),
            binding_hash=binding_hash,
            platform=dict(data.get("platform") or {}),
        )


def _current_user() -> str:
    try:
        return getpass.getuser() or ""
    except Exception:  # pragma: no cover - platform dependent
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def machine_binding() -> dict[str, str]:
    """Describe this machine/user without touching sensitive hardware ids.

    Raw host and user names are returned for hashing only; callers must not
    persist them.
    """

    node = ""
    try:
        node = platform.node() or socket.gethostname() or ""
    except Exception:  # pragma: no cover - platform dependent
        node = ""
    user = _current_user()
    system = platform.system() or ""
    machine = platform.machine() or ""

    strong = bool(node.strip()) and bool(user.strip())
    evidence = "|".join(
        part.strip().casefold() for part in (node, user, system, machine)
    )
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    return {
        "kind": "hostname+user" if strong else "weak",
        "node": node,
        "user": user,
        "system": system,
        "machine": machine,
        "binding_hash": f"sha256:{digest}",
    }


def derive_host_id(binding_hash: str) -> str:
    """Deterministic host id for a machine binding.

    Deterministic means re-installing OGX on the same machine/user reuses the
    same HID instead of creating a new one, while a copied state directory on a
    different machine resolves to a different HID.
    """

    return str(uuid5(HOST_ID_NAMESPACE, f"host:{binding_hash}"))


def host_identity_path(state_root: Path | str | None = None) -> Path:
    """Location of ``host.json`` inside the OGX state root."""

    root = Path(state_root) if state_root is not None else _default_state_root()
    return root / HOST_STATE_DIRNAME / HOST_IDENTITY_FILENAME


def _default_state_root() -> Path:
    from ..config import state_dir

    return state_dir()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".host-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        tmp_path = Path(handle.name)
    try:
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def load_host_identity(state_root: Path | str | None = None) -> HostIdentity | None:
    """Read ``host.json``; return ``None`` when it does not exist yet."""

    path = host_identity_path(state_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IdentityCorruptedError(
            f"Invalid JSON in host identity: {exc}", path=str(path)
        ) from exc
    return HostIdentity.from_dict(data)


def create_host_identity(
    state_root: Path | str | None = None,
    *,
    display_name: str = "",
    confirm: bool = False,
    binding: dict[str, str] | None = None,
) -> HostIdentity:
    """Create ``host.json`` for this machine.

    Refuses to overwrite an existing identity unless ``confirm`` is set. When
    the existing identity belongs to a different machine binding, an explicit
    ``confirm`` is still required and the new identity is written (never merged
    with the old one).
    """

    path = host_identity_path(state_root)
    if path.exists() and not confirm:
        raise IdentityValidationError(
            f"Host identity already exists at {path}; pass confirm=True to replace it",
            field="host_id",
        )

    evidence = binding or machine_binding()
    host = HostIdentity(
        schema_version=HOST_SCHEMA_VERSION,
        host_id=derive_host_id(evidence["binding_hash"]),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Never default to the raw hostname: only the binding digest is
        # persisted as machine evidence.
        display_name=display_name or "local-host",
        binding_kind=evidence.get("kind", "weak"),
        binding_hash=evidence["binding_hash"],
        platform={
            "system": evidence.get("system", ""),
            "machine": evidence.get("machine", ""),
        },
    )
    _atomic_write_json(path, host.to_dict())
    return host


def load_or_create_host_identity(
    state_root: Path | str | None = None,
    *,
    display_name: str = "",
) -> HostIdentity:
    """Idempotent: read the existing host identity or create one."""

    existing = load_host_identity(state_root)
    if existing is not None:
        return existing
    return create_host_identity(state_root, display_name=display_name)


def verify_host_identity(
    identity: HostIdentity | None = None,
    *,
    state_root: Path | str | None = None,
    binding: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verify a host identity against the current machine.

    Returns a report with ``state`` in
    ``verified | weak | foreign_or_renamed | missing`` and an ``access`` mode.
    Ownership-sensitive writes must be refused when ``access`` is
    ``read_only``.
    """

    identity = identity if identity is not None else load_host_identity(state_root)
    evidence = binding or machine_binding()

    if identity is None:
        return {
            "state": "missing",
            "access": "read_only",
            "binding_kind": evidence.get("kind", "weak"),
            "host_id": None,
            "expected_host_id": derive_host_id(evidence["binding_hash"]),
            "detail": "No host identity is registered for this state root.",
        }

    matches = identity.binding_hash == evidence["binding_hash"]
    if matches and identity.binding_kind == "weak":
        return {
            "state": "weak",
            "access": "read_only_for_ownership_operations",
            "binding_kind": "weak",
            "host_id": identity.host_id,
            "detail": (
                "Host binding is weak (no hostname or no user). Project data is "
                "usable, but ownership-sensitive operations require a stronger "
                "binding."
            ),
        }
    if matches:
        return {
            "state": "verified",
            "access": "read_write",
            "binding_kind": identity.binding_kind,
            "host_id": identity.host_id,
            "detail": "Host identity matches this machine binding.",
        }
    return {
        "state": "foreign_or_renamed",
        "access": "read_only",
        "binding_kind": identity.binding_kind,
        "host_id": identity.host_id,
        "expected_host_id": derive_host_id(evidence["binding_hash"]),
        "detail": (
            "The stored host identity does not match this machine. The state "
            "directory was copied from another host or the machine binding "
            "changed (hostname/user). GUARDIAN-X does not adopt it silently."
        ),
    }


def adopt_host_identity(
    state_root: Path | str | None = None,
    *,
    confirm: bool = False,
    binding: dict[str, str] | None = None,
) -> HostIdentity:
    """Explicitly re-bind ``host.json`` to the current machine."""

    if not confirm:
        raise IdentityValidationError(
            "Adopting a host identity requires explicit confirmation.",
            field="host_id",
        )
    return create_host_identity(state_root, confirm=True, binding=binding)
