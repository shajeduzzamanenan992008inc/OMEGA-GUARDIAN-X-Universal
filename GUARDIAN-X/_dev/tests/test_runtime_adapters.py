import asyncio
import http.client
import io
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ogx.runtime.asgi import ShieldASGI
from ogx.runtime.config import from_mapping
from ogx.runtime.engine import Shield
from ogx.runtime.proxy import make_proxy, parse_listen, parse_upstream
from ogx.runtime.wsgi import ShieldMiddleware


def make_shield(**tables):
    base = {"ratelimit": {"enabled": False}}
    for name, values in tables.items():
        base.setdefault(name, {}).update(values)
    return Shield(from_mapping(base), sink=lambda d, r: None)


# --------------------------------------------------------------------------- WSGI
class WsgiTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def app(environ, start_response):
            length = int(environ.get("CONTENT_LENGTH") or 0)
            body = environ["wsgi.input"].read(length) if length else b""
            self.calls.append((environ["PATH_INFO"], body, environ.get("omega.request_id")))
            start_response("200 OK", [("Content-Type", "text/plain"), ("X-Frame-Options", "DENY")])
            return [b"ok:" + body]

        self.app = ShieldMiddleware(app, make_shield())

    def call(self, method="GET", path="/", query="", body=b"", headers=None, ip="203.0.113.9", app=None):
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query, "REMOTE_ADDR": ip,
                   "wsgi.input": io.BytesIO(body), "wsgi.url_scheme": "http", "SERVER_NAME": "x", "SERVER_PORT": "80"}
        if body:
            environ["CONTENT_LENGTH"] = str(len(body))
            environ["CONTENT_TYPE"] = "application/x-www-form-urlencoded"
        for key, value in (headers or {}).items():
            environ["HTTP_" + key.upper().replace("-", "_")] = value
        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured["status"], captured["headers"] = status, response_headers

        out = b"".join((app or self.app)(environ, start_response))
        return captured["status"], dict(captured["headers"]), out

    def test_allowed_request_passes_with_body_replay_and_hardening(self):
        status, headers, out = self.call("POST", "/echo", body=b"a=1&b=2")
        self.assertEqual((status, out), ("200 OK", b"ok:a=1&b=2"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")           # the app's own header wins
        self.assertEqual(headers["X-Request-ID"], self.calls[0][2])

    def test_attack_is_blocked_and_never_reaches_the_app(self):
        status, headers, out = self.call("GET", "/search", query="q=<script>alert(1)</script>")
        self.assertTrue(status.startswith("403"))
        self.assertEqual(json.loads(out)["status"], 403)
        self.assertEqual(self.calls, [])
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_traversal_in_decoded_path_info_is_blocked(self):
        self.assertTrue(self.call("GET", "/a/../../etc/passwd")[0].startswith("403"))
        self.assertEqual(self.calls, [])

    def test_oversize_body_is_rejected_without_reading_it(self):
        guarded = ShieldMiddleware(lambda e, s: (_ for _ in ()).throw(AssertionError("app reached")), make_shield(firewall={"max_body_bytes": 8}))
        status, _, _ = self.call("POST", "/up", body=b"x" * 64, app=guarded)
        self.assertTrue(status.startswith("413"))

    def test_head_requests_get_headers_but_no_body_when_blocked(self):
        status, headers, out = self.call("HEAD", "/search", query="q=<script>x</script>")
        self.assertTrue(status.startswith("403"))
        self.assertEqual(out, b"")

    def test_rate_limiting_through_the_middleware(self):
        limited = ShieldMiddleware(lambda e, s: (s("200 OK", []), [b"ok"])[1],
                                   Shield(from_mapping({"ratelimit": {"requests_per_minute": 60, "burst": 2}}), sink=lambda d, r: None))
        statuses = [self.call(app=limited)[0][:3] for _ in range(4)]
        self.assertEqual(statuses, ["200", "200", "429", "429"])

    def test_application_exceptions_propagate_unchanged(self):
        def boom(environ, start_response):
            raise RuntimeError("app bug")
        with self.assertRaises(RuntimeError):
            self.call(app=ShieldMiddleware(boom, make_shield()))

    def test_config_path_loading(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "runtime.toml"
            cfg.write_text('[firewall]\nmode = "monitor"\n', encoding="utf-8")
            mw = ShieldMiddleware(lambda e, s: (s("200 OK", []), [b"ok"])[1], config=str(cfg))
            self.assertEqual(mw.shield.config.mode, "monitor")


# --------------------------------------------------------------------------- ASGI
async def echo_app(scope, receive, send):
    if scope["type"] == "lifespan":
        message = await receive()
        await send({"type": "lifespan.startup.complete"})
        return
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok:" + body})


async def drive(app, scope, messages):
    queue, sent = list(messages), []

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent, queue


def http_scope(path="/", query=b"", method="GET", headers=(), client=("203.0.113.9", 5000)):
    return {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": query,
            "headers": list(headers), "client": client, "scheme": "http"}


class AsgiTests(unittest.TestCase):
    def setUp(self):
        self.app = ShieldASGI(echo_app, make_shield())

    def test_allowed_request_replays_a_multi_chunk_body(self):
        messages = [{"type": "http.request", "body": b"hel", "more_body": True}, {"type": "http.request", "body": b"lo", "more_body": False}]
        sent, _ = asyncio.run(drive(self.app, http_scope(method="POST", headers=[(b"content-length", b"5")]), messages))
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(sent[1]["body"], b"ok:hello")
        headers = {k: v for k, v in sent[0]["headers"]}
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertIn(b"x-request-id", headers)

    def test_attack_is_blocked(self):
        sent, _ = asyncio.run(drive(self.app, http_scope("/s", b"q=%3Cscript%3Ealert(1)%3C%2Fscript%3E"), [{"type": "http.request", "body": b""}]))
        self.assertEqual(sent[0]["status"], 403)
        self.assertEqual(json.loads(sent[1]["body"])["status"], 403)

    def test_websocket_handshake_is_checked_and_closed_with_1008(self):
        scope = {"type": "websocket", "path": "/ws", "raw_path": b"/ws", "query_string": b"x=<script>", "headers": [], "client": ("203.0.113.9", 1), "scheme": "ws"}
        sent, _ = asyncio.run(drive(self.app, scope, []))
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    def test_lifespan_passes_through(self):
        sent, _ = asyncio.run(drive(self.app, {"type": "lifespan"}, [{"type": "lifespan.startup"}]))
        self.assertEqual(sent, [{"type": "lifespan.startup.complete"}])

    def test_oversized_bodies_stop_buffering_early(self):
        small = ShieldASGI(echo_app, make_shield(firewall={"max_body_bytes": 10}))
        messages = [{"type": "http.request", "body": b"x" * 8, "more_body": True} for _ in range(50)]
        sent, remaining = asyncio.run(drive(small, http_scope(method="POST"), messages))
        self.assertEqual(sent[0]["status"], 413)
        self.assertGreater(len(remaining), 40)                          # did not drain the whole stream


# --------------------------------------------------------------------------- reverse proxy
class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.calls.append({"method": self.command, "path": self.path, "body": body,
                                  "headers": [(k, v) for k, v in self.headers.items()]})
        payload = json.dumps({"method": self.command, "path": self.path, "body": body.decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Set-Cookie", "a=1")
        self.send_header("Set-Cookie", "b=2")
        self.send_header("Keep-Alive", "timeout=5")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = do_POST = do_HEAD = do_PUT = _answer

    def log_message(self, *args):
        pass


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        cls.upstream.calls = []
        cls.upstream.daemon_threads = True
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.upstream_url = f"http://127.0.0.1:{cls.upstream.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def start_proxy(self, upstream=None, **tables):
        proxy = make_proxy(make_shield(**tables), upstream or self.upstream_url, ("127.0.0.1", 0), timeout=5)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (proxy.shutdown(), proxy.server_close()))
        return proxy.server_address[1]

    def request(self, port, method="GET", path="/", body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        result = (response.status, response.getheaders(), data)
        conn.close()
        return result

    def setUp(self):
        self.upstream.calls.clear()

    def test_normal_request_is_forwarded_and_response_hardened(self):
        port = self.start_proxy()
        status, headers, data = self.request(port, "GET", "/hello?x=1", headers={"Host": "public.example", "X-Forwarded-For": "6.6.6.6"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["path"], "/hello?x=1")
        names = [k.lower() for k, _ in headers]
        self.assertIn("x-content-type-options", names)
        self.assertIn("x-request-id", names)
        self.assertNotIn("keep-alive", names)                            # hop-by-hop headers stripped
        self.assertEqual(names.count("set-cookie"), 2)                   # duplicates relayed
        seen = dict(self.upstream.calls[0]["headers"])
        self.assertEqual(seen["X-Forwarded-For"], "127.0.0.1")           # spoofed value dropped
        self.assertEqual(seen["X-Forwarded-Host"], "public.example")
        self.assertTrue(seen["Host"].startswith("127.0.0.1:"))
        self.assertEqual(seen["Connection"], "close")

    def test_attacks_are_blocked_before_reaching_the_upstream(self):
        port = self.start_proxy()
        for path in ("/s?q=<script>alert(1)</script>", "/p?id=1+UNION+SELECT+a,b+FROM+t", "/.env", "/x/../../etc/passwd"):
            status, _, data = self.request(port, "GET", path.replace("<", "%3C").replace(">", "%3E"))
            self.assertEqual(status, 403, path)
            self.assertEqual(json.loads(data)["status"], 403)
        self.assertEqual(self.upstream.calls, [])

    def test_post_body_is_forwarded_intact(self):
        port = self.start_proxy()
        payload = json.dumps({"name": "Ünïcode ok", "n": 5}).encode()
        status, _, data = self.request(port, "POST", "/api", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(json.loads(data)["body"]), {"name": "Ünïcode ok", "n": 5})
        self.assertEqual(dict(self.upstream.calls[0]["headers"])["Content-Length"], str(len(payload)))

    def test_json_body_attack_is_blocked(self):
        port = self.start_proxy()
        status, _, _ = self.request(port, "POST", "/api", body=b'{"u":"a\' or 1=1 -- "}', headers={"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        self.assertEqual(self.upstream.calls, [])

    def test_oversized_body_returns_413(self):
        port = self.start_proxy(firewall={"max_body_bytes": 16})
        status, _, _ = self.request(port, "POST", "/up", body=b"x" * 64)
        self.assertEqual(status, 413)
        self.assertEqual(self.upstream.calls, [])

    def test_rate_limit_returns_429(self):
        port = self.start_proxy(ratelimit={"enabled": True, "requests_per_minute": 60, "burst": 2})
        statuses = [self.request(port, "GET", "/")[0] for _ in range(4)]
        self.assertEqual(statuses, [200, 200, 429, 429])

    def test_head_has_headers_but_no_body(self):
        port = self.start_proxy()
        status, headers, data = self.request(port, "HEAD", "/")
        self.assertEqual((status, data), (200, b""))

    def test_unsupported_methods_and_target_forms(self):
        port = self.start_proxy()
        self.assertEqual(self.request(port, "TRACE", "/")[0], 501)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:      # absolute-form: never proxied
            sock.sendall(b"GET http://evil.example/ HTTP/1.1\r\nHost: x\r\n\r\n")
            self.assertTrue(sock.recv(200).startswith(b"HTTP/1.1 400"))
        self.assertEqual(self.upstream.calls, [])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:      # '//host/..' must never reach the upstream as-is
            sock.sendall(b"GET //evil.example/x HTTP/1.1\r\nHost: x\r\n\r\n")
            self.assertTrue(sock.recv(200).startswith((b"HTTP/1.1 400", b"HTTP/1.1 200")))
        self.assertTrue(all(not call["path"].startswith("//") for call in self.upstream.calls))

    def test_chunked_request_bodies_are_refused(self):
        port = self.start_proxy()
        raw = b"POST /x HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(raw)
            self.assertTrue(sock.recv(200).startswith(b"HTTP/1.1 501"))

    def test_upstream_down_returns_502_without_leaking_details(self):
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        port_dead = dead.getsockname()[1]
        dead.close()
        port = self.start_proxy(upstream=f"http://127.0.0.1:{port_dead}")
        status, _, data = self.request(port, "GET", "/")
        self.assertEqual(status, 502)
        self.assertNotIn(b"Connection refused", data)

    def test_duplicate_request_headers_survive(self):
        port = self.start_proxy()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("GET", "/dup")
        conn.putheader("Cookie", "a=1")
        conn.putheader("Cookie", "b=2")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 200)
        cookies = [v for k, v in self.upstream.calls[0]["headers"] if k.lower() == "cookie"]
        self.assertEqual(cookies, ["a=1", "b=2"])

    def test_argument_parsers(self):
        self.assertEqual(parse_listen("127.0.0.1:8080"), ("127.0.0.1", 8080))
        self.assertEqual(parse_listen(":9000"), ("127.0.0.1", 9000))
        for bad in ("8080", "host:port", "h:70000"):
            with self.assertRaises(ValueError):
                parse_listen(bad)
        self.assertEqual(parse_upstream("https://api.example.com").port, 443)
        for bad in ("ftp://x", "http://", "http://u:p@h", "http://h/path", "http://h?x=1"):
            with self.assertRaises(ValueError):
                parse_upstream(bad)


if __name__ == "__main__":
    unittest.main()
