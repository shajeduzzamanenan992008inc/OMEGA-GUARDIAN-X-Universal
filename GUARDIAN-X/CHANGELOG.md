# Changelog

## Unreleased — persistent observability, tracing, isolation and adversarial verification

- Added `src/ogx/api/metrics_store.py`: pluggable `MetricsStorage` interface with
  `InMemoryMetricsStorage` and bounded durable `SqliteMetricsStorage` (retention,
  max-rows, max-routes cardinality, concurrency-safe WAL writes, failure isolation,
  health reporting). `Metrics` hydrates from storage and persists writes.
- Added `src/ogx/api/tracing.py`: W3C trace context, propagation, contextvars,
  configurable Sampler, `OTLPExporter` interface with `NoOpExporter`, bounded
  in-memory `Tracer` with redaction, `AlertRule`/`AlertEvaluator`.
- `api/server.py`: `/ready` reports `observability_storage` as a non-critical check.
- Added tests: `test_metrics_store.py`, `test_tracing.py`,
  `test_enterprise_isolation.py`, `test_openapi_contract.py`,
  `test_backup_adversarial.py`, `test_performance_smoke.py`.
- Updated `docs/MASTER_COMPLETION_TRACKER.md` with dated evidence record.

## Unreleased — backup manifest signing and restore hardening

- Added `src/ogx/enterprise/backup_signing.py`: an explicit `SigningProvider` boundary and a
  concrete HMAC-SHA256 provider with a 0600 key file, `active`/`retired`/`revoked` key states,
  rotation that keeps old manifests verifiable, and `describe()` that never exposes secrets.
- Hardened `src/ogx/enterprise/backup.py`: strict versioned manifest schema (v2, v1 still
  verifies), separate reporting of hash integrity / AEAD authenticity / manifest signature,
  path-traversal, symlink, non-regular-file, oversize and artifact-name checks, a restore
  journal with `restore-recover` rollback, and retention that refuses to prune when no
  retained backup verifies. New flags/commands: `backup --sign`, `backup-verify
  --check-signature/--require-signature`, `restore --check-signature/--require-signature`,
  `restore-recover`, `signing-keys`, `signing-key-rotate`.
- Added `tests/test_backup_security.py` (11 tests: signature tamper, replaced artifact,
  missing/wrong/revoked key, rotation, forged algorithm, schema rejection, path safety,
  interrupted-restore recovery, prune refusal, no secret exposure).
- `api/server.py`: `/ready` now reports `database` and `schema` dependency checks separately
  and returns 503 with the missing tables; `api/observability.py` counts errors by public
  error code and renders `ogx_api_errors_total` in the Prometheus output.
- Docs: `BACKUP_AND_RESTORE.md` and `OBSERVABILITY.md` updated with the signing boundary,
  path safety, recovery and readiness semantics, plus honest limits.

## Unreleased — backup/restore and operational observability

- Added `src/ogx/enterprise/backup.py` (ENT-10): online consistent SQLite snapshot through the
  backup API, `PRAGMA integrity_check` proof, SHA-256/size manifest, optional 64 KiB-chunked
  ChaCha20-Poly1305 sealing through the `KMSProvider` boundary with chunk-index associated data,
  verified atomic restore (`os.replace`, `.pre-restore` safety copy, stale WAL sidecar cleanup)
  and retention pruning. New commands `ogx enterprise backup|backups|backup-verify|restore|
  backup-prune`.
- Added `src/ogx/api/observability.py` and `src/ogx/api/openapi.py` (ENT-08/ENT-09):
  thread-safe bounded metrics registry exposed at `/api/v1/metrics` (JSON or Prometheus text),
  request timing in the HTTP handler, and a generated OpenAPI 3.1 document at
  `/api/v1/openapi.json`.
- Added `tests/test_backup.py` (real restore, digest and AEAD tamper cases) and
  `tests/test_observability.py` (metrics, route labels, OpenAPI).
- Added `docs/BACKUP_AND_RESTORE.md`; updated the API document, tracker and report.

## Unreleased — enterprise authorization, REST API, encryption and cloud workspace host

- Added `src/ogx/enterprise/authz.py`: one deny-by-default policy engine shared by the CLI, REST
  API, dashboard and store helpers. Static 5-role/29-action matrix, explicit-deny precedence,
  tenant binding, service-scope intersection, break-glass with a mandatory reason and an optional
  audit sink. `ogx authz matrix|check|explain` exposes it.
- Added `src/ogx/enterprise/service_identity.py` and store tables/commands for tenant-scoped
  service accounts and scoped bearer tokens (`ogxst.<id>.<secret>`): SHA-256 at rest, one-time
  display, bounded expiry, revocation, account disablement, fingerprint-only listing and uniform
  authentication failures.
- Added `src/ogx/enterprise/crypto.py`: RFC 8439 ChaCha20-Poly1305 in portable Python checked
  against RFC vectors, a `KMSProvider` seam, a local 0600 keyring with rotation/revocation,
  purpose- and AAD-bound envelopes, plus `src/ogx/enterprise/secrets.py` for encrypted tenant
  secrets (`ogx enterprise secret-set|secret-get|secrets|kms-status|kms-rotate`).
- Added `src/ogx/api/`: authenticated `v1` REST API over loopback HTTP with bearer service
  tokens, policy-checked tenant routes, idempotency keys, bounded pagination and bodies,
  foreign-Origin refusal, per-token rate limiting, request ids and a uniform error schema; plus a
  dependency-free dashboard (overview, projects, hosts, governance, audit export, policy matrix)
  served as separate CSP-protected assets with the token held in memory only.
- Added `ogx serve` (loopback only) and `ogx host workspaces`.
- Added `src/ogx/hosts/adapters/cloud_runtime.py` (HOST-11): read-only GitHub Codespaces host
  adapter with pinned-host transport, credential isolation, strict response validation, redacted
  failures and honest capabilities (`workspace_read` only). Registered in `runtime_factory` and
  the capability matrix; Gitpod/Cloud9 remain descriptor-only.
- Added `tests/test_authz.py`, `tests/test_enterprise_identity.py`, `tests/test_crypto_kms.py`,
  `tests/test_api.py`, `tests/test_cloud_host.py` (81 tests). Canonical suite: **1182 passed,
  23 skipped, 119 subtests**; drop-in suite: 289 tests OK (9 skipped); wheel: 171 entries with
  the new modules and no test payload.
- Docs: new `docs/ENTERPRISE_API_AND_AUTHZ.md`, `docs/KMS_AND_ENCRYPTION.md`,
  `docs/HOST_CLOUD_WORKSPACE.md`, `docs/DOCUMENTATION_INDEX.md` (DOC-05 audit) and
  `docs/PRODUCTION_READINESS_ASSESSMENT.md` (PRD-01); `SECURITY_MODEL.md` gains TB-11/TB-12 and
  T-21..T-28 and the stale "ENT-02..06 not started" claim is corrected; `HOST_ARCHITECTURE.md`
  host status rows are reconciled with the adapters that actually exist.

## Unreleased — provider hosts and webhook verification

- Added `src/ogx/hosts/adapters/provider_runtime.py`: host lifecycle adapters for
  GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps and AWS CodeCommit, wrapping
  the canonical provider engine. Each host exposes the shared read surface
  (metadata, bounded file read, tree, branches, commits, pull requests) and one
  approval-gated pull/merge-request write. `runtime_factory` now constructs all
  seven verified Git hosts; branch/file/commit/push writes remain `unsupported`
  and are advertised as such.
- Added `src/ogx/platform/webhooks.py`: inbound webhook authenticity for GitHub,
  Bitbucket, Gitea and Forgejo (HMAC-SHA256) and GitLab (shared token), with
  constant-time comparison, rejected non-`bytes` bodies, a body-size bound, an
  event allowlist, a bounded TTL delivery-id replay guard and error messages
  that never contain the secret or the body. Azure DevOps and CodeCommit are
  refused (`unsupported_provider`) because they do not sign payloads.
- Added `HostType.AZURE_DEVOPS`, `HostType.AWS_CODECOMMIT` and the matching
  `ProviderType` members so `ogx host register --type ...` accepts them.
- Added `tests/test_webhooks.py` (29 tests) and
  `tests/test_host_provider_runtime.py` (47 tests). Updated
  `tests/test_host_lifecycle.py` so the unsupported-host case uses a remote
  workspace instead of GitLab. Canonical suite: 707 passed / 18 skipped;
  drop-in suite: 289 tests OK (9 skipped).
- Docs: `README.md`, `docs/HOST_ARCHITECTURE.md`,
  `docs/PROVIDER_ARCHITECTURE.md`, the tracker and this report now describe the
  provider hosts and the webhook verifier, including their limits.

## Unreleased — controlled patching, durable workflows and GitHub host

- Added `src/ogx/patching.py`: immutable patch plans, exact-single-match checks,
  SHA-256 preconditions, containment/symlink refusal, external backups, atomic
  writes and hash-chained audit. CLI: `ogx patch plan|apply|rollback|show|list`.
  Apply and rollback require `--confirm`; rollback refuses to overwrite edits
  made after the patch.
- Added `src/ogx/orchestrator.py`: SQLite-backed workflow state machine
  (`inspect → plan → edit → test → secure → review → evolve`) with validated
  transitions, per-stage checkpoints, resume, cancel, bounded retry,
  idempotency keys, recorded approvals for `edit`/`test`, event history and
  audit correlation. CLI: `ogx workflow create|run|status|list|events|approve|
  cancel|retry`. The `test` stage uses only the pinned-image container sandbox
  and fails closed without Docker.
- Added `src/ogx/hosts/adapters/github_runtime.py`: GitHub host read surface
  (metadata, bounded file read, tree, branches, commits, pull requests) with
  truth-derived capabilities, a mandatory API probe on connect, and one
  approval-gated draft-PR write through the existing validated plan/audit path.
  Branch creation, file update, push, merge, deployment and execution remain
  unsupported and are reported as such. `runtime_factory` now registers
  `github`; other providers still fail closed as unsupported host types.
- Fixed a defect found by the new tests: the orchestrator approval gate stored
  a timestamp in `current_stage` instead of the stage name. One cancel test was
  also corrected to accept intentionally skipped stages remaining `skipped`
  rather than requiring them to become `cancelled`.
- Fixed a containment bug found by Linux CI: patch planning resolved a path
  before checking its components for symlinks, which erased the symlink and let
  a symlinked target through. Components are now checked unresolved, followed by
  the resolved containment check, with a portable regression test.
- Fixed two reliability defects found by the new crash and benchmark checks.
  A killed run left the workflow permanently `running`; runs now hold a lease,
  refuse a concurrent live runner, and reclaim an expired lease by resetting
  only the interrupted stage. `--max-stages` now releases its lease instead of
  stranding the workflow. SQLite handles are now closed on exit (the context
  manager only committed), which had kept the database locked on Windows.
- Updated `tests/test_host_lifecycle.py`: GitHub is no longer the
  "unsupported host type" case; GitLab is.
- Verification (2026-11-27, branch based on `71fcbbc`): canonical suite 631
  passed / 18 skipped / 119 subtests; drop-in 289 tests OK (9 skipped);
  compileall and source/drop-in consistency pass; wheel 140 entries with the
  new modules. Code scanning 0 open / 13 closed; Dependabot and secret scanning
  0 open. Hosted CI at `71fcbbc` (OGX CI #84, Code Security #83, configuration
  validation #13) succeeded. Live provider, Docker, cross-drive and independent
  security verification remain outstanding — see
  `docs/MASTER_COMPLETION_TRACKER.md`.

## Unreleased — Universal identity, host identity and relocation engine

- Repaired the uncommitted `registry/v2.py` edit: fixed the `IndentationError`
  that made `import ogx` fail, replaced the `st_mtime` migration seed with a
  SHA-256 content digest, and made `patch_kinds` reflect the patches that
  actually ran. Migration now also reports `provisional_projects`.
- Added `src/ogx/pap/host_identity.py`: stable host id (UUID v5 from a
  privacy-conscious binding hash), `host.json` storage, `verified` / `weak` /
  `foreign_or_renamed` / `missing` decisions and explicit `adopt_host_identity`.
  No hardware serials or raw host names are persisted.
- Added `src/ogx/pap/lineage.py`: `explain_identity` (evidence, classification,
  conflicts) and `fork_project` (new identity + `.guardian-x/lineage.json`).
- Added `src/ogx/relocation.py`: plan/journal/apply/verify/recover/rollback with
  symlink and path-escape protection, hash-verified idempotent copy, registry
  workspace rebinding, and source retention unless a move is explicitly confirmed.
- `src/ogx/platform/identity.py`: `assert_relocation_safe` now also reports
  workspace resolution (`workspace_paths_resolve`).
- CLI: `identity explain`, `identity fork`, evidence-based `identity recover`,
  `host identity`, `relocate plan|apply|verify|recover`, and idempotent
  `host register`.
- CI: the canonical `tests/` suite, wheel build and clean-install smoke test now
  run in `.github/workflows/ci.yml` (previously only drop-in checks ran).
- Tests: 63 new tests collected across five files (62 pass here; 1 symlink test
  skips on Windows). Canonical suite: 578 passed, 17 skipped.
  Drop-in project checks: 2/2 passed.
- Documented compatibility decision: three P0 tests that asserted the *absence*
  of `identity explain`, `identity fork`, `relocate` and host-registration
  idempotency were inverted into positive tests. P0 fixtures and golden vectors
  are unchanged. Report: `docs/MEGA_ULTRA_COMPLETION_REPORT.md`.

## Unreleased — release-readiness fixes (0.8.0a1)

Host lifecycle & host engineering (v2)

- Added `src/ogx/hosts/lifecycle.py`: 12-state synchronous `HostSessionManager` with
  `HostSessionConfig`, `HostHealth`, live `HostSession`/`HostAdapter` contract,
  locked operations, process-local session manager, and audit logging.
- Added `src/ogx/hosts/adapters/filesystem_runtime.py`: scoped read-only local-filesystem
  host with strict containment, symlink-escape rejection and bounded reads/listings.
- Added `src/ogx/hosts/adapters/git_runtime.py`: read-only local-Git host using safe
  `subprocess.run` with bounded timeout and no remote/write commands.
- Added `src/ogx/hosts/adapters/runtime_factory.py`.
- Rewrote `src/ogx/hosts/capabilities.py` to truthful read-only values and added
  `REGISTERED`/`DISCONNECTED`/`FAILED` to `src/ogx/hosts/models.py`.
- `src/ogx/hosts/registry.py`: registration starts hosts at `registered`, not `active`.
- `src/ogx/cli.py`: `host connect/disconnect/health/recover/sessions/inspect/register`
  plus enhanced `host status` (live-state filtering), and registration now requires
  `--identifier` for local hosts. Fixed the missing `uuid4` import.
- Added tests: `tests/test_host_lifecycle.py` (13), `test_host_filesystem_runtime.py`
  (16/3 skipped), `test_host_git_runtime.py` (21), `test_host_cli_lifecycle.py` (6),
  `tests/test_host_contract.py` (12). Report: `docs/HOST_LIFECYCLE_VERIFICATION_REPORT.md`.

- Restored the PAP identity/manifest validators that commit `dbf8874` replaced with documentation-only constants while `identity.py` and `manifest_engine.py` still imported them. `import ogx` and the canonical test suite now collect again (340 passed).
- Removed the accidental 125-file `core/ogx/core/ogx/` nested duplicate and replaced the destructive `sync_core_from_src.py` generator with a read-only version/structure checker. It no longer copies or deletes feature code and supports `--check` and `--dry-run`.
- Architecture tests now validate both `src/ogx` and `core/ogx`: required packages/modules, no nested tree, no module/package collisions, import-time cycles, stdlib-only imports, version agreement, packaging declarations and clean imports of both packages. The `package == "core": continue` skip that hid the duplicate is gone.
- Moved the legacy project-folder workflow to `ogx.project_integration` and kept compatibility re-exports in `ogx.integration`, removing the ambiguous module/package name collision.
- Removed the undeclared `tomli_w` runtime dependency from discovery-root persistence; the fixed `[[roots]]` shape is now written with a stdlib serializer and covered by a regression test.
- Bounded the real HTTP transport read to `max_response_bytes + 1`, preventing a hostile endpoint from exhausting memory before the size check; added a regression test.
- Corrected CI paths to `working-directory: GUARDIAN-X`, added a Python 3.11/3.12/3.13 matrix, packaging, clean-install, drop-in and profile jobs, zero-collection guards, least-privilege permissions and pinned action commits. Corrected the config-validation workflow paths.
- Changed the CI profile commands from `python3` to `python` so the declared Windows/macOS/Linux profile runs on a host without a `python3` alias; GitHub Actions resolves both to the setup-python interpreter.
- Unified all version sources at `0.8.0a1`; removed stale captured test logs; the drop-in zip now excludes `src/`, tests, release material, CI, local env files and databases.

## Unreleased — Milestone G project intelligence

- Added `ogx.intelligence`, a static read-only project intelligence engine with language, framework/technology, dependency, structure, entry-point, build-system, testing, documentation, configuration, architecture and engineering-health analyzers.
- Added `ogx intelligence analyze` with project ID/name/path targeting, terminal/JSON/Markdown/both output, section filters, exclusions and bounded resource options. Reports are recomputed and external `both` artifacts are written only under `OGX_HOME`.
- Added read-only identity and Registry V2 association: existing identity and workspace/project records are reused; intelligence never registers or duplicates a project.
- Added evidence/confidence/limitation reporting, transparent indicator-coverage health scoring and secret-value-safe configuration reporting.
- Added deterministic tests for models, ecosystems, monorepos, malformed manifests, generated/vendored files, limits, symlinks, identity relocation, registry association and CLI output.
- Documentation: `docs/INTELLIGENCE_ENGINE.md`.

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

## Unreleased — Docker host adapter (HOST-09)

- Added `src/ogx/hosts/adapters/docker_runtime.py` (`DockerHostAdapter`): read-only Docker host adapter driving the local Docker CLI with `shell=False`, bounded timeouts/output, identifier validation, path-traversal rejection and redacted errors. Supports daemon validation, container/image listing and inspection, and bounded container file reads via `docker cp` with tar parsing. Registered in `runtime_factory.py` as `docker`; `HostType.DOCKER` and a capability-matrix entry added. Mutating operations (start/stop/rm/build/pull/exec, mounts, networks, volumes) and privileged containers are intentionally not implemented. Live Docker verification is BLOCKED (no daemon available).
- Added `tests/test_host_docker_runtime.py` (29 tests, mocked transport).
