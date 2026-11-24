# Host Health and Recovery

Date: 2026-11-24
Implementation: `src/ogx/hosts/lifecycle.py`, per-adapter `health()`

## 1. What health means here

Health is the live session's readiness to serve reads. The implemented adapters
do not prepare for writes, and there is no daemon, so health is session-level,
not host-installation-level. A host marked healthy in one session does not imply
a persistent install is reachable across process restarts.

## 2. States relevant to health

- `CONNECTED`: adapter reported OK on the last health check; live session is
  serving.
- `DEGRADED`: live session exists but the last health check was not fully healthy
  (adapter reported an issue). Reads may still work depending on the error.
- `FAILED`: recovery (or a fatal issue) could not restore a usable session.
- `DISCONNECTING` and `DISCONNECTED`: no active health.
- `RECOVERING`: a bounded recovery attempt is in progress.

Health is only meaningful while connected or degraded. Calling `health()` on a
disconnected or closed session is not supported.

## 3. Adapter health contract

Every implemented adapter provides `health()`:

- **Local filesystem.** Checks that the resolved root is the same directory that
  was connected, that the root is still readable, and that the filesystem is
  accessible. On mismatch or I/O, returns `UNREACHABLE` with issues.
- **Local Git.** Runs a lightweight `git rev-parse --show-toplevel` (or
  equivalent) plus a read-only read to confirm the repo is still a usable local
  repo. On failure, returns `UNREACHABLE` with issues.

Adapter health returns `None` on the manager side means the adapter is healthy;
anything falsy with a non-empty `issues` list makes the session `DEGRADED`.

## 4. Recovery

Recovery is bounded and read-only:

1. Transition the session to `RECOVERING`.
2. Re-run adapter validation. If it fails, retry up to a bounded
   number of attempts (configurable, default 3; explicitly allowed 1-5).
3. On consecutive failure after the retry window, move the session to
   `FAILED`, emit a `failed` audit event, and stop.
4. On success, reconnect and return to `CONNECTED`.

Recovery never performs destructive repair, writes, branch operations, remote
sync, or data migration. It only revalidates and reconnects.

## 5. Persistence and restart reality

- Status in the process hosts table is coarse: `registered` set at creation and
  `disconnected` on disconnect. `connected` is **never** persisted.
- Sessions live only for the process lifetime. A restart drops them.
- After a restart, any host must be reconnected and revalidated; the previous
  health state is not restored from storage.

## 6. Failures are closed somewhere level

- Unknown or unclassified adapter failures become `FAILED` (not silently
  recovered).
- Health check failures make the session `DEGRADED`; they do not tear it down
  unless the adapter is unreachable and recovery exhausts retries.
- There is no automatic background polling; health is checked on demand via the
  manager and CLI.

## 7. Audit

The manager emits `host.health.checked` on every health call and
`host.recovery.*` events across the recovery flow. These records are hashed into
the platform operations audit log and carry session/host identifiers, not file
contents.

## 8. Limitations

- No persistent health state across restarts.
- No async health/recovery path.
- No remote/cloud/SSH/Docker/other remote adapter health.
- No write capability; no "repair by writing" is ever attempted.
