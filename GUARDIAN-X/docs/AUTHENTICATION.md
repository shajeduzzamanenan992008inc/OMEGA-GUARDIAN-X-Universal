# Provider Authentication

Credentials are read from the process environment only. OGX never writes,
prints, logs or persists provider secrets, and it never loads credentials from
the project being analyzed.

## Environment variables

| Provider | Variable(s) | Transport | Default API base |
|---|---|---|---|
| GitHub | `OGX_GITHUB_TOKEN` | `Authorization: Bearer <token>` | `https://api.github.com` |
| GitHub Enterprise | `OGX_GITHUB_TOKEN` + `OGX_GITHUB_API_BASE` | Bearer | explicit |
| GitLab | `OGX_GITLAB_TOKEN` | `PRIVATE-TOKEN: <token>` | `https://gitlab.com/api/v4` |
| Bitbucket | `OGX_BITBUCKET_TOKEN` (`username:app-password`) | Basic auth | `https://api.bitbucket.org/2.0` |
| Gitea | `OGX_GITEA_TOKEN` | `Authorization: token <token>` | `https://gitea.com/api/v1` |
| Forgejo | `OGX_FORGEJO_TOKEN` | `Authorization: token <token>` | `https://codeberg.org/api/v1` |
| Azure DevOps | `OGX_AZURE_DEVOPS_TOKEN` + `OGX_AZURE_DEVOPS_ORG` | Basic (`:<pat>`) | `https://dev.azure.com` |
| AWS CodeCommit | `OGX_AWS_ACCESS_KEY_ID`, `OGX_AWS_SECRET_ACCESS_KEY`, `OGX_AWS_REGION` (falls back to `AWS_*`) | SigV4 | `https://codecommit.<region>.amazonaws.com` |
| SourceHut | `OGX_SOURCEHUT_TOKEN` | descriptor only | `https://git.sr.ht` |

A credential is resolved per adapter, from that adapter's own variable list.
**A token for one provider is never reused for another provider.** If only
`OGX_GITHUB_TOKEN` is present, a GitLab adapter has no credential and reports
`requires_auth`.

## What a missing credential means

- Unauthenticated capabilities (`identify`, `auth_discovery`, `api_version`)
  remain `supported`.
- Everything else reports `requires_auth`. The CLI prints `[----]` in
  `ogx providers list`.
- Calls that require a credential raise before any network request.

Inspect the effective state without contacting the network:

```bash
ogx providers inspect github
ogx providers inspect github --check   # performs an authenticated health check
```

## Self-hosted instances

Self-hosted deployments are configured with an explicit API base, either through
the provider's `OGX_*_API_BASE` variable or by passing `api_base` to the adapter.

`validate_api_base()` enforces:

- HTTPS by default;
- no `user:pass@` userinfo in the URL;
- rejection of loopback addresses unless `OGX_ALLOW_LOOPBACK_PROVIDER_HTTP=1`
  (and then only HTTP loopback);
- rejection of private, link-local and cloud-metadata addresses unless
  `OGX_ALLOW_PRIVATE_PROVIDER_ENDPOINTS=1`;
- rejection of non-HTTP(S) schemes and of query strings carrying data.

Path components (for example `/api/v1`) are permitted so self-hosted Gitea and
GitLab work.

## Local hosts

The host lifecycle layer uses local read-only hosts. `local_filesystem` and
`local_git` adapters use the local OS permissions and the repository on disk;
they do not authenticate against any remote host and do not issue network
requests. There is no host credential variable.

## Failure handling

| Condition | Normalized error | Retried |
|---|---|---|
| 401 / invalid token | `AuthenticationError` | no |
| 403 / insufficient scope | `PermissionDeniedError` | no |
| 404 | `NotFoundError` | no |
| 409 | `ConflictError` | no |
| 429 / `Retry-After` | `RateLimitError` | yes, with backoff |
| 5xx on a read | `ServerError` | yes, with backoff |
| 5xx on a write | `ServerError` | **no** |
| Malformed JSON | `MalformedResponseError` | no |
| Oversized body | `ResponseTooLargeError` | no |
| Timeout / connection error | `TransportError` | yes for reads |

Provider error bodies are redacted before they reach the user: token-shaped
values, `Authorization` headers and `Bearer` values are replaced with
`[REDACTED]`.

## Deliberately unsupported

- Storing tokens in the registry database.
- Passing credentials in URLs.
- Reading credentials from an analyzed project (`.env`, config files, CI files).
- Using one provider's token against another provider's API.
- Following HTTP redirects or pagination links to another host.
