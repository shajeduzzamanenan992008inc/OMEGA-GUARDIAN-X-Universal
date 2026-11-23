> **Superseded in 0.8.0a1.** The product overview is now `README.md` / `README.bn.md`. Kept for the detailed 0.7 command descriptions.

# OMEGA GUARDIAN X (OGX) — Universal Software Intelligence

**Release:** `0.7.1a1` (Guardian alpha — universal maintenance and governed upgrade workflows)  
**Runtime:** Python 3.11+ · zero mandatory third-party runtime dependencies  
**Deployment intent:** Local-first, self-hosted, cloud/hybrid-ready architecture  
**Default safety mode:** Read-only analysis; explicit approval for writes and remote operations

> OGX is a modular repository and software-project intelligence foundation. It is not yet a completed enterprise SaaS, a certified security product, a full static-analysis suite, or a universal runtime debugger. This Guardian alpha adds consolidated multi-signal maintenance audits, common structured-manifest syntax checks, allowlisted safe repair, opt-in Git remote checking/fetching, cross-platform dependency-bot configuration and an additive 51-engine universal analysis layer. See the capability matrix before relying on any check.

## Product design principles

- One external OGX engine can register and inspect many local repositories and connect to multiple Git hosting services.
- OGX's runtime, state, logs, caches and reports remain outside target repositories.
- Optional root integration creates only separate policy/control files, preserves existing files and records SHA-256 ownership for safe removal.
- Project code is not executed during normal scans.
- Remote clone, branch creation, push and Pull/Merge Request creation require explicit command-line confirmation.
- Sandbox commands use a pre-pulled immutable image digest, no network, a read-only container root, dropped Linux capabilities, process/memory/CPU limits and a temporary project snapshot. There is no unsandboxed fallback.
- AI agents and skills are adapters/extensions, not trusted authorities. Their output is untrusted and all operations are governed by OGX policy/approval checks.
- Unknown technologies are never rejected solely because they are unknown: OGX provides generic inventory and reports capability gaps.

## Install from source

Windows PowerShell:

```powershell
cd D:\Tools\OMEGA-GUARDIAN-X
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --no-build-isolation -e .
ogx --help
```

Linux/macOS:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-build-isolation -e .
ogx --help
```

The released wheel can be installed into a dedicated OGX environment. Do not install OGX dependencies into a target project unless that is an intentional project decision.

## Private environment and secrets

Keep OGX mail credentials, approval identities and optional Git-hosting tokens in one private environment file. OGX stores it outside scanned repositories by default:

```powershell
ogx env init
ogx env path
# Open the displayed path in Notepad, fill in required values, then:
ogx env check
```

The template documents Brevo/SMTP, approval mailboxes and GitHub/GitLab/Bitbucket/Gitea/Forgejo settings. Real `.env` files are ignored by Git; only the placeholder `.env.example` is included in this release. Environment variables already present in the OS take precedence. OGX never loads a target repository's `.env` automatically. See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Core workflows

```powershell
# Initialize global OGX state outside target projects
ogx init

# Discover stack and compatibility
ogx detect "D:\FTP\NexHaat"
ogx compatibility "D:\FTP\NexHaat"

# Main bounded, read-only scan (writes reports only to OGX external state)
ogx scan "D:\FTP\NexHaat" --format both

# Inventory, architecture, SBOM and license inventory
ogx inventory "D:\FTP\NexHaat"
ogx architecture "D:\FTP\NexHaat" --format mermaid
ogx sbom "D:\FTP\NexHaat"
ogx licenses "D:\FTP\NexHaat"

# Optional online dependency advisory lookup (sends exact package names/versions only)
ogx osv-audit "D:\FTP\NexHaat" --online --confirm-network

# Diagnose existing logs and generate a no-edit remediation plan
ogx diagnose .\logs\app.log
ogx debug "D:\FTP\NexHaat" --logs .\logs\app.log
ogx impact "D:\FTP\NexHaat" --file src\services\cart.py
ogx test-plan "D:\FTP\NexHaat" --logs .\logs\app.log
ogx fix-plan "D:\FTP\NexHaat"

# Detect installed AI agents and optional toolchains
ogx agents list
ogx skills list
ogx doctor

# Inspect Git metadata and a public/private remote (uses local Git credential helpers)
ogx repo inspect "D:\FTP\NexHaat"
ogx repo remote https://github.com/owner/repository.git

# Clone requires explicit confirmation and a non-existing destination
ogx repo clone https://github.com/owner/repository.git D:\Repos\repository --confirm

# Project-root control integration: plan first, then explicitly approve
ogx integrate plan "D:\FTP\NexHaat"
ogx integrate apply "D:\FTP\NexHaat" --confirm
ogx integrate status "D:\FTP\NexHaat"

# Git write operations require explicit confirmation; main/master pushes are refused
ogx repo branch-create "D:\FTP\NexHaat" feature/ogx-review --confirm
ogx repo push "D:\FTP\NexHaat" feature/ogx-review --confirm

# Git hosting API calls: store tokens in environment variables, never project files
$env:OGX_GITHUB_TOKEN = "<your-token>"
ogx platform repo github https://github.com/owner/repository
ogx platform pr github https://github.com/owner/repository --title "OGX review" --head feature/ogx-review --base main --confirm
```

Supported API credential variables:

- GitHub: `OGX_GITHUB_TOKEN` (repository metadata and draft PR creation)
- GitLab: `OGX_GITLAB_TOKEN` (project metadata and draft merge request creation); self-managed instances may set `OGX_GITLAB_API_BASE`
- Bitbucket: `OGX_BITBUCKET_TOKEN` (Bearer access token, or `username:app_password` for Basic auth)
- Gitea/Forgejo: `OGX_GITEA_TOKEN` / `OGX_FORGEJO_TOKEN`; custom host API base is derived from the remote URL
- GitHub Enterprise may set `OGX_GITHUB_API_BASE`

Tokens are read from the process environment and are not persisted by OGX. Use least-privilege scopes and your platform's credential manager. `repo remote` and generic Git operations work without the platform API token when Git authentication is configured separately.

## Universal engine layer

OGX 0.7.1a1 adds an additive engine layer alongside the existing scanner and Guardian workflows. All engines are static and read-only: they inspect bounded files and never execute project code, build tools, package managers, tests, adb or emulators.

```powershell
# List the 51 registered engines and their capability health
ogx engines list
ogx engines list --json

# Inspect detection and capability states for one project
ogx engines inspect "D:\Repos\MyProject" --engines android,lang-python --json

# Run the universal static pipeline (external JSON/Markdown reports)
ogx analyze "D:\Repos\MyProject" --format both

# Run the dedicated Android static engine
ogx android "D:\Repos\MyProject" --format both
```

The registry contains one Android engine and 50 spec-driven language/technology engines. Engine results use explicit states: `supported`, `partial`, `not_configured`, `unsupported` and `failed`. Build and test operations perform discovery only and report `executed: false`; they do not claim that a project builds or passes tests.

Android analysis covers manifest attributes, exported components, permissions, Gradle configuration, R8/minification, resources, source heuristics, test discovery, Lint XML ingestion and APK/AAB ZIP metadata. It does **not** decode binary manifests, cryptographically verify APK signatures, or run Gradle, unit tests, instrumentation tests, adb, emulators or devices. In this build environment the Android SDK, Gradle, adb, sdkmanager and emulator are absent, so Android build/test is `not_configured` and device operations are `not_attempted`. See [`docs/ANDROID_ENGINE.md`](docs/ANDROID_ENGINE.md), [`docs/LANGUAGE_SUPPORT_MATRIX.md`](docs/LANGUAGE_SUPPORT_MATRIX.md) and [`docs/ENGINE_DEVELOPMENT.md`](docs/ENGINE_DEVELOPMENT.md).

## Run project commands in the disposable sandbox

First inspect local sandbox capability:

```powershell
ogx sandbox plan
```

OGX never pulls an image automatically. Pull, review and pin an approved image digest yourself, then:

```powershell
ogx sandbox run "D:\FTP\NexHaat" --image "python@sha256:<64-hex-digest>" --confirm -- python -m pytest
```

The sandbox copies the project to a temporary directory, excludes `.git`, common generated/dependency directories and common `.env`/credential files, disables network access, and deletes the snapshot after the run. It does not mount the Docker socket or host home directory. It is a restricted container—not a formally verified VM—and the Docker daemon/host remain in the trusted computing base. Build tools and dependencies must already be available inside the pinned image or offline project snapshot.

## Universal Guardian: project protection, maintenance and upgrades

```powershell
# Consolidated stack/security/architecture/maintenance audit (reports remain outside the project)
ogx guardian audit "D:\Repos\MyProject"

# Maintenance priorities and deterministic repair preview
ogx guardian maintain "D:\Repos\MyProject"
ogx guardian repair-plan "D:\Repos\MyProject"

# Apply only supported safe fixes after reviewing the preview (creates external backup)
ogx guardian repair "D:\Repos\MyProject" --confirm

# Local Git remote check; add --online for read-only branch-head comparison
ogx guardian upgrade-check "D:\Repos\MyProject"
ogx guardian upgrade-check "D:\Repos\MyProject" --online

# Fetch remote-tracking refs only; no merge/rebase/checkout
ogx guardian upgrade-fetch "D:\Repos\MyProject" --remote origin --confirm

# Audit OGX itself and produce a governed self-evolution proposal
ogx guardian evolve .
```

Root integration now previews/creates a technology-aware Dependabot file and a Renovate configuration. Dependabot covers GitHub; Renovate is the cross-platform option for GitHub, GitLab, Bitbucket, Azure DevOps, Gitea and Forgejo, subject to each platform's supported features and hosted/self-hosted bot setup. Configuration files alone do not install or schedule the bot. See [`docs/UNIVERSAL_GUARDIAN.md`](docs/UNIVERSAL_GUARDIAN.md) for exact behavior, limitations and safe workflows.

Guardian audits do not execute target project code. `repair` currently applies only an allowlisted `.env` ignore fix when it is safe; it does not generate arbitrary source-code patches. Project checks still require an explicitly approved restricted sandbox command. Upgrade checks never merge or deploy, and dependency bots should create reviewable PRs/MRs rather than auto-merging unreviewed changes.

## Skills and agent adapters

```powershell
# List built-in declarative skills
ogx skills list

# Validate an external skill without executing it
ogx skills validate "D:\Skills\my-skill"

# Detect supported agent CLIs and declared adapters
ogx agents list
```

Built-in skill packs are under `core/ogx/skillpacks/official/`. Skills are declarative guidance/manifests in this release; the registry validates manifests but does not execute third-party skill code. Agent discovery reports runtime presence only. IDE/CLI/MCP adapters still require the respective supported product interfaces and user-granted permissions.

## Enterprise data-model preview

The release contains a tenant-oriented SQLite schema and local data-store prototype for organizations, memberships, projects, licenses, usage events, API credential hashes and audit events. It is **not** an authentication server or production SaaS service. Production deployment still requires PostgreSQL migrations, real SSO/OIDC, API authorization middleware, tenant isolation tests, encryption/key management, rate limits, backups, observability and an independent security review.

```powershell
ogx enterprise init
ogx enterprise org-create --slug acme --name "Acme Inc" --owner-subject "your-idp-subject"
```

The `external_subject` is an identity-provider subject identifier for the local prototype; this command does not authenticate the owner. Never expose this local SQLite store as a public multi-tenant API.

## Safety model

- Scans, inventory, architecture analysis, SBOM generation, license inventory and log diagnostics are read-only with respect to the target repository.
- Reports and registry data are external to the target repository.
- `integrate apply` writes only missing OGX-owned control files after explicit `--confirm`; existing files are preserved.
- `integrate remove` removes only unchanged files whose hashes match the ownership manifest.
- Clone, branch creation, push and PR/MR creation require explicit confirmation.
- The scanner uses heuristics. A finding is not automatically a confirmed vulnerability; reports include evidence, severity and confidence.
- `ogx debug` correlates supported traceback frames, log patterns and static findings into root-cause hypotheses; `ogx impact` estimates import-based dependents; `ogx test-plan` writes a regression-test proposal. These do not prove causation or automatically fix code.
- Optional OSV.dev advisory lookup is available for exact manifest-declared dependency versions. It is an advisory candidate check, not a resolved lockfile audit or exploitability proof. It requires both `--online` and `--confirm-network`; source code is not transmitted. See the OSV API documentation at https://google.github.io/osv.dev/api/.
- Comprehensive SAST/DAST, full data-flow analysis, exploit verification, legal license opinion or production certification is not included.

## Development and tests

```bash
python -m compileall -q core _dev/tests
PYTHONPATH=core python -m unittest discover -s _dev/tests -v
python -m pip wheel --no-deps --no-build-isolation . -w dist
```

The current suite contains 134 tests (9 skipped on Windows) and uses temporary repositories and fake local state. Network-facing Git-hosting API calls, real Docker execution, Windows installation, Android build/test/device execution, APK signature verification, large-repository performance and three reference-repository compatibility have not been certified by this build.

## Documentation

- `docs/UNIVERSAL_ENTERPRISE_MASTER_BLUEPRINT.md` — authoritative target architecture and delivery gates
- `docs/UNIVERSAL_AUDIT.md` — universal-layer audit, rollback and release recommendation
- `docs/LANGUAGE_SUPPORT_MATRIX.md` — per-engine detection and analysis coverage
- `docs/ANDROID_ENGINE.md` — Android scope, capability states and limitations
- `docs/ENGINE_DEVELOPMENT.md` — engine contract and extension guide
- `docs/CAPABILITY_MATRIX.md` — implemented versus planned capabilities
- `docs/SECURITY_MODEL.md` — threats, trust boundaries and guardrails
- `docs/ADAPTERS.md` — Git, agent, skill and tool adapter contracts
- `docs/ENTERPRISE_READINESS.md` — commercial and SaaS readiness gaps
- `docs/PROJECT_ROOT_INTEGRATION.md` — safe per-project integration
- `docs/AI_AGENT_GUIDE.md` — agent policy and workflows
- `docs/TEST_RESULTS.md` — current validation evidence

## Commercialization note

This source bundle is not yet a legally cleared or certified commercial release. Before selling it, complete trademark and ownership review, third-party license inventory, an attorney-reviewed EULA/privacy policy/DPA, signed release pipeline, independent penetration test, vulnerability-response process, support/SLA policy, export/privacy review and tax/payment compliance for the selling jurisdiction. Do not copy code from repositories with restrictive licenses into OGX without a separate license grant.


## Enterprise governance (0.7.0a1)

The current alpha adds the six-pillar enterprise operating model (Leadership, Direction, Management, Administration, Steering and Operations), organization-scoped objectives/initiatives/risks/decisions/changes/incidents/service metrics, role-governed membership management, audited status transitions and email-gated write approvals. See [`docs/ENTERPRISE_GOVERNANCE_AND_APPROVALS.md`](docs/ENTERPRISE_GOVERNANCE_AND_APPROVALS.md) for the full setup and security model.

High-impact project writes are email approval-gated by default. Configure Brevo transactional email or a standard SMTP-compatible mail relay, then use `ogx governance list`, `ogx governance approve` and `ogx governance execute`. Read-only project audits remain available without email configuration. The local-alpha identity subject values are not a substitute for OIDC/SAML authentication; production SaaS still requires an authenticated identity service, managed database, KMS, tenant isolation verification, backup/restore and security review.
