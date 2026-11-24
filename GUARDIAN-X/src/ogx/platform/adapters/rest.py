"""Shared REST plumbing for HTTP provider adapters.

Adapters declare pagination style and vendor authentication headers; all
reliability behaviour (timeouts, retries, bounded bodies, correlation IDs,
normalized errors) lives in :class:`ogx.platform.transport.HttpTransport`.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from ..base import ProviderAdapter
from ..capabilities import Capability
from ..models import _clean
from ..transport import HttpResponse

CursorFn = Callable[[Any, HttpResponse, dict[str, Any], str, int], tuple[str, dict[str, Any]] | None]


def clamp(value: int, *, default: int = 50, maximum: int = 1000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


class RestAdapter(ProviderAdapter):
    """Base class for token-authenticated JSON REST providers."""

    supports_pagination = True
    pagination_style: str = "link"
    per_page_param: str = "per_page"
    per_page: int = 50
    max_pages: int = 20

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.last_response: HttpResponse | None = None

    # ------------------------------------------------------------------ request
    def _headers(self) -> dict[str, str]:
        return {}

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.api_base}{path}"

    def _send(
        self,
        capability: Capability,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.require(capability)
        merged_headers = {**self._headers(), **(headers or {})}
        response = self._transport.send(
            method,
            self._url(path),
            headers=merged_headers,
            params=params,
            json_body=body,
            write=write,
            expected_host=urlsplit(self.api_base).hostname,
            provider=self.provider_id,
        )
        self.last_response = response
        return response

    def _json(
        self,
        capability: Capability,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        write: bool = False,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._send(
            capability,
            method,
            path,
            params=params,
            body=body,
            write=write,
            headers=headers,
        ).json(provider=self.provider_id)

    # --------------------------------------------------------------- pagination
    def _next_cursor(
        self,
        payload: Any,
        response: HttpResponse,
        params: dict[str, Any],
        path: str,
        batch_size: int,
    ) -> tuple[str, dict[str, Any]] | None:
        style = self.pagination_style
        if style == "link":
            link = response.header("link")
            if not link:
                return None
            match = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
            return (match.group(1), {}) if match else None
        if style == "page":
            following = response.header("x-next-page").strip()
            if not following:
                return None
            return (path, {**params, "page": following})
        if style == "offset":
            if batch_size < int(params.get(self.per_page_param, self.per_page)):
                return None
            page = int(params.get("page", 1)) + 1
            return (path, {**params, "page": str(page)})
        if style == "body_next":
            if isinstance(payload, dict) and isinstance(payload.get("next"), str):
                return (payload["next"], {})
            return None
        if style == "continuation":
            token = payload.get("continuationToken") if isinstance(payload, dict) else None
            if not token:
                return None
            return (path, {**params, "continuationToken": token})
        return None

    def _paginate(
        self,
        capability: Capability,
        path: str,
        *,
        params: dict[str, Any],
        limit: int,
        extract: Callable[[Any], Iterable[Any]],
        max_pages: int | None = None,
    ) -> tuple[list[Any], int]:
        wanted = clamp(limit, default=self.per_page, maximum=1000)
        collected: list[Any] = []
        current_path = path
        current_params = dict(params)
        pages = 0
        page_cap = self.max_pages if max_pages is None else max(1, max_pages)
        while len(collected) < wanted and pages < page_cap:
            payload = self._json(capability, "GET", current_path, params=current_params)
            batch = [item for item in extract(payload)]
            collected.extend(batch)
            pages += 1
            cursor = self._next_cursor(
                payload, self.last_response, current_params, current_path, len(batch)
            )
            if cursor is None or not batch:
                break
            current_path, current_params = cursor
        return collected[:wanted], pages

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _owner_name(repository: str) -> tuple[str, str]:
        from ..models import split_repository_slug

        owner, name = split_repository_slug(repository)
        if not name:
            raise ValueError("A repository name is required.")
        return owner, name

    @staticmethod
    def _text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _integer(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _boolean(value: Any) -> bool:
        return bool(value)

    def _redacted(self, payload: Any) -> Any:
        return _clean(payload)
