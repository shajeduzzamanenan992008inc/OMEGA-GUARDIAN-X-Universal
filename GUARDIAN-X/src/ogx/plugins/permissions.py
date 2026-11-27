"""Least-privilege capability grants for the plugin runtime.

Policy:

* Deny by default. A declared capability is *not* a granted capability.
* Effective permissions are the intersection of the manifest declaration and
  the explicit local grant, so a granted superset cannot escalate the plugin.
* Grants are bound to a publisher and an exact plugin version; upgrading a
  plugin requires a fresh grant.
* Sensitive capabilities (workspace writes, process spawn, outbound network)
  require an explicit approver in addition to the grantor.
* Expired, revoked and version-mismatched grants authorize nothing.
* Dependencies never inherit capabilities: each plugin is authorized from its
  own manifest and its own grant.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .manifest import PluginManifest

CAPABILITY_RISK: Mapping[str, str] = {
    "fs.read.package": "low",
    "artifact.emit": "low",
    "env.read.allowlisted": "medium",
    "fs.read.workspace": "medium",
    "fs.write.package": "medium",
    "process.spawn": "high",
    "fs.write.workspace": "high",
    "network.outbound": "high",
}

SENSITIVE_CAPABILITIES = frozenset(
    {"process.spawn", "fs.write.workspace", "network.outbound"}
)


class PermissionError_(ValueError):
    """Raised when a capability request is not authorized."""


# Keep a descriptive alias that does not shadow the built-in ``PermissionError``.
PermissionDenied = PermissionError_


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PermissionDenied("Grant timestamp is malformed.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True, slots=True)
class Grant:
    plugin_name: str
    plugin_version: str
    publisher: str
    capabilities: frozenset[str]
    granted_by: str
    granted_at: str
    approved_by: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None
    revoked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_name": self.plugin_name,
            "plugin_version": self.plugin_version,
            "publisher": self.publisher,
            "capabilities": sorted(self.capabilities),
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "approved_by": self.approved_by,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "revoked_reason": self.revoked_reason,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "Grant":
        required = {
            "plugin_name",
            "plugin_version",
            "publisher",
            "capabilities",
            "granted_by",
            "granted_at",
        }
        if not isinstance(payload, Mapping) or not required <= set(payload):
            raise PermissionDenied("Grant record is malformed.")
        capabilities = payload["capabilities"]
        if not isinstance(capabilities, (list, tuple)) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise PermissionDenied("Grant capabilities must be a list of strings.")
        return Grant(
            plugin_name=str(payload["plugin_name"]),
            plugin_version=str(payload["plugin_version"]),
            publisher=str(payload["publisher"]),
            capabilities=frozenset(capabilities),
            granted_by=str(payload["granted_by"]),
            granted_at=str(payload["granted_at"]),
            approved_by=payload.get("approved_by"),
            expires_at=payload.get("expires_at"),
            revoked_at=payload.get("revoked_at"),
            revoked_reason=payload.get("revoked_reason"),
        )

    def is_revoked(self) -> bool:
        return bool(self.revoked_at)

    def is_expired(self, now: datetime | None = None) -> bool:
        expiry = _as_time(self.expires_at)
        if expiry is None:
            return False
        return (now or _now()) >= expiry


def make_grant(
    manifest: PluginManifest,
    capabilities: Iterable[str],
    *,
    granted_by: str,
    approved_by: str | None = None,
    expires_at: str | None = None,
    now: datetime | None = None,
) -> Grant:
    """Create a grant after validating that every capability is reviewable."""

    requested = {str(cap) for cap in capabilities}
    unknown = requested - set(CAPABILITY_RISK)
    if unknown:
        raise PermissionDenied(f"Unknown capability: {', '.join(sorted(unknown))}.")
    if not granted_by.strip():
        raise PermissionDenied("A grant requires an explicit grantor identity.")
    sensitive = requested & SENSITIVE_CAPABILITIES
    if sensitive and not (approved_by and approved_by.strip()):
        raise PermissionDenied(
            "Sensitive capabilities require an explicit approver: "
            + ", ".join(sorted(sensitive))
        )
    if sensitive and approved_by and approved_by.strip() == granted_by.strip():
        raise PermissionDenied("Sensitive capability grants require two distinct identities.")
    return Grant(
        plugin_name=manifest.name,
        plugin_version=str(manifest.version),
        publisher=manifest.publisher,
        capabilities=frozenset(requested),
        granted_by=granted_by.strip(),
        granted_at=(now or _now()).isoformat(),
        approved_by=(approved_by.strip() if approved_by else None),
        expires_at=expires_at,
    )


def revoke_grant(grant: Grant, *, reason: str, now: datetime | None = None) -> Grant:
    if not reason.strip():
        raise PermissionDenied("Revocation requires a reason.")
    return replace(
        grant,
        revoked_at=(now or _now()).isoformat(),
        revoked_reason=reason.strip()[:200],
    )


class PermissionPolicy:
    """Evaluate effective capabilities for a plugin execution request."""

    def __init__(self, *, clock: Any = _now) -> None:
        self._clock = clock

    def effective(
        self,
        manifest: PluginManifest,
        grant: Grant | None,
        *,
        now: datetime | None = None,
    ) -> frozenset[str]:
        moment = now or self._clock()
        if grant is None:
            if manifest.permissions:
                raise PermissionDenied(
                    f"Plugin {manifest.name} declares capabilities but has no grant."
                )
            return frozenset()
        if grant.is_revoked():
            raise PermissionDenied("Plugin capability grant has been revoked.")
        if grant.is_expired(moment):
            raise PermissionDenied("Plugin capability grant has expired.")
        if grant.plugin_name != manifest.name:
            raise PermissionDenied("Capability grant belongs to a different plugin.")
        if grant.plugin_version != str(manifest.version):
            raise PermissionDenied(
                "Capability grant is bound to a different plugin version; re-grant required."
            )
        if grant.publisher != manifest.publisher:
            raise PermissionDenied("Capability grant publisher does not match the manifest.")

        declared = set(manifest.permissions)
        missing = declared - set(grant.capabilities)
        if missing:
            raise PermissionDenied(
                "Plugin declares capabilities that were not granted: "
                + ", ".join(sorted(missing))
            )
        if declared & SENSITIVE_CAPABILITIES and not grant.approved_by:
            raise PermissionDenied(
                "Sensitive capabilities require an approved grant."
            )
        # Never return more than the manifest declared (no grant-based escalation).
        return frozenset(declared & set(grant.capabilities))

    def require(
        self,
        manifest: PluginManifest,
        grant: Grant | None,
        capability: str,
        *,
        now: datetime | None = None,
    ) -> None:
        if capability not in self.effective(manifest, grant, now=now):
            raise PermissionDenied(
                f"Plugin {manifest.name} is not authorized for capability {capability!r}."
            )
