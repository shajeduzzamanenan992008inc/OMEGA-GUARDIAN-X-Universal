# OGX Product Roadmap

Status reflects source code in **0.7.1a1**. Architecture ambitions are not implementation evidence.

## Universal engine layer — 0.7.1a1 (implemented and locally tested)
- [x] Additive engine contract with explicit capability states, failure isolation, execution/network assertions and per-engine byte budgets.
- [x] Engine registry with 51 unique IDs: 1 Android engine + 50 spec-driven language/technology engines.
- [x] Bounded, read-only workspace indexing; symlink/traversal refusal; JSONC helper; project fingerprinting.
- [x] Unified findings with stable IDs, engine provenance, verification state, deduplication and secret redaction.
- [x] `ogx engines list`, `ogx engines inspect`, `ogx analyze` and `ogx android`; external JSON/Markdown reports.
- [x] Android static analysis for manifests, Gradle, R8/shrinking, resources, sources, tests, Lint XML and APK/AAB ZIP metadata.
- [x] Build/test discovery and tool-presence checks without executing project code, Gradle, tests, adb or emulators.
- [x] 40 new tests; full suite 134 tests passed, 9 skipped.
- [ ] Android build and unit/instrumentation test execution in an approved isolated environment with SDK/Gradle.
- [ ] Cryptographic APK/AAB signature verification and binary AndroidManifest decoding.
- [ ] Device/emulator evidence collection with explicit authorization and no ambient credentials.

## Foundation — 0.5.0a1 (implemented and locally tested)
- [x] External engine, multi-project registry and guarded per-project instructions/control integration.
- [x] Bounded inventory, broad language/framework indicators and generic fallback.
- [x] Heuristic security/bug scanner, secret redaction, manifest review and SARIF output.
- [x] Best-effort CycloneDX manifest SBOM and license inventory.
- [x] Optional OSV.dev audit for exact manifest-declared versions, gated by explicit network opt-in.
- [x] Static architecture/import graph, impact estimate, log/trace triage, regression plan and remediation plan.
- [x] Git CLI operations, provider metadata and draft PR/MR adapter foundations.
- [x] Agent runtime discovery, 16 official declarative skill packs and schema validation.
- [x] Restricted Docker snapshot runner with no unsandboxed fallback.
- [x] Local SQLite organization/member/project/audit prototype.
- [x] Local unit tests (94 at the pre-engine baseline; 134 after the universal layer), compile validation and local-Git remote workflow tests.
- [x] Combined Guardian audit, maintenance priority engine, JSON/TOML/XML parse checks and evidence-based health signals.
- [x] Narrow allowlisted `.env` ignore repair with external backups, tracked-secret and symlink protections.
- [x] Opt-in upstream branch-head comparison and explicit Git fetch that does not merge or rewrite the worktree.
- [x] Dependabot plus Renovate repository configuration templates; hosted/self-hosted bot installation remains an operator task.
- [x] OGX self-audit/evolution proposal flow without autonomous source rewriting.

## Next — 0.6: Complete analysis adapters
- [ ] Full lockfile parsers and transitive dependency resolution by ecosystem.
- [ ] External analyzer adapters for Semgrep, Ruff, Bandit, Gitleaks, Trivy, Syft, Grype and OSV-Scanner with version/capability checks.
- [ ] More language-native AST/LSP/compiler graph support and dynamic/runtime trace import.
- [ ] Incremental scan cache, baselines, policy packs and SARIF suppression governance.
- [ ] More Git provider operations: issues, CI status/artifacts, checks and webhooks.

## Next — 0.7: Debugging and repair
- [ ] Interactive debugger adapters, test discovery and framework-specific reproduction.
- [ ] Disposable VM/OS sandbox providers and adversarial sandbox validation.
- [ ] Patch generation in isolated worktrees, diff review, regression verification and rollback.
- [ ] Agent task orchestration with normalized capability/permission contracts.

## Next — 0.8 / 0.9: Universal extension ecosystem
- [ ] Runtime SDK for skills/plugins with process isolation and signed publisher trust.
- [ ] IDE integrations and MCP server implementation.
- [ ] Skill marketplace, registry, compatibility and revocation services.

## Next — 0.10: Enterprise / SaaS control plane
- [ ] PostgreSQL migrations, API service, OIDC/SAML, MFA and production RBAC enforcement.
- [ ] Tenant isolation testing, key management, encryption, audit retention, billing and metering.
- [ ] Dashboard, monitoring, job queues, deployment automation, backup and disaster recovery.

## 1.0 commercial readiness gates
- [ ] Reproducible, signed and provenance-attested release pipeline.
- [ ] Cross-platform install/upgrade matrix and 4 GB RAM performance baseline.
- [ ] Independent penetration test, legal review, EULA/DPA/privacy/support terms.
- [ ] Customer support, vulnerability disclosure and incident-response processes.
- [ ] Evidence-based certification only after independent audits.

## Milestone H - Universal provider engine (implemented and locally tested)

- [x] Provider-neutral contract with nine explicit capability states and
      introspection-derived support claims.
- [x] Registry of 28 providers: 7 verified Git hosting adapters and 21
      descriptor-only family providers.
- [x] Tier 1 adapters for GitHub, GitLab, Bitbucket, Gitea and Forgejo; Azure
      DevOps and AWS CodeCommit (SigV4) in Tier 2.
- [x] Bounded transport: timeouts, response caps, rate-limit handling with
      `Retry-After`, no retry for unsafe writes, redirect refusal, host pinning.
- [x] SSRF and endpoint validation, per-provider credential isolation, error-body
      redaction and normalized error taxonomy.
- [x] Repository/project identity linking with duplicate-ownership prevention.
- [x] Approval-gated pull/merge request creation with an audit trail.
- [x] CLI: `ogx providers list|inspect|capabilities`,
      `ogx platform status|capabilities`, `ogx host discover`,
      `ogx remote inspect|activate`.
- [x] 48 deterministic mocked-provider tests; full suite 336 passed, 11 skipped.
- [ ] SourceHut GraphQL contract validation (stays descriptor-only until then).
- [ ] Operational workspaces, CI/CD, project-management, registry and cloud
      adapters (currently descriptor-only).
- [ ] Additional write operations: issues are implemented; labels, reviews,
      checks and webhooks are not.
- [ ] Real-credential integration runs against hosted services.
