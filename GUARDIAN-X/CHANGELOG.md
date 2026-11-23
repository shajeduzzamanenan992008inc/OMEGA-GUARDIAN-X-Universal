# Changelog

## 0.8.0a1 - plug-and-play shield & dev core

Restructure (no change to scanner/report behaviour, rule ids or the `ogx` package/CLI names):
- One drop-in folder `omega-shield/` with a single entry point `init.py` (stdlib-only, cwd/`PYTHONPATH`-independent, per-host external state).
- `core/ogx` regrouped into capability packages `kernel`, `connectors`, `analysis`, `shield`, `devkit`, `governance`, `ecosystem`, `runtime`, `cli` with an enforced one-way dependency rule (`_dev/tests/test_architecture.py`) and a standard-library-only test.
- `ogx.kernel.paths` is the single source for names and exclusion sets (previously three diverging lists); the shield folder is always excluded from scans, detection, inventory and sandbox snapshots (fixes false "SQL" detection caused by the vendored `schema.sql`).
- `cli.py` (1,391 lines) split into `ogx/cli/{app,common}.py` and `ogx/cli/commands/*.py`.
- `integrate` is now an opt-in linker: nothing is created without `--with <groups>` (`copilot`, `agents`, `editors`, `deps`) and `--confirm`; the 30-file scatter and the `OMEGA GUARDIAN X/` (spaces) control folder are gone; manifest moved to `omega-shield/link-manifest.json`; approval payloads for `integration.apply` carry `groups`.
- Project policy moved to `omega-shield/shield.toml`; single `VERSION` file; `guardian evolve` recognises the new source layout.
- Kit assets moved to `devkit/` and `link/templates/`; `run_project_checks.py` resolves the host root itself and supports per-check `env`; build outputs, duplicate PowerShell scripts and wheels removed (`scripts/scan_many.py` added).

New (not part of the original engine):
- `ogx.runtime` request guard: hygiene limits, request-smuggling shape checks, attack signatures, IP policy, token-bucket rate limiting, API-key/role/IP access control, security headers; WSGI and ASGI middleware; experimental reverse proxy for applications in any language.
- `ogx.runtime.observe`: structured redacted JSON logging, de-duplicating error tracker (SQLite), debug utilities.
- CLI: `ogx guard check|test|keygen|proxy`, `ogx observe logs|errors|snapshot`; `init.py` verbs `scan audit detect doctor hooks guard proxy logs errors snapshot link status ogx`.
- Git hooks: `init.py hooks install|remove [--hook pre-commit|pre-push] --confirm` (never overwrites foreign hooks, honours `core.hooksPath`).

## 0.7.1a1

- Added private external-state `.env` configuration for mail approval and Git-hosting credentials.
- Added `ogx env init`, `ogx env path`, and `ogx env check`.
- Added safe dotenv parsing: known-key allowlist, no shell execution/expansion, OS environment precedence, symlink rejection, 128 KB bound, and file permission checks.
- Added `.env.example` and Git ignore protection; actual credentials are not included in release archives.
- Added tests for parser safety, external-only loading, permission warnings and configuration validation.
- Added an additive universal engine contract, registry and capability vocabulary with 51 unique engines: 1 Android engine + 50 spec-driven technology engines.
- Added a bounded read-only workspace, unified findings with stable IDs/provenance/verification state/deduplication, and a pipeline that writes reports only under external `OGX_HOME` state.
- Added `ogx engines list`, `ogx engines inspect`, `ogx analyze` and `ogx android`, with JSON/Markdown output and optional engine selection.
- Added Android static checks for manifests, Gradle, R8/shrinking, resources, sources, tests, Lint XML and APK/AAB ZIP metadata; build/test/device operations remain discovery-only and report `not_configured`/`not_attempted`.
- Extracted shared scanner rules into `ogx.rules`; existing scan output and CLI behavior remain compatible.
- Added 40 tests for engines, Android analysis, pipeline and CLI; full suite is 134 passed with 9 skipped.
- Updated the package readme reference to the existing `README-Guardian-X.md` without recreating the user-removed `README.md`.

# Changelog

## 0.2.0-alpha — External workspace architecture

- Removed project-local configuration and report writes from all CLI scan paths.
- Added OS-specific external OGX state directory and `OGX_HOME` override.
- Added external project registry (`ogx projects add/list`) and `ogx scan --project`.
- Changed `ogx start` to a safe read-only scan alias; `ogx init` initializes global OGX state only.
- Added Copilot instructions, custom Copilot agents, AGENTS/Codex/Claude/Gemini/Cursor/Windsurf guidance.
- Added Dependabot for Python dependencies and GitHub Actions.
- Added development requirements and stronger CI validation.
- Added regression tests for target-repository non-modification and external report storage.

## 0.1.0-alpha

- Initial universal project detection, basic dependency heuristics, secret-pattern checks, and JSON/Markdown reporting.


## 0.3.0-alpha — Project-root control integration
- Added explicit `ogx integrate plan/apply/status/remove` commands.
- Added a project-root `OMEGA GUARDIAN X/` control folder, governance policy, agent guidance, and PowerShell helper.
- Added GitHub Copilot instructions/agents and technology-aware Dependabot configuration only when target files are absent.
- Added root-level AGENTS/Claude/Gemini/Codex/Cursor/Windsurf guidance only when absent.
- Existing files are never overwritten; removal checks recorded SHA-256 hashes and preserves modified files.
- Normal scans remain read-only and keep reports in external OGX state.
- Added sequential Windows PowerShell helpers for multi-repository integration and scanning.


## 0.4.0a1 — Universal intelligence foundation
- Added bounded project inventory, multi-language framework indicators, static architecture/import mapping and Mermaid export.
- Added best-effort CycloneDX 1.5 manifest inventory, SPDX/license inventory and optional OSV.dev exact-version advisory lookup.
- Added Git remote parsing, explicit clone/branch/push operations, API adapter foundations for GitHub/GitLab/Bitbucket/Gitea/Forgejo, and draft PR/MR creation.
- Added agent runtime discovery, 16 declarative official skill packs, skill-manifest validation and external agent guidance.
- Added redacted log diagnostics, compatibility/toolchain reports, SARIF 2.1.0 export and evidence-based remediation plans.
- Added a restricted Docker snapshot runner with no network and no unsandboxed fallback.
- Added a local tenant-oriented SQLite data model and membership checks as a prototype, not production SaaS.
- Added capability matrix, enterprise readiness gates, security model and universal architecture blueprint.
- Added log/trace debug triage, static dependency impact analysis and regression-test plan generation (hypothesis/proposal only; no automatic source changes).


## 0.5.0a1 — Universal Guardian and governed evolution
- Added the consolidated `ogx guardian audit/maintain/evolve` workflow across static scan, inventory, architecture, compatibility, test/CI coverage and Git status.
- Added external Guardian JSON/Markdown reports and evidence-based maintenance priorities.
- Added opt-in upstream Git branch-head checks and explicit remote-ref-only fetch; no automatic merge, rebase or deploy.
- Added an allowlisted `.env` ignore repair with an external before-image, symlink/tracked-secret safeguards and explicit `--confirm`.
- Added root-level Renovate configuration alongside existing technology-aware Dependabot generation, preserving all pre-existing files.
- Added tests for guarded repairs, no-target-code execution, evolution proposals, audit reports and cross-host Git fetch behavior.
- Clarified that self-evolution means audited proposals and verified releases, not autonomous self-rewriting.


## 0.7.0a1 — Enterprise operating model and email-controlled change execution
- Added six enterprise governance pillars: Leadership, Direction, Management, Administration, Steering and Operations.
- Added organization-scoped objectives, initiatives, risks, decisions, changes, incidents and service metrics, with role checks, lifecycle transitions and audit events.
- Added membership administration safeguards, including last-owner protection and admin privilege boundaries.
- Added Brevo transactional-email and generic TLS SMTP adapters for one-time, time-limited, four-eyes write approvals.
- Added single-use approval workflow with hashed codes, expiry, failed-attempt lockout, request/event history and exact action revalidation before execution.
- Routed confirmed repair, integration and selected Git/platform writes through email approval by default.
- Added regression coverage for approval tampering, changed project/Git state, mail delivery failure, identity separation and six-pillar governance.
- Documented the local-alpha maturity boundary and production SSO, managed database, key management, audit and security requirements.
