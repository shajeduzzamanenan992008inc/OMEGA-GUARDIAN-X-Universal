# Observability

Stdlib-only logging, error tracking and debug utilities in `ogx.runtime.observe`. Data lives in the per-host state folder.

| Feature | API | CLI |
|---|---|---|
| Structured JSON logs, rotating, redacted, correlation context | `configure()`, `get_logger()`, `log_context()` | `init.py logs [--lines N] [--level L] [--json]` |
| Error tracking: fingerprint, de-duplicate, redact, bounded retention | `ErrorTracker`, `ErrorTrackingMiddleware`, `ErrorTrackingASGI`, `install_excepthooks()` | `init.py errors list \| show FP \| clear --confirm` |
| Debug utilities | `snapshot()`, `Timer`, `timed`, `dump_threads()`, `enable_faulthandler()` | `init.py snapshot` |

Design rules: only the `omega` logger namespace is configured (root logger untouched unless `capture_root=True`); log files are
`0600` in a `0700` folder; secrets are redacted (tokens, passwords, bearer/authorization, JWTs, AWS key ids, private keys,
credentials in URLs); the guard logs rule ids, client IP, method, path and query *length* - never bodies, headers or query strings;
`ErrorTracker.capture()` never raises; middleware re-raises the original exception unchanged.
Limits: local, single-host storage; no remote shipping, alerting or sampling.
