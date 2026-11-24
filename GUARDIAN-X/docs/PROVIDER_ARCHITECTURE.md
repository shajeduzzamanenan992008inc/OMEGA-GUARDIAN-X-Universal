# Universal Provider Architecture

`src/ogx/platform/` is the canonical multi-platform engine. It supersedes the
prototype under `src/ogx/hosts/`, which remains importable as a thin
compatibility facade.

## Layers

```text
ogx.platform
├── capabilities.py   capability enum, capability states, provider kinds, descriptors
├── base.py           ProviderAdapter contract; capability states derived by introspection
├── transport.py      bounded HTTP transport, retries, pagination helpers, host pinning
├── security.py       endpoint validation (SSRF), credential policy, redaction
├── models.py         provider-neutral repository/branch/commit/PR/issue models
├── errors.py         normalized, redaction-safe error taxonomy
├── registry.py       UniversalProviderRegistry (snapshot, descriptors, matrix, negotiate)
├── identity.py       repository <-> project linking with deterministic IDs
├── operations.py     write plans, approval gate, audit trail
├── families.py       descriptor-only families (workspaces, CI/CD, PM, registries, cloud)
├── cli_commands.py   CLI handlers for `ogx providers`, `ogx platform`, `ogx host`
└── adapters/         github, gitlab, bitbucket, gitea(+forgejo), azure_devops,
                      aws_codecommit (SigV4), sourcehut (descriptor-only)
```

`ogx.platform` depends only on the standard library and on `ogx.registry`
(SQLite) for identity linking. It never imports the CLI, the analyzers or the
project files being analyzed.

## Capability truth

A capability is advertised as `supported` only when the adapter class actually
overrides the corresponding method:

```python
getattr(cls, method) is not getattr(ProviderAdapter, method)
```

Consequences:

- A descriptor-only provider (a family stub, or SourceHut) can never claim a
  domain capability. It reports `unsupported`.
- A credential-dependent capability reports `requires_auth` when no credential
  is present, not `supported`.
- `require(capability)` raises `CapabilityUnavailableError` before any network
  call, so a missing capability can never look like a successful operation.

The nine states are exactly:

`supported`, `partial`, `unsupported`, `requires_auth`, `requires_permission`,
`requires_workspace`, `not_configured`, `unavailable`, `failed`.

## Adapter contract

`ProviderAdapter` exposes:

- identity: `identify()`, `api_version()`, `auth_status()`, `health()`
- discovery: `discover_repositories(owner=None, limit=...)`
- repositories: `get_repository`, `list_branches`, `get_branch`, `list_commits`,
  `get_commit`, `read_file`, `read_tree`
- collaboration: `list_pull_requests`, `get_pull_request`, `list_issues`,
  `get_issue`, `get_permissions`
- rate limits: `rate_limit()`
- writes (approval-gated): `create_pull_request`, `create_issue`, `update_issue`

Adapters normalize provider JSON into `ogx.platform.models` values. No adapter
returns provider-specific dictionaries to callers.

## Reliability

`HttpTransport` provides:

- bounded timeouts (default 15 s, maximum 300 s);
- a response-size cap (default 2 MB, minimum 1 KiB, maximum 64 MiB);
- at most 5 retries with exponential backoff capped at 2 s, honoring
  `Retry-After`;
- **no automatic retry for state-changing requests**;
- refusal of HTTP redirects, so a provider cannot bounce a request to another
  host;
- host pinning: every request and every pagination URL must stay on the
  configured API host;
- correlation IDs recorded for diagnostics.

Pagination is bounded by an explicit item limit; the engine never follows an
unbounded cursor chain.

## Writes

Only pull/merge request creation is implemented. The path is:

1. `plan_pull_request()` validates the capability, branch names, payload size
   and refuses protected/default head branches. It performs no I/O.
2. `execute_pull_request()` refuses unless `approved=True`, then writes an audit
   event, performs exactly one provider call, and records completion or failure.

Audit records are appended to `<state_dir>/platform/audit.jsonl`
(`OGX_HOME` aware). Destructive operations (delete, merge, push to a protected
branch, permission changes, deployments) are not implemented anywhere.

## Compatibility

- `ogx.hosts` re-exports the canonical adapters (`GitHubProvider` *is*
  `ogx.platform.adapters.github.GitHubAdapter`).
- `ogx host ...`, `ogx remote ...`, `ogx provider ...` and `ogx platform ...`
  remain valid commands. New surfaces are `ogx providers ...` and
  `ogx platform status|capabilities`.
- Existing registry, approval and enterprise modules are reused rather than
  duplicated: `RegistryV2`, `state_dir()`, `ApprovalManager` and the
  `platform.pr` approval operation.
