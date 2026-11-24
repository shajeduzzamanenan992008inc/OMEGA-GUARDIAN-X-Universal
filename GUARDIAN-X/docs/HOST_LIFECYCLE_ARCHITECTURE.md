# Host Lifecycle Architecture (Phase 1)

Date: 2026-11-24
Status: implemented in this pass; describes actual code.

## 1. What existed before this pass

- `hosts/models.py` — `HostType`, `ProviderType`, `HostStatus`, `HostCapability`,
  `HostRecord` (dataclass only).
- `hosts/interfaces.py` — async `RepositoryProvider`/`WorkspaceProvider`/
  `HostProvider` ABCs and result dataclasses. No lifecycle states, no sessions.
- `hosts/capabilities.py` — a static matrix. The local entries claimed
  `file_write`, `branch_create`, `workspace_exec` and `testing` as supported,
  none of which any adapter implements.
- `hosts/registry.py` — persistence over Registry V2 (`hosts`,
  `project_hosts`, `repository_hosts`, `workspace_hosts`), with status updates
  but no connection concept.
- `hosts/adapters/local_filesystem.py` and `local_git.py` — discovery wrappers
  with no connect/read/health methods; `local_git.py` defined several methods
  two or three times.
- CLI `ogx host` — list, show, status, capabilities, register, discover only.

A registered host was therefore indistinguishable from an operational one, and
`register_host` wrote `status = "active"` immediately.

## 2. Gaps addressed

1. No typed lifecycle, no state validation, no session identity.
2. No real filesystem read boundary; no path containment or symlink policy.
3. No operational local Git reads (branch, HEAD, log, tree, file).
4. No health, recovery, shutdown or dispose semantics.
5. Capability reports were declarations, not effective state.
6. Persisting `active` at registration overstated operational readiness.

## 3. Modules

| Module | Responsibility |
| --- | --- |
| `hosts/lifecycle.py` | states, transitions, `HostSession`, `HostSessionManager`, adapter ABC |
| `hosts/adapters/filesystem_runtime.py` | scoped read-only local filesystem adapter |
| `hosts/adapters/git_runtime.py` | scoped read-only local Git adapter |
| `hosts/adapters/runtime_factory.py` | host-type → adapter construction |
| `hosts/registry.py` | persistence only; no live session state |
| `hosts/capabilities.py` | truthful declared matrix (read-only local entries) |

## 4. Lifecycle states

`UNREGISTERED → REGISTERED → CONFIGURED → VALIDATING → CONNECTING → CONNECTED`
with `DEGRADED`, `DISCONNECTING`, `DISCONNECTED`, `RECOVERING`, `FAILED`,
`CLOSED`. Transitions are table-driven; invalid transitions raise
`InvalidTransitionError`. A session is created in `CONFIGURED` only after its
adapter is constructed and configuration is accepted; `CONNECTED` is reachable
only through adapter `validate()` then `connect()`.

## 5. Ownership and concurrency

- `HostSessionManager` owns sessions in-process, guarded by a re-entrant lock.
- One live session per host ID; terminal sessions may be disposed and replaced.
- Adapters own their connection resources and are never shared between
  sessions.
- Lifecycle operations are synchronous. This keeps the CLI, tests and adapters
  deterministic and avoids untested async edge cases.
- `shutdown()` disconnects every live session; `dispose()` removes a terminal
  session. Both are idempotent where safe.

## 6. Persistence boundary

Only coarse host status is persisted (`registered`, `disconnected`, `failed`)
through `HostRegistry.update_host_status`. **`connected` is never persisted**:
after a process restart there is no live session to justify it. Session state is
ephemeral, so a restart produces an empty manager and a persisted host that must
be reconnected and revalidated. This is deliberate; see
`HOST_HEALTH_AND_RECOVERY.md`.

## 7. Capability enforcement

`adapter.capabilities()` reports what the adapter can actually do for a
connected session. `manager.effective_capabilities()` maps every declared
capability to `unavailable` when the session is not connected, and adds the
lifecycle state. Operations not implemented (writes, exec, push, merge, branch
delete) are `unsupported` and raise before touching the host.

## 8. Error model

- `HostLifecycleError` — base, carries `code` and a redacted message.
- `InvalidTransitionError`, `SessionNotFoundError`, `DuplicateSessionError`,
  `UnsupportedHostTypeError`, `HostPathError`, `HostPermissionError`,
  `HostReadError`, `GitHostError`, `GitUnavailableError`.
- Adapter exceptions are wrapped with context; the original message is not
  discarded but is redacted for secrets.
- Failure paths set `FAILED` and `last_error`; they are never reported as
  connected.

## 9. Security boundaries

Filesystem: allowed roots, canonical containment via `resolve()` +
`os.path.commonpath`, traversal rejection, symlink-escape rejection, size and
entry limits, no writes, no execution.

Git: local repository only, `subprocess` argument arrays with `shell=False`,
bounded timeout, no remote/write/network commands, repository path validation,
blob size check before content read.

## 10. Adapter responsibilities

An adapter implements `validate`, `connect`, `disconnect`, `health`,
`capabilities` and the read operations it advertises. It must fail closed on
paths outside its root, must not silently claim unsupported operations, and must
be safe to disconnect repeatedly.
