"""EXPERIMENTAL reverse proxy: puts the guard in front of ANY HTTP application (Node, PHP, Go, Java, ...).

Scope (deliberately small): HTTP/1.1 origin-form requests, Content-Length bodies up to max_body_bytes,
one upstream, connection-per-request. No TLS termination, no WebSocket/Upgrade, no chunked request bodies.
For production traffic put a managed WAF / hardened reverse proxy in front as well; this is defence in depth.
"""
from __future__ import annotations

import http.client
import logging
import socket
import ssl
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from ogx.runtime.access import ip_in
from ogx.runtime.engine import Shield
from ogx.runtime.request import Request

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te",
              "trailer", "transfer-encoding", "upgrade"}
FORWARDED = {"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded", "x-real-ip"}
_LOG = logging.getLogger("omega.proxy")


@dataclass(frozen=True, slots=True)
class Upstream:
    scheme: str
    host: str
    port: int

    @property
    def netloc(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"


def parse_upstream(value: str) -> Upstream:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.path not in {"", "/"} or parts.query or parts.username:
        raise ValueError("upstream must look like http://host:port or https://host:port (no path, query or credentials)")
    return Upstream(parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))


def parse_listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise ValueError("listen must look like HOST:PORT, e.g. 127.0.0.1:8080")
    return host or "127.0.0.1", int(port)


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, handler, max_connections: int = 256):
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(max_connections)


def make_handler(shield: Shield, upstream: Upstream, *, timeout: float = 30.0, preserve_host: bool = False):
    class ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "omega-shield"
        sys_version = ""
        timeout = 15  # per-recv socket timeout while reading the client request (slowloris mitigation)

        def version_string(self) -> str:
            return "omega-shield"

        def log_message(self, fmt, *args):  # events go through the guard sink / observability, not stderr
            return

        # ------------------------------------------------------------ helpers
        def _reply(self, status: int, headers: list[tuple[str, str]], body: bytes, head_only: bool = False) -> None:
            self.close_connection = True
            self.send_response_only(status)
            names = {k.lower() for k, _ in headers}
            if "date" not in names:
                self.send_header("Date", self.date_time_string())
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only and body:
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        def _simple(self, status: int, message: str) -> None:
            body = ('{"error": "%s", "status": %d}' % (message, status)).encode()
            self._reply(status, shield.harden([("Content-Type", "application/json"), ("Content-Length", str(len(body)))]), body)

        # ------------------------------------------------------------ main flow
        def _handle(self) -> None:
            self.close_connection = True
            if not self.server.slots.acquire(blocking=False):
                return self._simple(503, "service busy")
            try:
                self._process()
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            finally:
                self.server.slots.release()

        def _process(self) -> None:
            target = self.path
            if not target.startswith("/") or target.startswith("//"):
                return self._simple(400, "origin-form request target required")
            if self.headers.get("Transfer-Encoding"):
                return self._simple(501, "chunked request bodies are not supported")
            path, _, query = target.partition("?")
            headers = list(self.headers.items())
            lengths = {v.strip() for k, v in headers if k.lower() == "content-length"}
            declared = None
            if len(lengths) == 1:
                only = next(iter(lengths))
                if only.isdigit() and len(only) <= 12:
                    declared = int(only)
            body = b""
            if declared and declared <= shield.config.max_body_bytes:
                body = self.rfile.read(declared)
                if len(body) != declared:
                    return
            method = self.command.upper()
            request = Request(method, path, query, headers, body, self.client_address[0], "http", declared)
            decision = shield.inspect(request)
            if decision.action == "block":
                status, out_headers, payload = shield.block_response(decision, "http")
                return self._reply(status, out_headers, payload, head_only=method == "HEAD")
            if declared and declared > shield.config.max_body_bytes:   # monitor mode cannot stream oversized bodies
                return self._simple(413, "request body too large for this proxy")
            self._forward(method, target, headers, body, decision, request)

        def _forward(self, method, target, headers, body, decision, request) -> None:
            listed = {t.strip().lower() for k, v in headers if k.lower() == "connection" for t in v.split(",")}
            peer = self.client_address[0]
            trusted = bool(shield.config.trusted_proxies) and ip_in(peer, shield.config.trusted_proxies)
            original_host = self.headers.get("Host", "")
            out = [(k, v) for k, v in headers
                   if k.lower() not in HOP_BY_HOP and k.lower() not in listed and k.lower() not in FORWARDED
                   and k.lower() not in {"host", "content-length", "x-request-id"}]
            chain = ", ".join(v for k, v in headers if k.lower() == "x-forwarded-for") if trusted else ""
            out.append(("Host", original_host if preserve_host and original_host else upstream.netloc))
            out.append(("X-Forwarded-For", f"{chain}, {peer}" if chain else peer))
            out.append(("X-Forwarded-Proto", "http"))
            if original_host:
                out.append(("X-Forwarded-Host", original_host))
            out.append(("X-Request-ID", decision.request_id))
            if body or method in {"POST", "PUT", "PATCH"}:
                out.append(("Content-Length", str(len(body))))
            out.append(("Connection", "close"))
            if upstream.scheme == "https":
                conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                    upstream.host, upstream.port, timeout=timeout, context=ssl.create_default_context())
            else:
                conn = http.client.HTTPConnection(upstream.host, upstream.port, timeout=timeout)
            try:
                conn.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
                for key, value in out:           # putheader keeps duplicate headers (e.g. several Cookie lines)
                    conn.putheader(key, value)
                conn.endheaders(body or None)
                response = conn.getresponse()
            except ValueError:
                conn.close()
                return self._simple(400, "invalid header value")
            except socket.timeout:
                conn.close()
                return self._simple(504, "upstream timed out")
            except (OSError, http.client.HTTPException) as exc:
                _LOG.warning("upstream error: %s", type(exc).__name__, extra={"fields": {"request_id": decision.request_id}})
                conn.close()
                return self._simple(502, "bad gateway")
            try:
                relay = [(k, v) for k, v in response.getheaders() if k.lower() not in HOP_BY_HOP]
                relay = shield.harden(relay, "http", decision.request_id)
                no_body = method == "HEAD" or response.status in {204, 304} or 100 <= response.status < 200
                self.close_connection = True
                self.send_response_only(response.status, response.reason)
                if "date" not in {k.lower() for k, _ in relay}:
                    self.send_header("Date", self.date_time_string())
                for key, value in relay:
                    self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                if not no_body:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            finally:
                response.close()
                conn.close()

    for verb in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        setattr(ProxyHandler, f"do_{verb}", ProxyHandler._handle)
    return ProxyHandler


def make_proxy(shield: Shield, upstream: str | Upstream, listen: tuple[str, int] = ("127.0.0.1", 8080), *,
               timeout: float = 30.0, preserve_host: bool = False, max_connections: int = 256) -> ProxyServer:
    target = parse_upstream(upstream) if isinstance(upstream, str) else upstream
    return ProxyServer(listen, make_handler(shield, target, timeout=timeout, preserve_host=preserve_host), max_connections)


def run_proxy(shield: Shield, upstream: str, listen: str = "127.0.0.1:8080", **options) -> int:
    host, port = parse_listen(listen)
    server = make_proxy(shield, upstream, (host, port), **options)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("[shield] WARNING: listening on a non-loopback address. Make sure the upstream is not reachable "
              "directly from the internet, otherwise the guard can be bypassed.", file=sys.stderr)
    print(f"[shield] guard proxy listening on {server.server_address[0]}:{server.server_address[1]} -> {upstream} "
          f"(mode={shield.config.mode}, profile={shield.config.signature_profile})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
