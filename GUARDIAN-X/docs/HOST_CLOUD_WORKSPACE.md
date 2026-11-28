# Cloud workspace host (HOST-11)

**Status:** implemented against the documented GitHub Codespaces REST API and
tested with a mocked transport. **Live verification is BLOCKED** — no authorized
Codespaces credential or account is available in this environment.

**Review date:** 2026-11-28

## 1. Provider selection

The backlog requires one concrete cloud workspace provider, chosen from a
documented API and auth model, with no invented endpoints. GitHub Codespaces was
selected because:

* its REST API is publicly documented (`GET /user/codespaces`,
  `GET /repos/{owner}/{repo}/codespaces`, `GET /user/codespaces/{name}`,
  `GET /user/codespaces/{name}/machines`);
* authentication reuses the project's existing GitHub credential path
  (`OGX_GITHUB_TOKEN`), so no second secret store or OAuth application is
  introduced;
* workspace metadata is sufficient for read-only inventory, which is the honest
  scope this pass can support.

Gitpod, AWS Cloud9 and other families remain **descriptor-only** in
`platform/families.py`. Their account model and API contract are not implemented
and are not advertised.

## 2. Implementation

`src/ogx/hosts/adapters/cloud_runtime.py` — `CloudWorkspaceHostAdapter`
(`host_type = "cloud_workspace"`), registered in
`hosts/adapters/runtime_factory.py` and in the capability matrix.

| Operation | Behaviour |
| --- | --- |
| `validate()` | provider must be `github_codespaces`; root must be `codespaces` or `owner/repo`; a code credential must be present |
| `connect()` | bounded probe of the codespaces list; a session is never reported connected without a successful response |
| `list_workspaces(limit)` | 1–100 normalized codespaces for the repository scope or the authenticated user |
| `inspect_workspace(name)` | one codespace, name validated against `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` |
| `health()` | re-probes, reports `healthy` with latency or `unreachable` with a redacted issue |
| `capabilities()` | `workspace_read` supported; create/delete/start/stop/exec, file and repository operations `unsupported` |
| `metadata()` | provider, API base, scope, credential env name and capability map |

Safety properties:

* HTTPS only, with `expected_host="api.github.com"` pinned through the canonical
  `platform.transport.HttpTransport`; redirects are refused by that transport.
* The bearer token is read from `OGX_GITHUB_TOKEN` only, placed in the
  `Authorization` header, never in a URL and never in a response or error.
* Error mapping is explicit: 401/403 → `cloud_permission_denied`, 404 →
  `cloud_not_found`, 429 → `cloud_rate_limited`, other ≥400 → `cloud_http_error`;
  transport exceptions collapse to a type name and are redacted.
* Every response is shape-checked; a missing/malformed `codespaces` array or a
  non-object entry is refused (`CloudResponseError`) rather than partially used.
* Environment secrets in the upstream payload are never copied into the
  normalized record (`environment_secrets` and similar fields are dropped).

## 3. Usage

```bash
ogx host register --type cloud_workspace --provider github --name codespaces \
  --identifier acme/guardian
ogx host list
ogx host capabilities <host-id>          # only workspace_read is supported
ogx host workspaces <host-id> --limit 20 # requires a live credential
```

Without `OGX_GITHUB_TOKEN`, `host connect` and `host workspaces` fail closed with
`credential_required`; registration itself does not need the token.

## 4. Evidence

```bash
python -m pytest tests/test_cloud_host.py -q     # 12 passed (mocked transport)
python -m pytest -q                              # 1182 passed, 23 skipped
ogx host register --type cloud_workspace --provider github \
  --name codespaces --identifier acme/demo --json     # exit 0
ogx host capabilities <host-id>                  # workspace_read: supported, rest unsupported
```

Covered by tests: credential absence, unsupported provider, repository vs user
scope, request shape (host pinning, header auth, no query credentials), response
normalization, secret stripping, capability truth, factory construction,
permission denial without token leakage, malformed/non-JSON responses, transport
failure redaction, inspection before/after connect, invalid workspace names and
health degradation.

## 5. Remaining prerequisite (BLOCKED)

A live, non-production Codespaces account with a least-privilege token
(`codespace` read scope) is required to run:

1. `ogx host register --type cloud_workspace --identifier <real-repo>`
2. `ogx host connect <id>` and confirm the probe against the real API
3. `ogx host workspaces <id>` and compare the inventory with the GitHub UI
4. `ogx host health <id>` and record latency and state

Until then, every cloud-host claim stays at "TESTED with a mocked transport".
No endpoint, field or capability in this document is speculative: they are the
documented Codespaces response fields the adapter actually reads.
