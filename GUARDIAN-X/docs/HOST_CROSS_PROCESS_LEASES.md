# Host session leases: cross-process ownership, fencing and recovery (HOST-13)

Status: **TESTED** locally on Windows (CPython 3.15.2) with real OS processes.
Live multi-machine verification remains **BLOCKED** (no second machine and no
shared state directory available).

## Problem

Host sessions used to live only in one process's memory. Two CLI invocations,
a CLI plus a worker, or two workers could therefore act on the same host at the
same time with no shared view of who owned it, and a crashed process left no
record that its session had existed. `HostSessionManager` guarded threads with
an `RLock`; nothing guarded processes.

## Design

`ogx.hosts.leases.HostLeaseStore` keeps ownership in
`OGX_HOME/hosts/leases.db` (SQLite, WAL, `BEGIN IMMEDIATE`, 10 s busy timeout):

| Table | Purpose |
| --- | --- |
| `host_session_leases` | one row per leased host: `lease_id`, `owner`, `fencing_token`, `acquired_at`, `expires_at` |
| `host_fencing_tokens` | append-only-high-water mark of the highest token ever issued per host |
| `lease_schema` | schema version (`1`); a mismatch refuses to open the store |

`owner` is `hostname:pid` (`default_owner()`), so a lease can be attributed to a
process and, on the same machine, to a live PID.

### Operations

- `acquire(host, ttl, owner)` — takes the lease, or raises `LeaseHeldError`
  carrying the current holder. Every successful acquisition increments the
  host's fencing token; tokens are never reused.
- `renew(host, lease_id, token, ttl)` — extends an unexpired lease for the
  exact holder. Wrong lease id or token → `LeaseLostError`. Expired →
  `LeaseLostError`. No row → `LeaseNotFoundError`.
- `release(host, lease_id, token)` — idempotent; returns `False` when the caller
  no longer owns the lease (for example after being fenced out).
- `validate(host, lease_id, token)` — true only for the current, unexpired
  holder.
- `inspect(host)` / `current_fencing_token(host)` / `sweep_expired()`.

### Integration

`HostSessionManager` accepts `lease_store`, `lease_ttl_seconds` (default 120 s,
clamped to 1 h) and `lease_owner`:

| Moment | Behaviour |
| --- | --- |
| `create_session` | acquires the lease before the session becomes usable; on registration failure the lease is released |
| `connect` | validates the lease **before** the idempotent "already connected" return, then renews after a successful connect |
| `health` | validates, then renews |
| `recover` | re-acquires (steals only when recoverable) or renews, auditing `host.session.lease_recovered` |
| `disconnect` / `shutdown` / `dispose` | releases the lease |
| any lease violation | `LeaseLostError`, audit `host.session.lease_lost`, fail closed |

The process-wide manager obtained from `get_session_manager()` always uses the
durable store and registers an `atexit` hook that releases this process's
leases, so a short-lived CLI command does not leave a lease behind.

Sessions created by `HostSessionManager()` directly (tests, embedding) keep the
previous in-memory behaviour until a `lease_store` is supplied.

## Recovery semantics

| Failure | Result |
| --- | --- |
| Owner process crashes | Its PID is gone, so `recoverable()` is true immediately on the same machine: the next `acquire`/`recover` steals the lease with a higher fencing token. No TTL wait. |
| Owner machine loses power | The lease expires after the TTL, then is stolen with a higher token. |
| Owner is on another machine and alive | Not stealable; only TTL expiry recovers it. |
| Owner still alive but partitioned | Same as above; the old holder is fenced out the moment its lease is stolen, and every manager operation re-validates. |
| Network partition between two processes | Filesystem locks decide. If SQLite cannot lock, the operation raises instead of proceeding. |
| Duplicate concurrent acquisition | `BEGIN IMMEDIATE` + primary key make exactly one process win; the rest get `LeaseHeldError`. Verified with 6 concurrent OS processes. |
| Credential revocation | Not modelled here; adapters re-validate credentials on `connect`/`health`. A revoked credential fails the adapter call and moves the session to `FAILED`; the lease is unaffected. |

Windows process liveness uses `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` +
`GetExitCodeProcess` compared against `STILL_ACTIVE`. It must **not** use
`os.kill(pid, 0)`, which on Windows calls `TerminateProcess` and would kill the
process being inspected.

## Limitations

1. **Not a security boundary.** A hostile local user who can write the state
   directory can edit the lease table. Leases coordinate cooperating processes,
   not attackers. Adapter-level checks (approval gates, allowlists, TLS,
   credential handling) remain the security controls.
2. **Wall clock.** Expiry uses the local clock. A forward jump can expire a live
   lease early; a backward jump can extend one. Mitigation: keep TTLs short and
   rely on `renew` for long operations.
3. **Shared filesystem assumptions.** Two machines sharing one state directory
   depend entirely on the filesystem's locking. SMB/NFS mounts without reliable
   locking are unsupported; use one state directory per machine.
4. **PID reuse.** A dead owner whose PID was reused by an unrelated live process
   is treated as alive; recovery then waits for TTL expiry. Fencing still
   prevents the stale holder from acting.
5. **No distributed consensus.** There is no quorum, lease renewal service or
   clock synchronisation. Multi-machine coordination needs an external
   coordinator and is out of scope for this repository.
6. **Windows handle caveat.** A terminated process can still own open handles,
   which is why liveness reads the exit code instead of the handle.

## Evidence

| Check | Result |
| --- | --- |
| `python -m pytest tests/test_host_leases.py -q` | **20 passed, 1 skipped** (symlink creation unavailable) |
| `python -m pytest tests -q` | **904 passed, 19 skipped, 119 subtests passed**, exit 0 |
| 6 concurrent OS processes acquiring one host | exactly one `won 1`, five `held`; fencing token stays `1` |
| Child process killed mid-lease (ttl 2 s) | lease recovered with fencing token `2` |
| Dead local PID with a 600 s lease | recovered immediately, token `2` |
| Three sequential `ogx host connect` CLI processes | exit 0 each; final fencing token `3`; no lease left behind |

Not verified: two physical machines, a shared network state directory, and
lease behaviour under real network partitions.
