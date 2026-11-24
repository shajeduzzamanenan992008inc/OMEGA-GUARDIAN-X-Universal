"""Common API reliability layer.

One transport is shared by all adapters and provides request timeouts, bounded
response sizes, pagination-safe URLs, rate-limit handling, bounded exponential
backoff with ``Retry-After`` support, normalized errors, safe JSON parsing and
correlation IDs. Unsafe write operations are never retried automatically.

The network call is injectable so unit tests update no real endpoints.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
from functools import partial
import urllib.error
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlsplit

from .errors import (
    MalformedResponseError,
    ProviderError,
    ResponseTooLargeError,
    TransportError,
    error_for_status,
)
from .security import redact, resolve_pinned_address, validate_request_url

RequestFn = Callable[[str, str, Mapping[str, str], bytes | None, float], "HttpResponse"]


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str = ""

    def header(self, name: str, default: str = "") -> str:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return default

    def json(self, *, provider: str | None = None) -> Any:
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedResponseError(
                f"{provider or 'provider'} returned a malformed JSON response.",
                provider=provider,
                detail=type(exc).__name__,
            ) from exc

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials a pre-validated address.

    The original hostname is kept for the ``Host`` header, SNI and certificate
    verification, so pinning the address does not weaken TLS identity checks.
    """

    def __init__(self, host: str, port: int, *, pinned_address: str, timeout: float) -> None:
        context = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_address = pinned_address

    def connect(self) -> None:  # noqa: D102
        sock = socket.create_connection((self._pinned_address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Plain-HTTP connection that dials a pre-validated address."""

    def __init__(self, host: str, port: int, *, pinned_address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_address = pinned_address

    def connect(self) -> None:  # noqa: D102
        self.sock = socket.create_connection((self._pinned_address, self.port), self.timeout)


def _urllib_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    *,
    max_bytes: int | None = None,
    allow_loopback_http: bool = False,
    allow_private_network: bool = False,
) -> HttpResponse:
    """Perform one request against a validated, pinned destination.

    DNS is resolved once, every answer is validated, and the socket is opened
    to that exact address. This removes the validation/connection TOCTOU window
    that a second, independent DNS lookup would reintroduce.
    """

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderError("Provider request URL must be an absolute http(s) URL.")
    host = parsed.hostname
    port = parsed.port or (443 if scheme == "https" else 80)
    address = resolve_pinned_address(
        host,
        port,
        allow_loopback=bool(allow_loopback_http or scheme == "https"),
        allow_private=allow_private_network,
    )

    # At most one byte past the limit, so a hostile body cannot exhaust memory
    # before the size check in HttpTransport.send.
    limit = max_bytes + 1 if max_bytes is not None else None
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    connection: http.client.HTTPConnection
    if scheme == "https":
        connection = _PinnedHTTPSConnection(host, port, pinned_address=address, timeout=timeout)
    else:
        connection = _PinnedHTTPConnection(host, port, pinned_address=address, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=dict(headers))
        response = connection.getresponse()
        payload = response.read(limit) if limit is not None else response.read()
        return HttpResponse(
            status=int(response.status),
            headers={key: value for key, value in response.getheaders()},
            body=payload,
            url=url,
        )
    finally:
        connection.close()


class HttpTransport:
    """Bounded, observable HTTP transport with no implicit credential reuse."""

    def __init__(
        self,
        *,
        request_fn: RequestFn | None = None,
        timeout: float = 15.0,
        max_response_bytes: int = 2_000_000,
        max_retries: int = 2,
        backoff_base: float = 0.25,
        backoff_cap: float = 2.0,
        allow_loopback_http: bool = False,
        allow_private_network: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        describe: str = "provider",
    ) -> None:
        if timeout <= 0 or timeout > 300:
            raise ValueError("timeout must be between 0 and 300 seconds.")
        if max_response_bytes < 1024 or max_response_bytes > 64 * 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1 KiB and 64 MiB.")
        if not 0 <= max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5.")
        self._request_fn = request_fn or partial(
            _urllib_request,
            max_bytes=int(max_response_bytes),
            allow_loopback_http=allow_loopback_http,
            allow_private_network=allow_private_network,
        )
        self.timeout = float(timeout)
        self.max_response_bytes = int(max_response_bytes)
        self.max_retries = int(max_retries)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.allow_loopback_http = allow_loopback_http
        self.allow_private_network = allow_private_network
        self._sleep = sleep
        self.describe = describe
        self.last_correlation_id: str | None = None
        self.request_count = 0

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None and retry_after >= 0:
            return min(retry_after, self.backoff_cap)
        return min(self.backoff_base * (2 ** attempt), self.backoff_cap)

    @staticmethod
    def _retry_after(response: HttpResponse) -> float | None:
        raw = response.header("retry-after")
        if not raw:
            return None
        try:
            return max(0.0, float(raw.strip()))
        except ValueError:
            return None

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        raw_body: bytes | None = None,
        write: bool = False,
        expected_host: str | None = None,
        correlation_id: str | None = None,
        provider: str | None = None,
    ) -> HttpResponse:
        verb = method.upper()
        if verb not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"Unsupported HTTP method: {verb}")
        if write and verb == "GET":
            raise ValueError("A write request must use a state-changing HTTP method.")

        query = urlencode(
            [(str(k), str(v)) for k, v in (params or {}).items() if v is not None],
            doseq=True,
        )
        target = f"{url}{'&' if '?' in url else '?'}{query}" if query else url
        if expected_host:
            validate_request_url(
                target,
                expected_host=expected_host,
                allow_loopback_http=self.allow_loopback_http,
                allow_private_network=self.allow_private_network,
            )
        parsed = urlsplit(target)
        if parsed.scheme not in {"https", "http"}:
            raise ProviderError(
                f"{provider or self.describe} request URL must be http(s).", provider=provider
            )

        request_headers = {
            "Accept": "application/json",
            "User-Agent": "OMEGA-GUARDIAN-X/provider-engine",
            **{str(k): str(v) for k, v in (headers or {}).items()},
        }
        if raw_body is not None:
            payload = raw_body
        elif json_body is not None:
            payload = json.dumps(dict(json_body)).encode("utf-8")
        else:
            payload = None
        if payload is not None:
            request_headers.setdefault("Content-Type", "application/json")

        attempt = 0
        while True:
            cid = correlation_id or uuid.uuid4().hex
            self.last_correlation_id = cid
            request_headers["X-OGX-Correlation-Id"] = cid
            self.request_count += 1
            try:
                response = self._request_fn(verb, target, request_headers, payload, self.timeout)
            except (
                urllib.error.URLError,
                socket.timeout,
                TimeoutError,
                ConnectionError,
                OSError,
                http.client.HTTPException,
            ) as exc:
                if not write and attempt < self.max_retries:
                    self._sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise TransportError(
                    f"{provider or self.describe} connection failed.",
                    provider=provider,
                    correlation_id=cid,
                    detail=type(exc).__name__,
                ) from exc

            if len(response.body) > self.max_response_bytes:
                raise ResponseTooLargeError(
                    f"{provider or self.describe} response exceeded the bounded size limit.",
                    provider=provider,
                    status=response.status,
                    correlation_id=cid,
                    detail=f"{len(response.body)} bytes > {self.max_response_bytes}",
                )

            if 300 <= response.status < 400:
                raise ProviderError(
                    f"{provider or self.describe} returned a redirect; refusing to follow it.",
                    provider=provider,
                    correlation_id=cid,
                )

            if response.status >= 400:
                retry_after = self._retry_after(response)
                if (
                    not write
                    and attempt < self.max_retries
                    and (response.status == 429 or 500 <= response.status <= 599)
                ):
                    self._sleep(self._backoff(attempt, retry_after))
                    attempt += 1
                    continue
                detail = redact(response.text())[:800]
                raise error_for_status(
                    response.status,
                    provider=provider or self.describe,
                    correlation_id=cid,
                    detail=detail or None,
                    retry_after=retry_after,
                )

            return response
