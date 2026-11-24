# API Reference

This reference covers the `0.8.0a1` CLI and the primary Python entry points.
`--help` is authoritative for exact flags; this page does not invent options.

## 1. Installation entry point

```bash
pip install .
ogx --version          # ogx 0.8.0a1
ogx --help
```

The console script is `ogx = ogx.cli:main`. The package requires Python 3.11+,
has no runtime dependencies, and never imports the analyzed project.

## 2. Global behaviour

- Reports and state are written under the OGX state directory (`OGX_HOME`,
  default `~/.omega-shield`-style user data path), not into the analyzed
  repository.
- Scanning is read-only and performs no network I/O.
- Writes and remote operations are approval-gated.
- Exit status is non-zero on failure; do not parse success from prose.

## 3. CLI commands

### Lifecycle and onboarding

| Command | Purpose |
| --- | --- |
| `ogx init` | Initialize global state outside target repositories |
| `ogx env` | Create/validate private environment settings |
| `ogx start` | Safe onboarding alias for detect + scan |
| `ogx detect` | Detect technologies without modifying the repository |
| `ogx scan` | Read-only scan with external reports |
| `ogx integrate` | Install project-root AI governance and OGX control folder |

### Analysis

| Command | Purpose |
| --- | --- |
| `ogx analyze` | Universal static engine pipeline |
| `ogx intelligence` | Static project intelligence and engineering analysis |
| `ogx android` | Android static engine (no build/device execution) |
| `ogx inventory` | Bounded file and technology inventory |
| `ogx architecture` | Static import/dependency graph |
| `ogx sbom` | Best-effort CycloneDX 1.5 inventory |
| `ogx impact` | Static import dependents for a changed file |
| `ogx licenses` | License files and SPDX headers (not legal advice) |
| `ogx osv-audit` | Optional live OSV lookup for declared dependencies |
| `ogx diagnose` / `ogx debug` | Log analysis and correlation; never executes the project |
| `ogx validate` | Static project validation |
| `ogx test-plan` / `ogx fix-plan` | Plans only; no source edits |
| `ogx policy` | Inspect target project OGX policy |
| `ogx engines` | List or inspect capability-declared analysis engines |
| `ogx guardian` | Audit, maintenance, safe repair and upgrade checks |
| `ogx doctor` / `ogx compatibility` | Toolchain and capability reporting |

### Identity and registry

| Command | Purpose |
| --- | --- |
| `ogx identity {create,show,validate,recover,update}` | Manage `.guardian-x/identity.json` |
| `ogx registry` | Registry V2: projects, repositories, workspaces, hosts |
| `ogx projects` | Project shortcuts in global storage |
| `ogx pap` | GX-PAP Project Activation Protocol |
| `ogx discover` / `ogx discover-root` / `ogx activate` | Project discovery and activation |

### Providers and hosts

| Command | Purpose |
| --- | --- |
| `ogx providers {list,inspect,capabilities}` | Universal provider registry and matrix |
| `ogx platform {status,capabilities,repo,pr}` | Provider engine status, matrix, read and approval-gated writes |
| `ogx provider` / `ogx host` / `ogx remote` / `ogx repository` / `ogx workspace` | Compatibility surfaces |

#### `ogx host`

Local-only host lifecycle (read-only hosts; write/exec/branch/remote are
unsupported):

| Command | Purpose |
| --- | --- |
| `ogx host register --type local_filesystem --identifier <id> --root <path>` | Register a read-only local filesystem host; root may not be empty |
| `ogx host register --type local_git --identifier <id> --root <path>` | Register a read-only local Git host; root must be a Git repository |
| `ogx host list` | List registered hosts (status, not live) |
| `ogx host connect <id>` | Connect and validate the host | | Returns 0 on success; non-zero on failure |
| `ogx host disconnect <id>` | Disconnect the live session (idempotent while disconnected) |
| `ogx host health <id>` | Run the adapter health check | | Returns 0 when healthy |
| `ogx host recover <id> [--attempts N]` | Bounded read-only recovery (1–5 attempts, default 3) |
| `ogx host sessions` | List live sessions | | Process-local; empty after disconnect/restart |
| `ogx host sessions --json` | Live sessions as JSON |
| `ogx host status <id>` | Status (live state + coarse persisted status) |
| `ogx host status <id> --json` | Status as JSON |
| `ogx host inspect <id>` | Inspect a registered host |
| `ogx host inspect <id> --json` | Inspect as JSON |

Exit codes: `0` success, non-zero on failure. Errors are printed to stderr.

Truths about capability reporting:

- `ogx host inspect ... --json` and `ogx platform capabilities --for-host ...`
  return the adapter-reported capabilities when the session is connected/
  degraded, and `unavailable` otherwise.
- Registration does not mark a host `active`; new registrations start at
  `registered`.
- `connected` is never persisted; a restart drops sessions.

### Discovery

| Command | Purpose |
| --- | --- |
| `ogx host discover <host_id>` | Discover repositories/projects on a host (read-only) |

For the implemented local filesystem host, `ogx host discover` delegates to
the adapter's `discover_projects()`, which runs project-root discovery against
the configured host root. The command is read-only.

### Governance and execution

| Command | Purpose |
| --- | --- |
| `ogx repo` | Read-only Git inspection plus explicitly approved operations |
| `ogx sandbox` | Plan or run inside a disposable restricted Docker container |
| `ogx enterprise` | Local tenant/organization data-model preview |
| `ogx governance` | Email-gated approvals and controlled execution |
| `ogx agents` / `ogx skills` | Discover agents; inspect declarative skill packs |

## 4. Python API

Only these are intended for external use; everything else is internal and may
change without notice.

| Module / symbol | Contract |
| --- | --- |
| `ogx.cli.main(argv=None)` | CLI entry point; returns an exit status |
| `ogx.__version__` | Package version string |
| `ogx.platform.registry.UniversalProviderRegistry` | Provider snapshot, descriptors, capability matrix, negotiation |
| `ogx.platform.base.ProviderAdapter` | Provider adapter contract |
| `ogx.platform.identity.normalize_repository_url` | Canonical repository key |
| `ogx.platform.identity.link_repository_to_project` | Idempotent, conflict-safe linking |
| `ogx.platform.identity.assert_relocation_safe` | Post-relocation invariant check |
| `ogx.registry.get_registry()` / `ogx.registry.v2.RegistryV2` | SQLite project/repository/workspace/host registry |
| `ogx.hosts.interfaces.HostProvider` | Host contract (compatibility surface) |

Example (read-only):

```python
from ogx.platform.registry import UniversalProviderRegistry

registry = UniversalProviderRegistry()
for provider in registry.descriptors():
    print(provider.provider_id, provider.kind, provider.status)
```

Capability negotiation returns states such as `requires_auth`; it does not make
a network call to prove a capability.

## 5. Environment variables

| Variable | Purpose |
| --- | --- |
| `OGX_HOME` | State directory (registry, audit, reports) |
| `OGX_GITHUB_TOKEN` | GitHub / GitHub Enterprise token |
| `OGX_GITHUB_API_BASE` | GitHub Enterprise API base |
| `OGX_GITLAB_TOKEN` | GitLab private token |
| `OGX_BITBUCKET_TOKEN` | Bitbucket `username:app-password` |
| `OGX_GITEA_TOKEN` / `OGX_FORGEJO_TOKEN` | Gitea / Forgejo token |
| `OGX_AZURE_DEVOPS_TOKEN`, `OGX_AZURE_DEVOPS_ORG` | Azure DevOps PAT and org |
| `OGX_AWS_ACCESS_KEY_ID`, `OGX_AWS_SECRET_ACCESS_KEY`, `OGX_AWS_REGION` | CodeCommit SigV4 (falls back to `AWS_*`) |
| `OGX_SOURCEHUT_TOKEN` | SourceHut (descriptor only) |

Credentials are read per provider, never persisted by OGX, never loaded from
the analyzed project and never printed. See `docs/AUTHENTICATION.md`.

## 6. Non-API surfaces

- No REST/HTTP server API is implemented.
- No dashboard is implemented.
- No webhook receiver is implemented.
- No plugin execution API exists; skills and agents are declarative manifests.

These are listed so that their absence is not mistaken for an undocumented
endpoint.
