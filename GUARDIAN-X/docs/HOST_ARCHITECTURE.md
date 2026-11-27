# Host Architecture

## 1. Status statement

**No cloud, remote or write-capable host adapter is declared operational in
`0.8.0a1`.** The two local hosts are operational for their documented read-only
surface: connect, read, health, recover and CLI operations are implemented and
covered by dedicated suites. A shared host contract suite
(`tests/test_host_contract.py`, 12 tests) covers the registry plus both local
adapters' metadata/capability surface. This document records the real state,
not a roadmap claim.

| Host | Category | Status |
| --- | --- | --- |
| Local filesystem | local | OPERATIONAL (read-only) — lifecycle, adapter, CLI and tests implemented; write/exec/branch ops unsupported |
| Local Git repository | local | OPERATIONAL (read-only) — lifecycle, adapter, CLI and tests implemented; write/branch/remote ops unsupported |
| GitHub | git host | READ SURFACE OPERATIONAL (mocked) — lifecycle adapter over the canonical provider engine; metadata/file/tree/branch/commit/PR reads and one approval-gated draft-PR write. Branch/file/push/merge writes unsupported; **no live credential verified** |
| GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps, AWS CodeCommit | git host | PROVIDER VERIFIED (mocked/static); host contract suite exists at `tests/test_host_contract.py` (12 passed) and covers the registry/metadata contract |
| GitHub Codespaces, Gitpod, Docker, Kubernetes, remote SSH | cloud workspace | DESCRIPTOR_ONLY |
| GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps, CodeCommit host sessions | git host | UNSUPPORTED — provider adapters exist, but no lifecycle host adapter is registered, so `host connect` fails closed |
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
- In-process reconnection and bounded recovery are implemented and tested for
  the two local hosts. Live session state is process-local: it is not persisted
  across processes, and there is no automated cross-process failover.

## 5. Capability negotiation

A host capability may be reported only when the adapter implements and verifies
it. The capability vocabulary distinguishes `supported`, `partial`,
`requires_auth`, `requires_permission`, `requires_workspace`,
`not_configured`, `unavailable` and `failed`. An adapter that cannot answer must
report the limitation rather than returning an empty success.

The provider engine derives capability truth by introspection: a method that is
not overridden is not supported. See `docs/PROVIDER_ARCHITECTURE.md`.

## 6. Host session lifecycle

### 6a. Implemented lifecycle (November 2026)

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

Test evidence: lifecycle 13, filesystem 16 (3 skipped), Git 21, GitHub host 10 (mocked transport), CLI 6 — all pass.
Canonical suite 631 passed / 18 skipped; drop-in 289 tests, OK (9 skipped), executed 2026-11-27 on the implementation branch (base `71fcbbc`).

Truths that hold despite the `OPERATIONAL` labels:

- The local hosts are **read-only**. No local host adapter here supports writes,
  execution, push/branch operations, or remote/network operations. The GitHub
  host adapter performs network reads and one approval-gated draft-PR write;
  it does not implement branch creation, file updates, push or merge.
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

## 10. Provider-backed Git hosts (GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps, CodeCommit)

`ogx.hosts.adapters.provider_runtime.ProviderGitHostAdapter` wraps the canonical
provider adapter for each verified Git host. It adds no second HTTP stack, no
second credential store and no new write path.

| Host type | Provider adapter | Identifier | Credential env |
| --- | --- | --- | --- |
| `gitlab` | `GitLabAdapter` | `owner/repository` (subgroups allowed) | `OGX_GITLAB_TOKEN` |
| `bitbucket` | `BitbucketAdapter` | `workspace/repository` | `OGX_BITBUCKET_TOKEN` |
| `gitea` | `GiteaAdapter` | `owner/repository` | `OGX_GITEA_TOKEN` |
| `forgejo` | `ForgejoAdapter` | `owner/repository` | `OGX_FORGEJO_TOKEN` |
| `azure_devops` | `AzureDevOpsAdapter` | `project/repository` | `OGX_AZURE_DEVOPS_TOKEN` (+ `OGX_AZURE_DEVOPS_ORG`) |
| `aws_codecommit` | `CodeCommitAdapter` | repository name | AWS credential env vars |

Shared behaviour:

* `validate()` checks the identifier and the API base without network I/O;
* `connect()` requires a credential and a successful remote health probe, so a
  session is never reported connected after a failed probe;
* reads (metadata, bounded file read, tree, branches, commits, pull requests)
  delegate to the provider adapter;
* `capabilities()` is derived from the adapter's own introspection: a missing
  operation is reported `unsupported`, never emulated;
* the only write is approval-gated pull/merge-request creation through
  `plan_pull_request()` + `execute_pull_request()`, which writes an audit record
  and refuses `approved=False`.

```bash
ogx host register --type gitlab --provider gitlab --name upstream \
  --identifier group/project --json
ogx host connect <host-id>     # requires OGX_GITLAB_TOKEN
```

Branch creation, file writes, commit creation, push, merge, deployment and
arbitrary execution are **not** implemented for these hosts and are reported as
`unsupported`. All six adapters are covered by mocked transport tests; no live
provider call has been verified from this checkout.
