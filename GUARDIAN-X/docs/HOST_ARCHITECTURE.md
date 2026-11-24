# Host Architecture

## 1. Status statement

**No host adapter is declared operational in `0.8.0a1`.** A shared host
contract suite now exists (`tests/test_host_contract.py`, 12 tests) and passes
for the registry plus the local adapters' metadata/capability surface. It does
not yet cover connect/read/disconnect/health/recovery, because the adapters do
not implement those operations. This document records the real state, not a
roadmap claim.

| Host | Category | Status |
| --- | --- | --- |
| Local filesystem | local | OPERATIONAL (read-only) — lifecycle, adapter, CLI and tests implemented; write/exec/branch ops unsupported |
| Local Git repository | local | OPERATIONAL (read-only) — lifecycle, adapter, CLI and tests implemented; write/branch/remote ops unsupported |
| GitHub | git host | UNVERIFIED shim over the canonical provider engine |
| GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps, AWS CodeCommit | git host | PROVIDER VERIFIED (mocked/static); no host contract suite |
| GitHub Codespaces, Gitpod, Docker, Kubernetes, remote SSH | cloud workspace | DESCRIPTOR_ONLY |
| SourceHut | git host | DESCRIPTOR_ONLY |

`ogx host list` prints `No hosts registered.` on a fresh state directory.

## 2. Layers

```text
ogx host / remote / repository / workspace   (CLI, compatibility commands)
            │
ogx.hosts                                     (compatibility facade)
  ├── interfaces.py   HostProvider, RepositoryProvider, WorkspaceProvider ABCs
  ├── models.py       HostRecord, capabilities, status values
  ├── capabilities.py capability vocabulary and negotiation helpers
  ├── registry.py     host registration and lookup (Registry V2 backed)
  └── adapters/       local_filesystem, local_git, github (shim)
            │
ogx.platform                                  (canonical provider engine)
  └── adapters/       github, gitlab, bitbucket, gitea/forgejo,
                      azure_devops, aws_codecommit, sourcehut (descriptor)
```

`ogx.hosts` is deliberately thin: it re-exports canonical adapters rather than
duplicating them. `ogx.hosts.adapters.github` resolves to the platform GitHub
adapter, not a second implementation.

## 3. Interfaces

`HostProvider` (sync/async ABC in `ogx/hosts/interfaces.py`):

- `host_type`, `provider_type`
- `identify_host(host_identifier)`
- `inspect_host(host_id)`
- `get_capabilities(host_id)`
- `check_health(host_id)`
- `list_resources(host_id, resource_type)`
- `get_provider_info()`

`RepositoryProvider` covers discovery, inspection, branch/commit/file/tree
listing, pull-request listing, metadata, access checks and provider info.
`WorkspaceProvider` covers workspace discovery, inspection, access checks,
capabilities and provider info. Full signatures are in
`src/ogx/hosts/interfaces.py`; the canonical provider contract is documented in
`docs/CORE_CONTRACTS.md`.

## 4. Identity, persistence and switching

- Host records live in Registry V2 (`OGX_HOME/registry/registry.db`), not in the
  analyzed project.
- A host record is keyed by `host_id`; the project it serves is linked through
  the project registry, never by path string.
- Because project identity is path-independent (`docs/PROJECT_IDENTITY.md`), a
  host switch does not change `project_id` or `identity_hash`.
- Host state persistence, reconnection and failure recovery are **not**
  end-to-end verified. There is no automated failover.

## 5. Capability negotiation

A host capability may be reported only when the adapter implements and verifies
it. The capability vocabulary distinguishes `supported`, `partial`,
`requires_auth`, `requires_permission`, `requires_workspace`,
`not_configured`, `unavailable` and `failed`. An adapter that cannot answer must
report the limitation rather than returning an empty success.

The provider engine derives capability truth by introspection: a method that is
not overridden is not supported. See `docs/PROVIDER_ARCHITECTURE.md`.

## 6. Security boundaries

### 6a. Host session lifecycle (November 2026)

This milestone adds a real, synchronous, process-local host session lifecycle:

- **Manager** (`src/ogx/hosts/lifecycle.py`): `HostSessionManager` with 12 states,
  validated transitions, one live session per host ID, locked operations, bounded
  health/recovery, shutdown/disposal, list and `effective_capabilities`.
- **Persistence**: only coarse status (`registered` / `disconnected`) is written;
  `connected` is never persisted. Sessions are process-local; a restart drops them.
- **Audit**: session/health/recovery events flow to the platform operations audit
  log via a configured sink (default: `platform.operations.append_audit`).
- **CLI** (`src/ogx/cli.py`): `host connect/disconnect/health/recover/sessions/inspect`
  plus enhanced `host status` with the live state; registration requires `--identifier`
  for local hosts.

Operational local hosts:

- `src/ogx/hosts/adapters/filesystem_runtime.py` — scoped read-only local-filesystem
  host (list, read, metadata, discover) with strict containment, symlink-escape
  rejection and bounded reads/listings.
- `src/ogx/hosts/adapters/git_runtime.py` — read-only local-Git host (metadata,
  exists, file, tree, branches, tags, commits, history) using safe
  `subprocess.run` with a bounded timeout and no remote/write commands.
- `src/ogx/hosts/adapters/runtime_factory.py` — adapter factory for
  `local_filesystem` and `local_git`.

Test evidence: lifecycle 13, filesystem 16 (3 skipped), Git 21, CLI 6 — all pass.
Canonical suite 465 passed; drop-in suite 245 passed.

Truths that hold despite the `OPERATIONAL` labels:

- These local hosts are **read-only**. No host adapter here supports writes,
  execution, push/branch operations, or remote/network operations.
- `effective_capabilities()` returns the adapter's reported values only while
  connected/degraded; otherwise every declared capability is `unavailable`.
- Registration does **not** mark a host `active`; new registrations start at
  `registered`.

See `docs/HOST_SESSION_LIFECYCLE.md`, `docs/LOCAL_FILESYSTEM_HOST.md`,
`docs/LOCAL_GIT_HOST.md`, `docs/HOST_HEALTH_AND_RECOVERY.md`.

## 7. Security boundaries

- Host access is read-only by default; remote writes require a validated plan,
  a capability check, explicit `approved=True` and an audit event.
- Credentials are read per provider from its own environment variables and are
  never loaded from the analyzed repository, written to the registry, or reused
  across providers.
- API bases pass endpoint validation (HTTPS, no userinfo, no loopback/private/
  metadata hosts unless explicitly opted in); pagination and redirects stay on
  the configured host.
- No host adapter may expose the host Docker socket or the user home directory
  to analyzed code.

## 7. Capability negotiation
## 8. Contract suite required before an operational claim

Each host adapter must pass the same suite:

1. identity and provider type
2. discovery of at least one resource
3. connection/open and close
4. health reporting (healthy and failure paths)
5. read of a known file
6. list of a known collection
7. metadata shape
8. capability negotiation (supported and unsupported)
9. permission-denied handling
10. network/transport failure handling
11. disconnect and recovery
12. no credential leakage in errors or logs

Until that suite exists and passes, host support must be reported as
PARTIALLY_IMPLEMENTED, OPERATIONAL with documented limits, or UNVERIFIED — never
as complete if it overstates the evidence.

## 9. Adding a host

1. Implement `HostProvider` (and `RepositoryProvider`/`WorkspaceProvider` as
   applicable) in `ogx/hosts/adapters/` or delegate to a verified platform
   adapter.
2. Reuse `ogx.platform.transport` and `ogx.platform.security`; do not add a new
   HTTP stack.
3. Register the adapter without importing vendor SDKs into orchestration code.
4. Add mocked contract tests for every required behaviour above, including
   failure paths.
5. Update this table and `docs/CAPABILITY_MATRIX.md` with the verified status.
