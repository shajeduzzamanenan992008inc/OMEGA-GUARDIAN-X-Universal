"""Token refresh and rotation abstractions with per-provider honesty (PROV-07).

Not every provider can refresh a token, and the ones that can do it in
incompatible ways. This module records exactly which mechanism each supported
provider actually offers, refuses to invent an endpoint for the others, and
keeps rotated secrets out of logs, reports and object representations.

Supported providers
-------------------
``github``          GitHub App installation access tokens are *re-issued*
                    (``POST /app/installations/{id}/access_tokens``) with an
                    app JWT; user PATs and OAuth app tokens have no refresh.
``gitlab``          OAuth2 ``grant_type=refresh_token``.
``bitbucket``       OAuth2 ``grant_type=refresh_token`` (access tokens are
                    short-lived; app passwords rotate manually).
``gitea``           No refresh endpoint; token rotation is manual.
``forgejo``         No refresh endpoint; token rotation is manual.
``azure_devops``    PATs cannot be refreshed; rotate manually.
``aws_codecommit``  Temporary credentials come from STS; refresh means
                    re-assuming the role, which needs AWS credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .security import CredentialPolicy, redact, validate_api_base
from .transport import HttpTransport, TransportError

__all__ = [
    "CredentialHandle",
    "CredentialRotationError",
    "PROVIDER_REFRESH_SUPPORT",
    "RefreshClient",
    "RefreshKind",
    "RefreshUnsupported",
    "RotationStore",
    "RotatedCredential",
    "refresh_support",
]

# Refresh a little before the provider's expiry so an in-flight request never
# carries an already-dead token.
EXPIRY_SKEW_SECONDS = 120


class CredentialRotationError(RuntimeError):
    """Raised when a credential cannot be refreshed or rotated safely."""


class RefreshUnsupported(CredentialRotationError):
    """Raised when the provider has no supported token-refresh mechanism."""


class RefreshKind(str):
    OAUTH2 = "oauth2_refresh_token"
    GITHUB_APP = "github_app_installation"
    AWS_STS = "aws_sts_assume_role"
    MANUAL = "manual_rotation"


@dataclass(frozen=True, slots=True)
class ProviderRefreshSupport:
    provider_id: str
    kind: str
    token_endpoint: str | None
    supports_refresh: bool
    credential_policy: CredentialPolicy
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "kind": self.kind,
            "token_endpoint": self.token_endpoint,
            "supports_refresh": self.supports_refresh,
            "credential_env": list(self.credential_policy.env_vars),
            "username_env": self.credential_policy.username_env,
            "notes": self.notes,
        }


PROVIDER_REFRESH_SUPPORT: dict[str, ProviderRefreshSupport] = {
    "github": ProviderRefreshSupport(
        provider_id="github",
        kind=RefreshKind.GITHUB_APP,
        token_endpoint="https://api.github.com/app/installations/{installation_id}/access_tokens",
        supports_refresh=True,
        credential_policy=CredentialPolicy("github", ("OGX_GITHUB_TOKEN",)),
        notes=(
            "Only GitHub App installation tokens are re-issued, and that call needs a signed "
            "app JWT that this project does not create. Personal access tokens and OAuth app "
            "tokens have no refresh endpoint and must be rotated by the owner."
        ),
    ),
    "gitlab": ProviderRefreshSupport(
        provider_id="gitlab",
        kind=RefreshKind.OAUTH2,
        token_endpoint="https://gitlab.com/oauth/token",
        supports_refresh=True,
        credential_policy=CredentialPolicy("gitlab", ("OGX_GITLAB_TOKEN",)),
        notes=(
            "OAuth2 refresh tokens only. Project/group/personal access tokens are long-lived "
            "and are rotated manually."
        ),
    ),
    "bitbucket": ProviderRefreshSupport(
        provider_id="bitbucket",
        kind=RefreshKind.OAUTH2,
        token_endpoint="https://bitbucket.org/site/oauth2/access_token",
        supports_refresh=True,
        credential_policy=CredentialPolicy("bitbucket", ("OGX_BITBUCKET_TOKEN",)),
        notes=(
            "OAuth2 consumers can refresh. App passwords (user:secret) cannot be refreshed; "
            "they must be revoked and re-created."
        ),
    ),
    "gitea": ProviderRefreshSupport(
        provider_id="gitea",
        kind=RefreshKind.MANUAL,
        token_endpoint=None,
        supports_refresh=False,
        credential_policy=CredentialPolicy("gitea", ("OGX_GITEA_TOKEN",), "OGX_GITEA_USERNAME"),
        notes="No refresh endpoint; access tokens are rotated manually by the instance owner.",
    ),
    "forgejo": ProviderRefreshSupport(
        provider_id="forgejo",
        kind=RefreshKind.MANUAL,
        token_endpoint=None,
        supports_refresh=False,
        credential_policy=CredentialPolicy("forgejo", ("OGX_FORGEJO_TOKEN",), "OGX_FORGEJO_USERNAME"),
        notes="No refresh endpoint; access tokens are rotated manually by the instance owner.",
    ),
    "azure_devops": ProviderRefreshSupport(
        provider_id="azure_devops",
        kind=RefreshKind.MANUAL,
        token_endpoint=None,
        supports_refresh=False,
        credential_policy=CredentialPolicy("azure_devops", ("OGX_AZURE_DEVOPS_TOKEN",)),
        notes=(
            "Personal access tokens are not refreshable through the REST API; rotate in Azure "
            "DevOps and update the environment variable."
        ),
    ),
    "aws_codecommit": ProviderRefreshSupport(
        provider_id="aws_codecommit",
        kind=RefreshKind.AWS_STS,
        token_endpoint="https://sts.amazonaws.com/",
        supports_refresh=True,
        credential_policy=CredentialPolicy(
            "aws_codecommit", ("OGX_AWS_CODECOMMIT_TOKEN",)
        ),
        notes=(
            "CodeCommit uses SigV4 with temporary STS credentials. Refreshing means calling "
            "sts:AssumeRole again with the base credentials; no bearer token is involved."
        ),
    ),
}


def refresh_support(provider_id: str) -> ProviderRefreshSupport:
    try:
        return PROVIDER_REFRESH_SUPPORT[provider_id]
    except KeyError as exc:
        raise CredentialRotationError(f"Unknown provider for credential rotation: {provider_id!r}.") from exc


@dataclass(frozen=True, slots=True)
class CredentialHandle:
    provider_id: str
    secret: str = field(repr=False)
    issued_at: float
    expires_at: float | None = None
    source: str = "environment"

    def is_expired(self, now: float | None = None, skew: int = EXPIRY_SKEW_SECONDS) -> bool:
        if self.expires_at is None:
            return False
        moment = time.time() if now is None else now
        return moment >= self.expires_at - skew

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "fingerprint": fingerprint(self.secret),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "source": self.source,
            "expired": self.is_expired(),
        }

    def __repr__(self) -> str:  # never leak the secret through repr/pytest output
        return (
            f"CredentialHandle(provider_id={self.provider_id!r}, "
            f"fingerprint={fingerprint(self.secret)!r}, expires_at={self.expires_at!r})"
        )


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RotatedCredential:
    provider_id: str
    fingerprint: str
    rotated_at: float
    expires_at: float | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "fingerprint": self.fingerprint,
            "rotated_at": self.rotated_at,
            "expires_at": self.expires_at,
            "source": self.source,
        }


class RotationStore:
    """Records *fingerprints* and expiry of rotated credentials (never values).

    The owner injects the new value through the environment (or a secret
    manager); the store only proves that a rotation happened and which
    fingerprint is current, so audits never contain a live token.
    """

    def __init__(self, state_home: Path | str) -> None:
        self.path = Path(state_home) / "credentials" / "rotations.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": 1, "providers": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialRotationError("Rotation store is unreadable.") from exc
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise CredentialRotationError("Unsupported rotation store schema.")
        return payload

    def _save(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def record(self, handle: CredentialHandle) -> RotatedCredential:
        if not handle.secret or not handle.provider_id:
            raise CredentialRotationError("A rotation record needs a provider and a secret.")
        payload = self._load()
        entry = RotatedCredential(
            provider_id=handle.provider_id,
            fingerprint=fingerprint(handle.secret),
            rotated_at=time.time(),
            expires_at=handle.expires_at,
            source=handle.source,
        )
        payload["providers"][handle.provider_id] = entry.to_dict()
        self._save(payload)
        return entry

    def current(self, provider_id: str) -> RotatedCredential | None:
        entry = self._load()["providers"].get(provider_id)
        if not isinstance(entry, dict):
            return None
        return RotatedCredential(
            provider_id=str(entry["provider_id"]),
            fingerprint=str(entry["fingerprint"]),
            rotated_at=float(entry["rotated_at"]),
            expires_at=entry.get("expires_at"),
            source=str(entry.get("source", "rotation")),
        )

    def revoke(self, provider_id: str) -> bool:
        payload = self._load()
        existed = payload["providers"].pop(provider_id, None) is not None
        if existed:
            self._save(payload)
        return existed

    def is_current(self, handle: CredentialHandle) -> bool:
        record = self.current(handle.provider_id)
        return record is not None and record.fingerprint == fingerprint(handle.secret)


class RefreshClient:
    """Bounded OAuth2 refresh for providers that actually document one."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        provider_id: str = "gitlab",
    ) -> None:
        self.support = refresh_support(provider_id)
        self._transport = transport or HttpTransport()

    def refresh(
        self,
        *,
        client_id: str,
        refresh_token: str,
        client_secret: str | None = None,
        now: float | None = None,
    ) -> CredentialHandle:
        support = self.support
        if not support.supports_refresh or support.kind != RefreshKind.OAUTH2:
            raise RefreshUnsupported(
                f"{support.provider_id} has no OAuth2 refresh flow: {support.notes}"
            )
        if not support.token_endpoint:
            raise RefreshUnsupported(f"{support.provider_id} declares no token endpoint.")
        if not client_id or not refresh_token:
            raise CredentialRotationError("A refresh needs a client id and a refresh token.")

        endpoint = validate_api_base(support.token_endpoint)
        form: dict[str, str] = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        }
        if client_secret:
            form["client_secret"] = client_secret
        body = "&".join(f"{key}={_quote(value)}" for key, value in form.items()).encode("utf-8")
        try:
            result = self._transport.send(
                "POST",
                endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                raw_body=body,
                write=True,
                expected_host=endpoint.split("/")[2],
                correlation_id=f"refresh-{support.provider_id}",
                provider=support.provider_id,
            )
        except TransportError as exc:
            raise CredentialRotationError(
                f"Token refresh for {support.provider_id} failed: "
                f"{redact(str(exc), (refresh_token, client_secret or ''))}"
            ) from exc
        if not 200 <= result.status < 300:
            raise CredentialRotationError(
                f"Token refresh for {support.provider_id} returned HTTP {result.status}."
            )
        try:
            payload = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialRotationError("Token refresh response was not valid JSON.") from exc
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise CredentialRotationError("Token refresh response contained no access token.")
        expires_in = payload.get("expires_in")
        issued = time.time() if now is None else now
        expires_at = issued + float(expires_in) if isinstance(expires_in, (int, float)) else None
        return CredentialHandle(
            provider_id=support.provider_id,
            secret=token,
            issued_at=issued,
            expires_at=expires_at,
            source="oauth2_refresh",
        )


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
