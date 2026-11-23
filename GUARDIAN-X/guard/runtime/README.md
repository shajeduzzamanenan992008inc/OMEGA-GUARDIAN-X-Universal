# guard/runtime - request guard

Defence-in-depth for the *running* application: request hygiene, attack signatures, rate limiting, IP policy and
per-route API-key access control. Stdlib only. Code lives in `core/ogx/runtime/`; **this folder holds your configuration**.

| Where it runs | How |
|---|---|
| Python WSGI app (Flask, Django, Bottle, ...) | `shield.ShieldMiddleware(app)` |
| Python ASGI app (FastAPI, Starlette, ...) | `shield.ShieldASGI(app)` |
| Any language (Node, PHP, Go, Java, ...) | `python omega-shield/init.py proxy --upstream http://127.0.0.1:3000` (experimental) |

Load the runtime from your app without touching `sys.path` permanently:

```python
import runpy
shield = runpy.run_path("omega-shield/init.py")["load_runtime"]()
```

Read `docs/RUNTIME_GUARD.md` for the rule catalogue, tuning workflow and the honest limitations
(this is not a replacement for a managed WAF; multipart bodies are not inspected).
