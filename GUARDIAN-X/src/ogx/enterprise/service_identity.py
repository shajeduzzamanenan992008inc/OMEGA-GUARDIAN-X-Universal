"""Tenant-scoped service identities and scoped bearer tokens (ENT-05).

Until an IdP exists (ENT-03 is still unsupported), OGX cannot authenticate a
human interactively. It *can* authenticate machine callers honestly: a service
account belongs to exactly one tenant, carries one base role and an explicit
scope list, and issues opaque bearer tokens whose secret is stored only as a
SHA-256 digest.

Properties enforced here:

* a token secret is returned **once** and never persisted in clear text;
* a token can never grant more than its account's role allows
  (:func:`ogx.enterprise.authz.permissions_for_roles`), and the effective
  permission set is the *intersection* of role and scope;
* tokens are tenant-bound, expiring, revocable and single-tenant by construction;
* a revoked account revokes every token issued for it;
* authentication failures are indistinguishable (no account enumeration) and
  never echo the presented secret.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .authz import (
    Action,
    PolicyEngine,
    Principal,
    Resource,
    SERVICE_ROLES,
    permissions_for_roles,
    validate_scopes,
)

TOKEN_PREFIX = "ogxst"
TOKEN_SECRET_BYTES = 32
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 30 * 24 * 3600


class ServiceIdentityError(RuntimeError):
    """Base error for service-identity operations."""


class InvalidToken(ServiceIdentityError):
    """The presented bearer token is malformed, unknown, expired or revoked."""


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    token_id: str
    account_id: str
    organization_id: str
    subject: str
    scopes: tuple[str, ...]
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "token_id": self.token_id,
            "account_id": self.account_id,
            "organization_id": self.organization_id,
            "subject": self.subject,
            "scopes": list(self.scopes),
            "expires_at": self.expires_at,
            "warning": "This token is shown once and is not recoverable.",
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def fingerprint(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


class ServiceIdentityManager:
    """Create service accounts, issue/revoke tokens and authenticate bearers."""

    def __init__(self, store: Any, policy: PolicyEngine | None = None) -> None:
        self.store = store
        self.policy = policy or getattr(store, "policy", None) or PolicyEngine()

    # ------------------------------------------------------------- accounts
    def create_account(self, organization_id: str, name: str, base_role: str,
                       scopes: list[str] | tuple[str, ...],
                       actor_subject: str) -> dict[str, Any]:
        cleaned_name = str(name or "").strip()
        if not cleaned_name or len(cleaned_name) > 64:
            raise ValueError("A service account name is required (max 64 characters).")
        if not all(character.isalnum() or character in "-_." for character in cleaned_name):
            raise ValueError(
                "Service account names may only contain letters, digits, '-', '_' and '.'."
            )
        if base_role not in SERVICE_ROLES:
            raise ValueError(f"Service accounts require one of: {', '.join(SERVICE_ROLES)}")
        cleaned_scopes = validate_scopes(scopes)
        allowed = permissions_for_roles((base_role,))
        overreaching = sorted(set(cleaned_scopes) - allowed)
        if overreaching:
            raise ValueError(
                f"Scopes exceed the {base_role} role: {', '.join(overreaching)}"
            )
        role = self.store.member_role(organization_id, actor_subject)
        if role is None:
            raise PermissionError("Not a member of this organization.")
        self.policy.require(
            Principal(subject=actor_subject, roles=(role,), tenant_id=organization_id),
            Action.SERVICE_ACCOUNT_MANAGE,
            Resource(kind="organization", id=organization_id, tenant_id=organization_id),
        )
        return self.store.create_service_account(
            organization_id, cleaned_name, base_role, list(cleaned_scopes), actor_subject
        )

    def list_accounts(self, organization_id: str, actor_subject: str) -> list[dict[str, Any]]:
        role = self.store.member_role(organization_id, actor_subject)
        if role is None:
            raise PermissionError("Not a member of this organization.")
        self.policy.require(
            Principal(subject=actor_subject, roles=(role,), tenant_id=organization_id),
            Action.SERVICE_ACCOUNT_MANAGE,
            Resource(kind="organization", id=organization_id, tenant_id=organization_id),
        )
        return self.store.list_service_accounts(organization_id)

    # ---------------------------------------------------------------- tokens
    def issue_token(self, organization_id: str, account_id: str, actor_subject: str,
                    ttl_seconds: int = 3600) -> IssuedToken:
        if not MIN_TTL_SECONDS <= int(ttl_seconds) <= MAX_TTL_SECONDS:
            raise ValueError(
                f"Token lifetime must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS} seconds."
            )
        account = self.store.get_service_account(account_id)
        if account is None or account["organization_id"] != organization_id:
            raise ValueError("Service account not found in this organization.")
        if account["status"] != "active":
            raise ValueError("Service account is not active.")
        role = self.store.member_role(organization_id, actor_subject)
        if role is None:
            raise PermissionError("Not a member of this organization.")
        self.policy.require(
            Principal(subject=actor_subject, roles=(role,), tenant_id=organization_id),
            Action.SERVICE_ACCOUNT_MANAGE,
            Resource(kind="service_account", id=account_id, tenant_id=organization_id),
        )

        token_id = secrets.token_hex(16)
        secret = secrets.token_urlsafe(TOKEN_SECRET_BYTES)
        expires_at = _iso(_now() + timedelta(seconds=int(ttl_seconds)))
        self.store.insert_service_token(
            token_id=token_id,
            account_id=account_id,
            token_hash=hash_secret(secret),
            fingerprint_value=fingerprint(secret),
            scopes=account["scopes"],
            expires_at=expires_at,
            actor_subject=actor_subject,
        )
        return IssuedToken(
            token=f"{TOKEN_PREFIX}.{token_id}.{secret}",
            token_id=token_id,
            account_id=account_id,
            organization_id=organization_id,
            subject=f"service:{organization_id}/{account['name']}",
            scopes=tuple(account["scopes"]),
            expires_at=expires_at,
        )

    @staticmethod
    def parse_token(token: str) -> tuple[str, str]:
        value = str(token or "").strip()
        parts = value.split(".")
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
            raise InvalidToken("Malformed bearer token.")
        token_id, secret = parts[1], parts[2]
        if len(token_id) != 32 or any(character not in "0123456789abcdef" for character in token_id):
            raise InvalidToken("Malformed bearer token.")
        if len(secret) < 32:
            raise InvalidToken("Malformed bearer token.")
        return token_id, secret

    def authenticate(self, token: str) -> Principal:
        """Resolve a bearer token to a service principal or raise InvalidToken."""

        token_id, secret = self.parse_token(token)
        record = self.store.find_service_token(token_id)
        if record is None:
            raise InvalidToken("Bearer token is not recognised.")
        if not secrets.compare_digest(str(record["token_hash"]), hash_secret(secret)):
            raise InvalidToken("Bearer token is not recognised.")
        if record.get("revoked_at"):
            raise InvalidToken("Bearer token has been revoked.")
        expires_at = str(record.get("expires_at") or "")
        if not expires_at:
            raise InvalidToken("Bearer token has no expiry.")
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise InvalidToken("Bearer token expiry is malformed.") from exc
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= _now():
            raise InvalidToken("Bearer token has expired.")
        account = self.store.get_service_account(record["service_account_id"])
        if account is None or account["status"] != "active":
            raise InvalidToken("Bearer token is not recognised.")
        organization = self.store.get_organization(account["organization_id"])
        if organization is None or organization.get("status") != "active":
            raise InvalidToken("Bearer token is not recognised.")

        self.store.touch_service_token(token_id)
        return Principal(
            subject=f"service:{account['organization_id']}/{account['name']}",
            kind="service",
            roles=(account["base_role"],),
            tenant_id=account["organization_id"],
            scopes=tuple(record["scopes"]),
            display_name=account["name"],
        )

    def revoke_token(self, organization_id: str, token_id: str, actor_subject: str) -> dict:
        role = self.store.member_role(organization_id, actor_subject)
        if role is None:
            raise PermissionError("Not a member of this organization.")
        self.policy.require(
            Principal(subject=actor_subject, roles=(role,), tenant_id=organization_id),
            Action.SERVICE_ACCOUNT_MANAGE,
            Resource(kind="service_token", id=token_id, tenant_id=organization_id),
        )
        return self.store.revoke_service_token(organization_id, token_id, actor_subject)

    def list_tokens(self, organization_id: str, actor_subject: str) -> list[dict[str, Any]]:
        role = self.store.member_role(organization_id, actor_subject)
        if role is None:
            raise PermissionError("Not a member of this organization.")
        self.policy.require(
            Principal(subject=actor_subject, roles=(role,), tenant_id=organization_id),
            Action.SERVICE_ACCOUNT_MANAGE,
            Resource(kind="organization", id=organization_id, tenant_id=organization_id),
        )
        return self.store.list_service_tokens(organization_id)


__all__ = [
    "InvalidToken",
    "IssuedToken",
    "MAX_TTL_SECONDS",
    "MIN_TTL_SECONDS",
    "ServiceIdentityError",
    "ServiceIdentityManager",
    "TOKEN_PREFIX",
    "fingerprint",
    "hash_secret",
]
