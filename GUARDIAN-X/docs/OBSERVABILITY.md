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

## API process observability (ENT-09)

`ogx.api.observability` adds an in-process metrics registry for the authenticated
API. It is deliberately bounded: route labels collapse identifiers
(`/api/v1/hosts/{id}`), error counters are keyed only by the public error code,
and no tenant id, subject, token, header or body is ever recorded.

| Surface | Behaviour |
| --- | --- |
| `GET /health` | liveness only |
| `GET /ready` | per-dependency checks: `database` (SELECT 1) and `schema` (required tables), 200 when all pass, 503 otherwise with the missing tables listed |
| `GET /api/v1/metrics` | JSON snapshot: totals, status codes, `error_codes`, per-route count/error/duration stats, in-flight gauge, uptime |
| `GET /api/v1/metrics?format=prometheus` | Prometheus text rendering of the same data |

Every request is timed in the HTTP handler and carries a correlation id in the
response header, the error envelope and the access log line. Error categories
are the API's own stable codes (`invalid_token`, `forbidden`, `not_found`,
`rate_limited`, ...), never raw exception text.

Limits (unchanged and honest): metrics are **per process and in memory**, lost on
restart; there is no persistence, no OTLP/tracing export, no alert delivery and
no retention configuration. A pluggable metrics storage interface is documented
as future work, not implemented.
