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

Approval-gated write operations are implemented for GitHub. The path is:

1. `plan_*()` functions validate the capability, branch names, payload size
   and refuse protected/default head branches. They perform no I/O.
2. `execute_*()` functions refuse unless `approved=True`, then write an audit
   event, perform the provider call(s), and record completion or failure.

Implemented write operations for GitHub:

- **Pull/Merge Request creation** (`plan_pull_request` / `execute_pull_request`) — draft PR only
- **Branch creation** (`plan_branch_create` / `execute_branch_create`) — from a base branch or commit SHA
- **File create/update** (`plan_file_update` / `execute_file_update`) — Contents API path, single file
- **Multi-file commit** (`plan_file_commit` / `execute_file_commit`) — Git database API, bounded (max 20 files, 4 MiB total)
- **Fast-forward ref update** (`plan_ref_update` / `execute_ref_update`) — recovery for failed push, never force-pushes

Audit records are appended to `<state_dir>/platform/audit.jsonl`
(`OGX_HOME` aware) with hash-chaining and secret redaction. Destructive operations
(delete, merge PR, force push, permission changes, deployments) are not
implemented anywhere.

## Compatibility

- `ogx.hosts` re-exports the canonical adapters (`GitHubProvider` *is*
  `ogx.platform.adapters.github.GitHubAdapter`).
- `ogx host ...`, `ogx remote ...`, `ogx provider ...` and `ogx platform ...`
  remain valid commands. New surfaces are `ogx providers ...` and
  `ogx platform status|capabilities`.
- Existing registry, approval and enterprise modules are reused rather than
  duplicated: `RegistryV2`, `state_dir()`, `ApprovalManager` and the
  `platform.pr` approval operation.

## Inbound webhook verification

`ogx.platform.webhooks` authenticates deliveries that arrive *from* a provider.
It is a library, not an HTTP server: the caller receives the raw body, verifies
it, and only then parses it.

| Provider | Mechanism | Signature header | Event header |
| --- | --- | --- | --- |
| GitHub | HMAC-SHA256 (`sha256=<hex>`) | `X-Hub-Signature-256` | `X-GitHub-Event` |
| GitLab | shared secret token | `X-Gitlab-Token` | `X-Gitlab-Event` |
| Bitbucket Cloud | HMAC-SHA256 (`sha256=<hex>`) | `X-Hub-Signature` | `X-Event-Key` |
| Gitea | HMAC-SHA256 (hex) | `X-Gitea-Signature` | `X-Gitea-Event` |
| Forgejo | HMAC-SHA256 (hex) | `X-Forgejo-Signature` | `X-Forgejo-Event` |
| Azure DevOps | none in the delivery contract | — | — |
| AWS CodeCommit (SNS) | none in the HTTP headers | — | — |

```python
from ogx.platform.webhooks import DeliveryReplayGuard, WebhookVerifier

verifier = WebhookVerifier(
    "github",
    secret,
    allowed_events={"push", "pull_request"},
    replay_guard=DeliveryReplayGuard(),
)
event = verifier.verify(request.headers, request.body)  # raises on failure
payload = event.json()  # only after the signature was accepted
```

Guarantees and limits:

* comparison is constant-time (`hmac.compare_digest`); a missing, malformed or
truncated signature never passes;
* bodies must be `bytes` (re-encoding changes the signature), and are bounded
  (`max_body_bytes`, default 1 MiB) before hashing;
* secrets shorter than 16 bytes, unknown providers and unsigned providers raise
  `WebhookVerificationError` with a stable `code`; error text never includes the
  secret or the body;
* the event header is read only after the signature passes and is checked
  against the caller's allowlist;
* the replay guard is bounded, TTL-scoped, process-local memory — a multi-worker
  receiver needs shared storage;
* payload contents are still untrusted input, not instructions.

Azure DevOps service hooks and CodeCommit/SNS notifications are rejected at
construction (`unsupported_provider`); authenticate those at the transport or
gateway layer instead of pretending the payload is signed.
