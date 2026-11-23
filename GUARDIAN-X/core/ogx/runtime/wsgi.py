"""WSGI middleware (Flask, Django, Bottle, Pyramid, ...). Plain callable, no framework imports."""
from __future__ import annotations

import io
from http import HTTPStatus
from urllib.parse import quote

from ogx.runtime.config import RuntimeConfig, load_runtime_config
from ogx.runtime.engine import Shield
from ogx.runtime.request import Request

REQUEST_ID_KEY = "omega.request_id"


def request_from_environ(environ: dict, config: RuntimeConfig) -> Request:
    headers = [(key[5:].replace("_", "-").title(), value) for key, value in environ.items() if key.startswith("HTTP_")]
    if environ.get("CONTENT_TYPE"):
        headers.append(("Content-Type", environ["CONTENT_TYPE"]))
    length_raw = environ.get("CONTENT_LENGTH")
    declared = None
    if length_raw not in (None, ""):
        headers.append(("Content-Length", str(length_raw)))
        try:
            declared = int(length_raw)
        except ValueError:
            declared = None
    raw_uri = environ.get("RAW_URI") or environ.get("REQUEST_URI") or ""
    if raw_uri.startswith("/"):
        path, _, query = raw_uri.partition("?")
    else:  # servers hand us a decoded PATH_INFO; re-quote it so the guard sees a request-target
        path = quote(environ.get("SCRIPT_NAME", "") + environ.get("PATH_INFO", ""), safe="/%")
        query = environ.get("QUERY_STRING", "")
    body = b""
    if declared and 0 < declared <= config.max_body_bytes:
        body = environ["wsgi.input"].read(declared)
        environ["wsgi.input"] = io.BytesIO(body)   # replay for the application
    return Request(environ.get("REQUEST_METHOD", "GET"), path or "/", query, headers, body,
                   environ.get("REMOTE_ADDR", ""), environ.get("wsgi.url_scheme", "http"), declared)


class ShieldMiddleware:
    def __init__(self, app, shield: Shield | None = None, config: RuntimeConfig | str | None = None):
        if shield is None:
            shield = Shield(config if isinstance(config, RuntimeConfig) else load_runtime_config(config))
        self.app, self.shield = app, shield

    def __call__(self, environ, start_response):
        request = request_from_environ(environ, self.shield.config)
        decision = self.shield.inspect(request)
        environ[REQUEST_ID_KEY] = decision.request_id
        if decision.action == "block":
            status, headers, body = self.shield.block_response(decision, request.scheme)
            start_response(f"{status} {HTTPStatus(status).phrase}", headers)
            return [b""] if request.method.upper() == "HEAD" else [body]

        def guarded_start_response(status, headers, exc_info=None):
            hardened = self.shield.harden(list(headers), request.scheme, decision.request_id)
            return start_response(status, hardened, exc_info)

        return self.app(environ, guarded_start_response)
