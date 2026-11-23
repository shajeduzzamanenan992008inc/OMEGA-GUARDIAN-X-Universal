"""ASGI middleware (FastAPI, Starlette, Django-ASGI, Quart, ...). Plain callable, no framework imports."""
from __future__ import annotations

from http import HTTPStatus

from ogx.runtime.config import RuntimeConfig, load_runtime_config
from ogx.runtime.engine import Shield
from ogx.runtime.request import Request


def request_from_scope(scope: dict, body: bytes = b"") -> Request:
    headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope.get("headers", [])]
    raw_path = scope.get("raw_path")
    path = raw_path.decode("latin-1") if raw_path else scope.get("path", "/")
    path = path.split("?", 1)[0]
    declared = None
    for key, value in headers:
        if key.lower() == "content-length" and value.strip().isdigit():
            declared = int(value.strip())
    client = scope.get("client") or ("", 0)
    method = scope.get("method", "GET") if scope["type"] == "http" else "GET"
    return Request(method, path or "/", scope.get("query_string", b"").decode("latin-1"), headers, body,
                   client[0] or "", "https" if scope.get("scheme") in {"https", "wss"} else "http", declared)


class ShieldASGI:
    def __init__(self, app, shield: Shield | None = None, config: RuntimeConfig | str | None = None):
        if shield is None:
            shield = Shield(config if isinstance(config, RuntimeConfig) else load_runtime_config(config))
        self.app, self.shield = app, shield

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)      # lifespan etc.
            return
        limit = self.shield.config.max_body_bytes
        chunks: list[dict] = []
        body = b""
        if scope["type"] == "http":
            more = True
            while more:
                message = await receive()
                if message["type"] != "http.request":
                    chunks.append(message)
                    break
                chunks.append(message)
                body += message.get("body", b"")
                more = message.get("more_body", False)
                if len(body) > limit:
                    break                              # enough to prove the limit is exceeded; do not buffer more
        request = request_from_scope(scope, body)
        decision = self.shield.inspect(request)
        scope.setdefault("state", {})["omega_request_id"] = decision.request_id
        if decision.action == "block":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            status, headers, payload = self.shield.block_response(decision, request.scheme)
            await send({"type": "http.response.start", "status": status,
                        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers]})
            await send({"type": "http.response.body", "body": b"" if request.method.upper() == "HEAD" else payload})
            return
        replay = iter(chunks)

        async def replay_receive():
            for message in replay:
                return message
            return await receive()                     # e.g. http.disconnect

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                current = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", [])]
                hardened = self.shield.harden(current, request.scheme, decision.request_id)
                message = {**message, "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in hardened]}
            await send(message)

        await self.app(scope, replay_receive if scope["type"] == "http" else receive, guarded_send)
