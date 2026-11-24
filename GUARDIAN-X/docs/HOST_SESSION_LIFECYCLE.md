# Host Session Lifecycle

Date: 2026-11-24
Implementation: `src/ogx/hosts/lifecycle.py`

## 1. Contract

`HostAdapter` is a synchronous ABC with `validate`, `connect`, `disconnect`,
`health` and `capabilities`. Optional reads (`read_file`, `list_directory`,
`metadata`) raise `UnsupportedCapabilityError` by default, so an adapter cannot
silently inherit an operation it does not implement.

`HostSession` carries a stable `session_id`, `host_id`, `host_type`, provider,
state, timestamps, last health, capability snapshot, last error, session
configuration and an `audit_correlation` id. It is the only object the manager
hands out; adapters stay private.

## 2. States and transitions

```
UNREGISTERED -> REGISTERED -> CONFIGURED -> VALIDATING -> CONNECTING -> CONNECTED
                                      |                        |
                                      +---- FAILED <-----------+
CONNECTED <-> DEGRADED
CONNECTED/DEGRADED/FAILED -> DISCONNECTING -> DISCONNECTED
FAILED/DEGRADED/DISCONNECTED -> RECOVERING -> CONNECTED (or DEGRADED) / FAILED
any non-CLOSED -> CLOSED
```

Every change goes through `ALLOWED_TRANSITIONS`; anything else raises
`InvalidTransitionError`. `CONNECTED` cannot be reached from `REGISTERED` or
`CONFIGURED` without adapter `validate()` and `connect()` succeeding.

## 3. Manager semantics

- One live session per host ID (`DuplicateSessionError` otherwise). A terminal
  session may be disposed and replaced.
- Operations hold a re-entrant lock; sessions are not shared across threads
  without the manager.
- `connect` is idempotent while connected; `disconnect` is idempotent while
  disconnected. `shutdown` closes every session and returns per-session errors.
- `health` is only valid while connected/degraded. Non-healthy results move the
  session to `DEGRADED`; they never report `CONNECTED`.
- `recover` is bounded to 1–5 attempts, optional exponential backoff, and fails
  closed to `FAILED`. It never performs destructive repair.
- Session operations are process-local. `reset_session_manager()` drops the
  cached manager for tests and teardown.

## 4. Persistence

Only coarse status is written to the `hosts` table via
`HostRegistry.update_host_status`: `registered` at session creation and
`disconnected` on disconnect. **`connected` is never persisted.** A process
restart yields no sessions; any host must be reconnected and revalidated. This
avoids stale "connected" records after a crash.

## 5. Capability truth

`manager.effective_capabilities(session_id)` returns adapter-reported values
only for `CONNECTED`/`DEGRADED`; otherwise every declared capability is
`unavailable`, with the lifecycle state included. `HostRegistry`'s static
capability methods are declarations, not effective state; the CLI marks them as
such.

## 6. Audit

When constructed with `audit_sink`, the manager emits
`host.session.created`, `connected`, `disconnected`, `failed`, `recovered`,
`closed` and `host.health.checked`. The process-wide manager wires this to the
hash-chained platform audit log. Events contain identifiers, type and state, not
file contents.

## 7. Known limitations

- No session persistence or daemon; CLI sessions end with the process.
- No async API; no remote/cloud/SSH/Docker hosts.
- No mutating operations; write/exec/push capabilities are unsupported.
- Audit records are tamper-evident, not immutable.
