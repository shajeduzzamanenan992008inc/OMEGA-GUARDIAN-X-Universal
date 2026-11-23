# Runtime guard

Defence in depth for the running application: request hygiene -> client policy -> rate limit -> access control -> attack signatures.
Framework-neutral engine (`ogx.runtime.engine.Shield`) + adapters (WSGI, ASGI, reverse proxy). Standard library only.

> **Honest scope.** This is a lightweight in-app / in-front layer for small and medium projects. It is **not** a replacement for a managed
> WAF, DDoS protection or TLS termination. Signatures are heuristic regular expressions: expect some false positives and misses.

## Using it
```python
import runpy
shield = runpy.run_path("omega-shield/init.py")["load_runtime"]()        # no permanent sys.path change
app = shield.ShieldMiddleware(app)                                       # WSGI   (Flask: app.wsgi_app = ...)
app = shield.ShieldASGI(app)                                             # ASGI   (wrap the outermost app)
```
Any language: `python omega-shield/init.py proxy --upstream http://127.0.0.1:3000 [--listen 127.0.0.1:8080]`.
Configuration: `guard/runtime/runtime.toml` (validated; bad values fail loudly). Check it with `init.py guard check`;
try a request with `init.py guard test "/s?q=<script>" [--method POST --header "Content-Type: application/json" --body '{...}']`
(exit code 0 = allowed, 1 = blocked).

## Recommended rollout
1. `mode = "monitor"`: nothing is blocked, events are logged (`init.py logs --level WARNING`).
2. Tune: `disabled_rules`, `skip_signature_paths`, `max_body_bytes`, rate limits, `trusted_proxies`.
3. `mode = "enforce"`.

## Decision order
Hygiene -> client policy (IP lists, scanner user agents) -> rate limit -> access control -> signatures. In `enforce` mode the first blocking
rule wins; in `monitor` mode every layer runs and events record `would_block`.

## Rule catalogue
Protocol, policy and access rules:

| Rule | Name | Status | When |
|---|---|---|---|
| OGX-RT-0010 / 0011 | ip-blocked / ip-not-allowed | 403 | `block_ips` / `allow_ips` |
| OGX-RT-0020 | rate-limited | 429 + `Retry-After` | token bucket per client IP (and per route) |
| OGX-RT-0030 | credentials-missing / invalid | 401 + `WWW-Authenticate` | access rule requires an API key |
| OGX-RT-0031 / 0032 | role-denied / route-ip-denied | 403 | role or per-route IP allow list not satisfied |
| OGX-RT-0100 | method-not-allowed | 405 + `Allow` | method outside `allowed_methods` (TRACE/CONNECT are never allowed by default) |
| OGX-RT-0110 | url-too-long | 414 | path + query longer than `max_url_length` |
| OGX-RT-0120 | headers-too-large | 431 | too many headers / header bytes |
| OGX-RT-0130 | body-too-large | 413 | declared or actual body over `max_body_bytes` |
| OGX-RT-0140 | invalid-content-length / request-smuggling-shape | 400 | malformed or conflicting `Content-Length`, or `Content-Length` together with `Transfer-Encoding` |
| OGX-RT-0150 | host-not-allowed | 400 | `Host` not in `allowed_hosts` |
| OGX-RT-0160 | control-characters / null-byte | 400 | NUL, CR/LF or other control characters in request line/headers |
| OGX-RT-0170 | malformed-percent-encoding | 400 in `strict`, log-only otherwise | invalid `%xx` sequences |
| OGX-RT-1090 | scanner-user-agent | 403 | `block_user_agents` substring |

Attack signatures (generated from the code; "log" rules never block in the balanced profile):

| Rule | Name | Severity | Looks at | Balanced | Strict |
|---|---|---|---|---|---|
| OGX-RT-1001 | path-traversal | high | path, query | block | block (path, query) |
| OGX-RT-1002 | sensitive-file-probe | high | path, query | block | block (path, query) |
| OGX-RT-1010 | sql-injection | high | path, query, headers, body | block | block (path, query, headers, body) |
| OGX-RT-1020 | cross-site-scripting | high | path, query, headers | block | block (path, query, headers, body) |
| OGX-RT-1030 | command-injection | high | path, query, headers | block | block (path, query, headers) |
| OGX-RT-1050 | ssrf-probe | medium | query, body | log | block (query, body) |
| OGX-RT-1060 | jndi-or-template-injection | critical | path, query, allheaders, body | block | block (path, query, allheaders, body) |
| OGX-RT-1065 | shellshock | critical | allheaders | block | block (allheaders) |
| OGX-RT-1070 | dangerous-wrapper | high | path, query | block | block (path, query) |
| OGX-RT-1080 | xml-external-entity | high | body | block | block (body) |

Access-rule matching decodes up to three percent-encoding rounds, converts `\` to `/`, resolves `.`/`..` segments, collapses `//`, and compares
case-insensitively, so `/ADMIN`, `//admin//x`, `/%2561dmin` and `/pub/../admin` all hit the `/admin` rule.

## Reverse proxy (experimental)
HTTP/1.1 origin-form requests, Content-Length bodies up to `max_body_bytes`, one upstream (http or https with certificate verification),
connection-per-request, bounded concurrency (256) and per-recv socket timeouts. Removes hop-by-hop headers, replaces any client-supplied
`X-Forwarded-*` (unless the peer is a configured trusted proxy), adds `X-Request-ID`, relays duplicate headers (e.g. `Set-Cookie`), adds the
missing security headers. Default bind is `127.0.0.1`; binding elsewhere prints a warning: make sure the upstream is not reachable
directly, otherwise the guard can be bypassed.

## Limitations
* Signatures are heuristic; obfuscation beyond three decode rounds, split payloads and logic flaws are not detected. No response inspection.
* Only the first `inspect_body_bytes` of text/JSON/form/XML bodies are inspected; **multipart and binary bodies are not inspected**.
* The rate limiter is in memory per process: with N worker processes the effective limit is up to N times higher, and it resets on restart.
* IP policy depends on a correct `trusted_proxies`; otherwise `X-Forwarded-For` is (rightly) ignored.
* Access control is coarse API-key/role gating, not an identity system; keys are bearer secrets, so use TLS in front.
* The proxy has no TLS termination, WebSocket/Upgrade, chunked request bodies or keep-alive.
* Verified on Linux with Python 3.12; not yet exercised on Windows or Python 3.11 here.
