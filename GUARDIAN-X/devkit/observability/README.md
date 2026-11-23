# devkit/observability - logs, error tracking, debug utilities

Code lives in `core/ogx/runtime/observe/` (stdlib only). Data goes to the per-host state folder **outside** your project
(`python omega-shield/init.py status` prints it), so nothing here can end up in git by accident.

```python
import runpy
shield = runpy.run_path("omega-shield/init.py")["load_runtime"]()
obs = shield.observe

obs.configure(level="INFO")                       # rotating JSON-lines log; root logger untouched
log = obs.get_logger("billing")
with obs.log_context(request_id="r-1"):           # correlation fields on every record inside the block
    log.info("charge created", extra={"fields": {"amount": 12}})

tracker = obs.ErrorTracker()                      # de-duplicated, redacted, bounded (SQLite)
app = obs.ErrorTrackingMiddleware(app, tracker)   # WSGI; ErrorTrackingASGI for ASGI
obs.install_excepthooks(tracker)                  # optional: uncaught exceptions in any thread
```

CLI: `init.py logs`, `init.py errors list|show <fingerprint>|clear --confirm`, `init.py snapshot`.
Secrets (tokens, passwords, bearer headers, JWTs, private keys, credentials in URLs) are redacted before anything is stored.
`python omega-shield/devkit/observability/demo.py` runs a self-contained demo.
