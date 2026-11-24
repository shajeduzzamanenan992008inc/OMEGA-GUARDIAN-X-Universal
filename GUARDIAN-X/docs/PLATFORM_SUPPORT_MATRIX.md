# Platform Support Matrix

Generated from the live registry (`ogx.platform.registry`), not from design
intent. States are shown for an **authenticated** session; without a credential
every credential-dependent capability reports `requires_auth`.

Regenerate with:

```bash
PYTHONPATH=src python -m ogx platform capabilities
```

`implementation_status` values:

- `verified` — adapter code exists and is covered by mocked-provider tests.
- `descriptor_only` — the provider is described (identity, auth discovery, API
  version) but no domain operation is implemented. Domain capabilities report
  `unsupported`.

## Summary

| Provider | Kind | Implementation | Supported | Partial | Unsupported |
|---|---|---|---|---|---|
| `github` | git_hosting | verified | 19 | 0 | 15 |
| `gitlab` | git_hosting | verified | 19 | 0 | 15 |
| `bitbucket` | git_hosting | verified | 18 | 0 | 16 |
| `gitea` | git_hosting | verified | 19 | 0 | 15 |
| `forgejo` | git_hosting | verified | 19 | 0 | 15 |
| `azure_devops` | git_hosting | verified | 14 | 0 | 20 |
| `aws_codecommit` | git_hosting | verified | 12 | 0 | 22 |
| `sourcehut` | git_hosting | descriptor_only | 3 | 0 | 31 |
| `azure_pipelines`, `github_actions`, `gitlab_ci`, `jenkins` | cicd | descriptor_only | 3 | 0 | 31 |
| `aws`, `azure`, `gcp` | cloud_infrastructure | descriptor_only | 3 | 0 | 31 |
| `docker_workspace`, `github_codespaces`, `gitpod`, `kubernetes_workspace`, `remote_ssh_workspace` | cloud_workspace | descriptor_only | 3 | 0 | 31 |
| `crates_io`, `maven_central`, `npm`, `nuget`, `pypi` | package_registry | descriptor_only | 3 | 0 | 31 |
| `jira`, `linear`, `trello` | project_management | descriptor_only | 3 | 0 | 31 |

29 capability entries are evaluated per provider. The three universally
supported entries for descriptor-only providers are `identify`,
`auth_discovery` and `api_version`.

## Verified providers

### github
`identify`, `auth_discovery`, `health_check`, `rate_limit_read`, `pagination`,
`api_version`, `permission_read`, `repository_discovery`,
`repository_metadata`, `branch_list`, `commit_list`, `commit_inspect`,
`file_read`, `tree_read`, `pull_request_list`, `issue_list`,
`pull_request_create`, `issue_create`, `issue_update`.

### gitlab
Same set as GitHub. Merge requests are normalized into the shared
`PullRequest` model.

### bitbucket
GitHub set minus `permission_read`. Uses app-password basic authentication.

### gitea / forgejo
Same set as GitHub. Both use `Authorization: token ...`. Forgejo defaults to
`https://codeberg.org/api/v1`; Gitea defaults to `https://gitea.com/api/v1`.
Self-hosted instances are configured with an explicit API base.

### azure_devops
`identify`, `auth_discovery`, `health_check`, `pagination`, `api_version`,
`repository_discovery`, `repository_metadata`, `branch_list`, `commit_list`,
`commit_inspect`, `file_read`, `tree_read`, `pull_request_list`,
`pull_request_create`.

### aws_codecommit
`identify`, `auth_discovery`, `health_check`, `api_version`,
`repository_discovery`, `repository_metadata`, `branch_list`,
`commit_inspect`, `file_read`, `tree_read`, `pull_request_list`,
`pull_request_create`. Requests are SigV4-signed.

## Deliberate limits

- `sourcehut` is `descriptor_only`: no validated GraphQL contract was
  available, so no endpoint was invented.
- The 21 family providers expose identity only. They are not operational
  integrations.
- Writes are limited to pull/merge request creation, always approval-gated.
- No capability is claimed for deleting, merging, pushing to protected
  branches, changing permissions, or deploying.
