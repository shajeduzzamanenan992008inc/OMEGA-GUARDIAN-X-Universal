# Core Contracts

Version: `0.8.0a1` (alpha). These contracts are implemented and covered by
tests, but they are **not** frozen: no semantic-version stability is promised
before 1.0. Anything described as "not implemented" below must not be treated
as available.

## Contract map

| Contract | Implementation | Consumer |
| --- | --- | --- |
| Provider | `ogx.platform.base.ProviderAdapter` | `ogx.platform.registry`, CLI, `ogx.hosts` facade |
| Host | `ogx.hosts.interfaces.HostProvider` | host registry, `ogx host` CLI |
| Repository source | `ogx.hosts.interfaces.RepositoryProvider` | host adapters, compatibility layer |
| Workspace source | `ogx.hosts.interfaces.WorkspaceProvider` | workspace operations |
| Registry | `ogx.registry.v2.RegistryV2` | identity, hosts, platform, enterprise |
| Identity | `ogx.platform.identity` + `ogx identity` CLI | repository linking |
| Approval / execution | `ogx.platform.operations`, enterprise approval modules | provider writes, governance |
| Agent / skill | declarative manifests (`ogx.agents`, `ogx.skills`) | discovery and inspection only |

## 1. Provider contract

`ogx.platform` is the canonical multi-platform engine. `ogx.hosts` is a
compatibility facade and must not grow a second implementation.

`ProviderAdapter` groups:

- **identity** — `identify()`, `api_version()`, `auth_status()`, `health()`
- **discovery** — `discover_repositories(owner=None, limit=...)`
- **repositories** — `get_repository`, `list_branches`, `get_branch`,
  `list_commits`, `get_commit`, `read_file`, `read_tree`
- **collaboration** — `list_pull_requests`, `get_pull_request`, `list_issues`,
  `get_issue`, `get_permissions`
- **rate limits** — `rate_limit()`
- **writes (approval-gated)** — `create_pull_request`, `create_issue`,
  `update_issue`

Rules:

1. **Capability truth.** A capability is `supported` only when the adapter
   class overrides the corresponding method:
   `getattr(cls, method) is not getattr(ProviderAdapter, method)`.
2. A descriptor-only provider can never claim a domain capability; it reports
   `unsupported`.
3. A credential-dependent capability reports `requires_auth` when no
   credential is present — never `supported`.
4. `require(capability)` raises `CapabilityUnavailableError` **before** any
   network call.
5. Capability states are exactly: `supported`, `partial`, `unsupported`,
   `requires_auth`, `requires_permission`, `requires_workspace`,
   `not_configured`, `unavailable`, `failed`.
6. Adapters normalize provider JSON into `ogx.platform.models`; provider-shaped
   dictionaries must not cross the adapter boundary.
7. Adapters must use `ogx.platform.transport` (bounded timeouts, size cap,
   redirect refusal, host pinning, no retry for state-changing requests) and
   `ogx.platform.security` (endpoint validation, credential policy, redaction).

## 2. Host contract

`ogx.hosts.interfaces.HostProvider` requires:

- `host_type`, `provider_type` (properties)
- `identify_host(host_identifier)`
- `inspect_host(host_id)`
- `get_capabilities(host_id)`
- `check_health(host_id)`
- `list_resources(host_id, resource_type)`
- `get_provider_info()`

`RepositoryProvider` requires repository discovery, inspection, branch/commit/
file/tree listing, pull-request listing, metadata, access check and provider
info. `WorkspaceProvider` requires discovery, inspection, access check,
capabilities and provider info.

Contract status:

- The interfaces exist and are importable.
- Adapters present: `local_filesystem`, `local_git`, and a GitHub shim.
- A shared host contract suite exists (`tests/test_host_contract.py`, 12 tests)
  and passes for the registry and the local adapters' metadata/capability
  surface. The session lifecycle, health/recovery and the two read-only local
  hosts are additionally covered by `tests/test_host_lifecycle.py` (13),
  `tests/test_host_filesystem_runtime.py` (16 passed, 3 skipped),
  `tests/test_host_git_runtime.py` (21) and `tests/test_host_cli_lifecycle.py`
  (6). The local filesystem and local Git hosts are therefore declared
  OPERATIONAL for that read-only surface only. No adapter is operational for
  writes, execution, remote or branch operations, and no cloud/remote host
  passes the suite. `ogx host list` reports `No hosts registered.` by default.
- A host claim is valid only after that adapter passes the shared contract
  suite: identity, discovery, connect, health, read, list, metadata,
  capabilities, failure handling, permission handling, disconnect, recovery.

## 3. Registry contract

`RegistryV2` is a SQLite-backed store (`OGX_HOME/registry/registry.db`,
schema version 2) with:

- tables: projects, repositories, workspaces, hosts (plus version metadata);
- `PRAGMA foreign_keys = ON`, WAL journal, `busy_timeout`;
- a `transaction()` context manager that commits on success and rolls back on
  error;
- migration preview/backup helpers (`MigrationError`, `RegistryCorruptedError`);
- immutable `project_id` and `identity_hash` fields.

Callers must not write identity columns directly; use the identity API so
conflict rules apply.

## 4. Identity contract

- `normalize_repository_url(url)` returns a credential-free, lowercase
  `host/path` key with `.git` stripped. Embedded credentials are rejected.
- `deterministic_repository_id(provider_id, normalized_url)` is
  `uuid5(namespace, "<provider>:<normalized>")`; the same repository therefore
  gets the same row id across processes and machines.
- `link_repository_to_project(...)` is idempotent for the same project and
  raises `DuplicateEntityError` when the URL is already linked to a different
  project. **OGX never re-parents a repository silently.**
- `assert_relocation_safe(project_id)` reports identity invariants after a
  workspace move; workspace locations may change, `project_id` and
  `identity_hash` must not.

Details and tested/untested relocation scenarios: `docs/PROJECT_IDENTITY.md`.

## 5. Approval and execution contract

For approval-gated writes:

1. `plan_*` performs validation only and no I/O. It checks capability,
   permission, payload size and refuses protected/default head branches.
2. `execute_*` refuses unless `approved=True`.
3. Execution appends an audit event under
   `<state_dir>/platform/audit.jsonl`, performs exactly one provider call, and
   records completion or failure.
4. A denial is a result, not an exception that can be bypassed by retry.
5. Destructive operations (delete, merge, protected-branch push, permission
   change, deploy) are **not implemented**.

The audit file is append-only JSONL; it is **not** tamper-evident and **not**
immutable storage.

## 6. Agent and skill contract

- Skills and agents are declarative manifests. They are parsed and inspected;
  third-party code is not executed.
- Manifest validation covers declared identity, version and requested
  capabilities.
- Plugin isolation, signature verification, publisher trust and revocation are
  **NOT IMPLEMENTED**; no plugin should be treated as trusted on the basis of
  its manifest alone.

## 7. Change rules

- Adding a provider capability means overriding the method on a real adapter and
  adding a test; documentation alone changes nothing.
- Adding a host means implementing `HostProvider` and adding the adapter to the
  contract suite before any operational claim.
- `core/ogx` must stay standard-library-only and structurally consistent with
  `src/ogx`; `python _dev/tools/sync_core_from_src.py --check` is the gate.
- Vendor-specific code belongs in `ogx.platform.adapters` (or a dedicated
  adapter package), never in the orchestration modules.

## 8. Verification

| Contract | Test evidence |
| --- | --- |
| Capability truth, provider lifecycle | `tests/test_platform_engine.py` |
| Transport bounds | `tests/test_transport_bounds.py` |
| Registry and relocation | `tests/test_registry.py` |
| Identity file lifecycle | `tests/test_identity.py` |
| Architecture, stdlib-only, cycles | `_dev/tests/test_architecture.py` |
| Drop-in parity | `_dev/tests/` suite (258 tests) |

Absence of a test is recorded as a gap, not as support.
