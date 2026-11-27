"""Inbound webhook authenticity verification.

The verifier authenticates a payload that arrives *from* a Git provider. It is
provider-neutral, standard-library only and performs no network I/O.

Guarantees:

* secrets are compared in constant time (:func:`hmac.compare_digest`);
* a missing, malformed or truncated signature is a failure, never a pass;
* the body is bounded before any hashing, and body content is never included
  in an error message;
* the event name is read only after the signature is accepted, and must be in
  the caller's allowlist when one is configured;
* optional delivery-id replay suppression is bounded and TTL-scoped.

Honest limits:

* Providers without a payload signature (Azure DevOps service hooks and AWS
  CodeCommit/SNS notifications) cannot be authenticated here. Constructing a
  verifier for them raises ``unsupported_provider`` instead of pretending.
* The replay guard is process-local memory. A receiver with several workers
  needs shared storage or the provider's own delivery deduplication.
* Verification proves the payload was produced by somebody holding the secret.
  It does not make the payload's *contents* trustworthy as instructions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_REPLAY_WINDOW_SECONDS = 300
DEFAULT_MAX_REPLAY_ENTRIES = 4096
MIN_SECRET_BYTES = 16


class WebhookVerificationError(ValueError):
    """A webhook payload failed authenticity or policy checks."""

    def __init__(self, message: str, *, code: str = "webhook_verification_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WebhookScheme:
    """Header names and signature algorithm for one provider."""

    provider_id: str
    algorithm: str  # "hmac-sha256" | "shared-token" | "none"
    signature_header: str = ""
    event_header: str = ""
    delivery_header: str = ""
    signature_prefix: str = ""

    @property
    def supported(self) -> bool:
        return self.algorithm in {"hmac-sha256", "shared-token"}


SCHEMES: dict[str, WebhookScheme] = {
    "github": WebhookScheme(
        provider_id="github",
        algorithm="hmac-sha256",
        signature_header="x-hub-signature-256",
        event_header="x-github-event",
        delivery_header="x-github-delivery",
        signature_prefix="sha256=",
    ),
    "gitlab": WebhookScheme(
        provider_id="gitlab",
        algorithm="shared-token",
        signature_header="x-gitlab-token",
        event_header="x-gitlab-event",
        delivery_header="x-gitlab-event-uuid",
    ),
    "bitbucket": WebhookScheme(
        provider_id="bitbucket",
        algorithm="hmac-sha256",
        signature_header="x-hub-signature",
        event_header="x-event-key",
        delivery_header="x-request-uuid",
        signature_prefix="sha256=",
    ),
    "gitea": WebhookScheme(
        provider_id="gitea",
        algorithm="hmac-sha256",
        signature_header="x-gitea-signature",
        event_header="x-gitea-event",
        delivery_header="x-gitea-delivery",
    ),
    "forgejo": WebhookScheme(
        provider_id="forgejo",
        algorithm="hmac-sha256",
        signature_header="x-forgejo-signature",
        event_header="x-forgejo-event",
        delivery_header="x-forgejo-delivery",
    ),
    # No cryptographic signature is part of the delivery contract.
    "azure_devops": WebhookScheme(provider_id="azure_devops", algorithm="none"),
    "aws_codecommit": WebhookScheme(provider_id="aws_codecommit", algorithm="none"),
}

SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(
    sorted(provider for provider, scheme in SCHEMES.items() if scheme.supported)
)


def _lower_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        result[str(key).strip().lower()] = str(value)
    return result


def _secret_bytes(secret: str | bytes | None, *, minimum: int = MIN_SECRET_BYTES) -> bytes:
    if secret is None:
        raise WebhookVerificationError(
            "A webhook secret is required to verify this provider.", code="secret_required"
        )
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if not raw:
        raise WebhookVerificationError(
            "A webhook secret is required to verify this provider.", code="secret_required"
        )
    if len(raw) < max(1, int(minimum)):
        raise WebhookVerificationError(
            f"The webhook secret must be at least {int(minimum)} bytes.", code="secret_too_short"
        )
    return raw


def _body_bytes(body: Any, *, maximum: int) -> bytes:
    if isinstance(body, str):
        raise WebhookVerificationError(
            "Webhook bodies must be supplied as bytes; re-encoding would change the signature.",
            code="invalid_body",
        )
    if isinstance(body, (bytearray, memoryview)):
        raw = bytes(body)
    elif isinstance(body, bytes):
        raw = body
    else:
        raise WebhookVerificationError(
            "Webhook bodies must be supplied as bytes.", code="invalid_body"
        )
    if len(raw) > maximum:
        raise WebhookVerificationError(
            f"Webhook body exceeds the {maximum}-byte limit.", code="body_too_large"
        )
    return raw


def _hex_signature(value: str, *, prefix: str) -> bytes:
    candidate = value.strip()
    if prefix:
        if not candidate.lower().startswith(prefix):
            raise WebhookVerificationError(
                "Webhook signature is missing the expected algorithm prefix.",
                code="invalid_signature_format",
            )
        candidate = candidate[len(prefix):]
    if len(candidate) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in candidate):
        raise WebhookVerificationError(
            "Webhook signature is not a SHA-256 hex digest.", code="invalid_signature_format"
        )
    return bytes.fromhex(candidate)


class DeliveryReplayGuard:
    """Bounded, TTL-scoped memory of accepted delivery ids (process-local)."""

    def __init__(
        self,
        *,
        window_seconds: float = DEFAULT_REPLAY_WINDOW_SECONDS,
        max_entries: int = DEFAULT_MAX_REPLAY_ENTRIES,
        clock: Any = time.monotonic,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._window = float(window_seconds)
        self._max = int(max_entries)
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_remember(self, key: str) -> bool:
        """Return ``False`` when ``key`` was already accepted inside the window."""

        now = self._clock()
        with self._lock:
            expired = [name for name, expiry in self._seen.items() if expiry <= now]
            for name in expired:
                self._seen.pop(name, None)
            if key in self._seen:
                return False
            if len(self._seen) >= self._max:
                oldest = min(self._seen, key=self._seen.__getitem__)
                self._seen.pop(oldest, None)
            self._seen[key] = now + self._window
            return True


@dataclass(frozen=True, slots=True)
class VerifiedWebhook:
    """An authenticated delivery. The body is kept as bytes, unparsed."""

    provider_id: str
    event: str
    delivery_id: Optional[str]
    body: bytes
    digest: str

    def json(self) -> Any:
        """Parse the authenticated body as JSON, with a typed failure mode."""

        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookVerificationError(
                "Authenticated webhook body is not valid JSON.", code="invalid_json"
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "event": self.event,
            "delivery_id": self.delivery_id,
            "sha256": self.digest,
            "bytes": len(self.body),
        }


class WebhookVerifier:
    """Verify one provider's inbound webhook deliveries."""

    def __init__(
        self,
        provider_id: str,
        secret: str | bytes | None,
        *,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        allowed_events: Optional[Any] = None,
        replay_guard: Optional[DeliveryReplayGuard] = None,
        require_delivery_id: bool = False,
        min_secret_bytes: int = MIN_SECRET_BYTES,
    ) -> None:
        scheme = SCHEMES.get(str(provider_id or "").strip().lower())
        if scheme is None:
            raise WebhookVerificationError(
                f"Unknown webhook provider: {provider_id!r}.", code="unknown_provider"
            )
        if not scheme.supported:
            raise WebhookVerificationError(
                f"{scheme.provider_id} does not sign webhook payloads; authenticate the"
                " transport (mutual TLS or signed gateway) instead.",
                code="unsupported_provider",
            )
        if not isinstance(max_body_bytes, int) or max_body_bytes <= 0:
            raise WebhookVerificationError(
                "max_body_bytes must be a positive integer.", code="invalid_policy"
            )
        events = None
        if allowed_events is not None:
            events = frozenset(str(item).casefold() for item in allowed_events if str(item).strip())
            if not events:
                raise WebhookVerificationError(
                    "allowed_events was provided but is empty.", code="invalid_policy"
                )
        self.scheme = scheme
        self._secret = _secret_bytes(secret, minimum=min_secret_bytes)
        self._max_body = int(max_body_bytes)
        self._allowed_events = events
        self._replay_guard = replay_guard
        self._require_delivery_id = bool(require_delivery_id)

    # ------------------------------------------------------------------ public
    @property
    def provider_id(self) -> str:
        return self.scheme.provider_id

    def verify(self, headers: Mapping[str, Any] | None, body: bytes) -> VerifiedWebhook:
        lowered = _lower_headers(headers)
        raw = _body_bytes(body, maximum=self._max_body)

        supplied = lowered.get(self.scheme.signature_header, "")
        if not supplied:
            raise WebhookVerificationError(
                f"Missing required signature header '{self.scheme.signature_header}'.",
                code="missing_signature",
            )
        if self.scheme.algorithm == "shared-token":
            expected = self._secret
            candidate = supplied.encode("utf-8")
            if not hmac.compare_digest(candidate, expected):
                raise WebhookVerificationError(
                    "Webhook token does not match the configured secret.",
                    code="invalid_signature",
                )
        else:
            expected = hmac.new(self._secret, raw, hashlib.sha256).digest()
            candidate = _hex_signature(supplied, prefix=self.scheme.signature_prefix)
            if not hmac.compare_digest(candidate, expected):
                raise WebhookVerificationError(
                    "Webhook signature does not match the payload.",
                    code="invalid_signature",
                )

        event = lowered.get(self.scheme.event_header, "").strip()
        if not event:
            raise WebhookVerificationError(
                f"Missing required event header '{self.scheme.event_header}'.",
                code="missing_event",
            )
        if self._allowed_events is not None and event.casefold() not in self._allowed_events:
            raise WebhookVerificationError(
                "Webhook event is not in the configured allowlist.", code="event_not_allowed"
            )

        delivery_id = lowered.get(self.scheme.delivery_header, "").strip() or None
        if delivery_id is None and self._require_delivery_id:
            raise WebhookVerificationError(
                "This verifier requires a delivery id for replay protection.",
                code="missing_delivery_id",
            )
        if delivery_id is not None and self._replay_guard is not None:
            if not self._replay_guard.check_and_remember(
                f"{self.scheme.provider_id}:{delivery_id}"
            ):
                raise WebhookVerificationError(
                    "Webhook delivery id was already accepted (replay).", code="replay_detected"
                )

        return VerifiedWebhook(
            provider_id=self.scheme.provider_id,
            event=event,
            delivery_id=delivery_id,
            body=raw,
            digest=hashlib.sha256(raw).hexdigest(),
        )


def sign_payload(provider_id: str, secret: str | bytes, body: bytes) -> str:
    """Produce the header value a provider would send (used by tests/tools).

    Not a security boundary: it exists so receivers and operators can build
    deterministic fixtures without reimplementing the scheme.
    """

    scheme = SCHEMES.get(str(provider_id or "").strip().lower())
    if scheme is None:
        raise WebhookVerificationError(
            f"Unknown webhook provider: {provider_id!r}.", code="unknown_provider"
        )
    if scheme.algorithm == "shared-token":
        return secret.decode("utf-8") if isinstance(secret, bytes) else str(secret)
    if scheme.algorithm != "hmac-sha256":
        raise WebhookVerificationError(
            f"{scheme.provider_id} does not sign webhook payloads.",
            code="unsupported_provider",
        )
    digest = hmac.new(_secret_bytes(secret), bytes(body), hashlib.sha256).hexdigest()
    return f"{scheme.signature_prefix}{digest}"
