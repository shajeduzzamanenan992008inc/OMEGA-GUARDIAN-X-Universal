# OMEGA-GUARDIAN-X Universal — Mega Remaining Work List

**Audit date:** 2026-11-27  
**Repository:** https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal  
**Audited ref:** `main`  
**Observed HEAD:** `df618f80f879f103020b457f82e2c7c923068fec` (`df618f8`)  
**Visibility:** Public  
**Remote branches observed:** `main` only  
**Open pull requests observed:** 0  
**Primary source of requirement status:** `GUARDIAN-X/docs/MASTER_COMPLETION_TRACKER.md`  
**Companion report:** `GUARDIAN-X/docs/MASTER_COMPLETION_REPORT.md`

> This is a remote GitHub audit, not a local-workspace audit. The local directory
> `D:\FTP\OMEGA-GUARDIAN-X-Universal` was not accessible during this inspection.
> Any unpushed local work (including a possible SSH adapter branch) must be
> inspected and preserved by the local IDE/terminal agent before starting work.

---

## 1. Executive result

**Classification: DEVELOPMENT INCOMPLETE.**

The public `main` branch contains meaningful implemented and tested foundations:
- Local filesystem and local Git read-only host adapters.
- GitHub host read operations and approval-gated writes for branch creation,
  file updates, multi-file commits, fast-forward ref updates and draft PR creation.
- Six additional Git-provider adapters with read support and approval-gated
  pull/merge-request creation, tested with mocked transport.
- Durable workflow orchestration, patch planning/application/rollback,
  approval gates, audit correlation and lease-based crash recovery.
- Project identity, registry, relocation journal and local relocation tests.
- Security scanning/configuration, bounded transport, secret redaction and
  documented sandbox controls.

However, the tracker still explicitly records incomplete, partial, descriptor-only,
unsupported or externally blocked requirements across hosts, security, identity,
AI engineering, plugins, enterprise services, interfaces, testing, performance,
release engineering and production readiness.

The tracker is an evidence ledger, not proof that every listed capability is
operational. `TESTED` commonly means mocked transport or a limited local OS;
it does not mean live-provider or production verification.

## 2. Fresh GitHub repository observations

| Item | Observed result |
|---|---|
| Default branch | `main` |
| HEAD | `df618f80f879f103020b457f82e2c7c923068fec` |
| Latest observed commit | Merge PR #25, “Reconcile stale docs/tracker claims after GitHub write lifecycle” |
| Repository visibility | Public |
| Remote branches | Only `main` returned by branch search |
| Open PRs | None returned |
| Recent merged work | PR #25 documentation reconciliation; PR #24 GitHub write lifecycle; PR #22 provider Git hosts/webhooks; PR #21 lease/crash recovery and measured baselines |
| Tracker path | `GUARDIAN-X/docs/MASTER_COMPLETION_TRACKER.md` |
| Report path | `GUARDIAN-X/docs/MASTER_COMPLETION_REPORT.md` |

### Important documentation/accounting inconsistencies

1. The tracker header still describes an earlier branch/snapshot and older merge
   context, although current `main` is `df618f8` and PR #25 is merged.
2. The completion report says the tracker has 140 requirement rows, while its
   status-count table totals 146. The report also contains earlier verification
   figures and historical descriptions that are not fully synchronized with
   the current tracker.
3. The tracker’s top-level evidence section records 754 passed, 18 skipped and
   119 subtests, while the companion report contains older suite figures in
   earlier sections. Treat the current tracker’s evidence section as the newer
   recorded snapshot, but rerun tests before making a new claim.
4. The tracker says HOST-08/09/10/11 are descriptor-only. No corresponding
   remote feature branches were observed on GitHub. Local unpushed work is
   outside this remote audit.
5. The completion report’s “Remaining known work” section is useful but is not
   a substitute for the detailed requirement rows below.

These inconsistencies should be reconciled in a future documentation pass;
do not silently rewrite historical evidence or erase old requirement rows.

---

## 3. Mega list — all tracked incomplete requirements

The following items are taken from the current tracker’s non-complete statuses
(`PARTIAL`, `DESCRIPTOR_ONLY`, `UNSUPPORTED`, `BLOCKED`, and `IMPLEMENTED` where
fresh end-to-end evidence is still missing). The status shown is the tracker’s
recorded status, not a fresh execution performed during this remote audit.

### A. Security and trust boundaries

#### SEC-12 — Container sandbox for project execution
**Tracker status: PARTIAL**

Remaining work:
- Execute the existing pinned-image sandbox against an available Docker
  installation and immutable image digest.
- Verify no implicit image pull, network disabled, read-only container root,
  dropped capabilities, `no-new-privileges`, CPU/memory/process limits and
  bounded execution time.
- Verify snapshot creation excludes credentials, home directories, Docker
  socket, unrelated repositories and sensitive environment variables.
- Test cleanup after success, timeout, cancellation, crash and daemon failure.
- Add a documented live-verification record; keep fail-closed behaviour when
  Docker/image is absent.
- Do not describe this as a formally verified VM or complete OS isolation.

#### SEC-14 — Independent security review
**Tracker status: BLOCKED**

Remaining work:
- Arrange an independent reviewer with relevant application-security and
  supply-chain experience.
- Provide a scoped threat model, architecture, test evidence and redacted
  review environment.
- Record findings, severity, reproduction steps, remediation and retest.
- Close only with reviewer evidence; internal self-review is not a substitute.

#### SEC-15 — Threat model
**Tracker status: PARTIAL**

Remaining work:
- Complete and maintain threat modelling for all trust boundaries: local host,
  GitHub/provider hosts, plugin runtime, sandbox, identity/relocation, tenant
  data, API/dashboard, queue/workers, credentials and release pipeline.
- Add attack trees, abuse cases, trust-boundary diagrams, assets, attacker
  capabilities, mitigations, residual risks and verification mapping.
- Include lease expiry/race cases, webhook replay, provider token compromise,
  cross-tenant access, malicious repository content and supply-chain attacks.
- Link every material threat to tests or explicitly documented residual risk.
- Distinguish documented controls from controls actually enforced in code.

### B. Universal identity and relocation

#### ID-10 — C: → D: relocation
**Tracker status: BLOCKED**

Remaining work:
- Execute on two real local volumes.
- Test plan/apply/verify/recover/rollback with identity preservation.
- Test same-volume rename, cross-volume copy, source-retained copy, interrupted
  transfer, partial destination, collision and permission failure.
- Verify path-independent PID, correct WID/HID handling and registry
  reconciliation after moving the project.
- Record exact OS, filesystem and test evidence.

#### ID-11 — USB/removable-drive relocation
**Tracker status: BLOCKED**

Remaining work:
- Execute on a real removable drive.
- Test drive-letter changes, mount-path changes, disconnect during copy,
  reconnect, read-only media, insufficient space and filesystem limitations.
- Verify project identity survives relocation and no absolute drive path is
  treated as permanent identity.
- Test recovery and duplicate detection after reconnect.

#### ID-12 — Machine-to-machine migration
**Tracker status: BLOCKED**

Remaining work:
- Execute migration on a second physical or virtual machine.
- Verify identity import/adoption, host identity change, workspace identity,
  registry reconciliation, permissions, path changes and conflict handling.
- Test missing dependencies, different OS/user profiles and interrupted
  migration.
- Document what is portable and what is intentionally host-local.

#### ID-13 — Local ↔ GitHub end-to-end identity
**Tracker status: BLOCKED**

Remaining work:
- Configure an authorized, least-privilege GitHub credential in the local
  environment (never commit or expose it).
- Exercise local project registration, remote repository linking, duplicate
  detection, remote clone/import, reconciliation and identity explainability.
- Test repository rename/URL change, fork, clone, detached local copy,
  conflicting PID and deleted/recreated remote.
- Confirm no accidental identity merge or silent fork.

#### ID-14 — Signed identity attestation
**Tracker status: UNSUPPORTED**

Remaining work:
- Decide whether signed identity attestation is required or remains an
  explicitly deferred optional capability.
- If approved, design key ownership, key rotation, verification, revocation,
  offline operation and recovery before implementation.
- Never invent, generate or use production signing keys without owner
  authorization.
- Keep the capability reported unsupported until implemented and tested.

#### ID-15 — Owner ratification of D1–D12
**Tracker status: BLOCKED**

Remaining work:
- Obtain explicit owner decisions for D1–D12 in the tracker’s decision
  register.
- Preserve recommendations as recommendations until approved.
- Do not silently activate state-layout v2, schema v3, new machine-binding
  semantics, changed copy/move defaults, or changed identity conflict policy.
- Record each decision, rationale, date and migration compatibility impact.

### C. Universal host engine and remote execution hosts

#### HOST-08 — Remote SSH host
**Tracker status: DESCRIPTOR_ONLY**

Remaining work:
- Inspect local workspace first for any unpushed `SshHostAdapter` work and
  preserve it; remote GitHub branch search showed only `main`.
- If no valid local implementation exists, implement a real SSH runtime
  adapter integrated with the canonical host lifecycle and runtime factory.
- Use explicit host-key verification (`StrictHostKeyChecking=yes`), a pinned
  known-hosts source, key-based authentication, bounded subprocess execution,
  argument arrays (no shell interpolation), timeouts and output caps.
- Add safe remote identity, health, metadata and read operations.
- Refuse unknown host keys, unsafe paths, credential leakage and arbitrary
  command execution.
- Add mocked contract/security tests and perform live verification only with
  an explicitly authorized non-production SSH target.
- Synchronize canonical `src` and drop-in `core` copies where required.

#### HOST-09 — Docker host
**Tracker status: DESCRIPTOR_ONLY**

Remaining work:
- Implement a real Docker runtime adapter, not only a family descriptor.
- Use a bounded Docker CLI/API boundary with explicit daemon/context selection.
- Default to read-only inspection; expose container/image/network/volume
  metadata without leaking environment secrets or mounted file contents.
- Enforce safe resource bounds and refuse privileged containers, host-root
  mounts, Docker socket exposure and unsafe bind mounts by default.
- Separate inspect/start/stop/remove/build/exec capabilities and require
  explicit validated plans and approval for mutations.
- Never execute arbitrary project code on the host as a fallback.
- Add unit, mocked integration, security and failure-path tests.
- Run live tests only against an authorized disposable Docker environment.

#### HOST-10 — Kubernetes host
**Tracker status: DESCRIPTOR_ONLY**

Remaining work:
- Implement a real `KubernetesHostAdapter` and register it in the runtime
  factory, host/provider enums and capability matrix.
- Use a safe bounded `kubectl` boundary or a justified client library; no shell
  interpolation.
- Pin and validate the selected kubeconfig/context; enforce TLS verification;
  never enable insecure-skip-tls.
- Read-only by default. Safely inspect namespaces, pods, deployments,
  ReplicaSets, StatefulSets, DaemonSets, Jobs, CronJobs, Services, Ingress,
  NetworkPolicy metadata, ConfigMap metadata, Secret metadata only, PV/PVC and
  StorageClass metadata.
- Never read or display Secret values.
- Apply namespace allowlists and explicit cluster-scope policy; Kubernetes RBAC
  remains authoritative.
- No `kubectl exec`, arbitrary shell, port-forwarding, Helm or mutation unless a
  separately reviewed and approval-gated capability is explicitly added.
- Add mocked contract/security tests; perform live checks only with an
  authorized non-production cluster.

#### HOST-11 — Cloud workspace hosts
**Tracker status: DESCRIPTOR_ONLY**

Remaining work:
- Select and explicitly scope providers (for example Codespaces/Gitpod or
  another owner-approved workspace provider).
- Implement real authentication, workspace discovery, metadata, lifecycle,
  health and bounded file operations where supported.
- Derive capabilities from actual provider operations; unsupported actions
  must remain unsupported.
- Add per-provider credential isolation, bounded transport, audit, approval
  gates and mocked contract tests.
- Verify live against authorized non-production accounts.
- Do not claim generic “cloud host” support based only on a descriptor.

#### HOST-13 — Cross-process failover/reconnect
**Tracker status: UNSUPPORTED**

Remaining work:
- Design durable host-session ownership, leases, fencing tokens and recovery.
- Prevent two processes from concurrently mutating the same host session.
- Define stale-session recovery, process crash, lease expiry, network partition
  and credential revocation behaviour.
- Add cross-process tests (not only threads), race tests and crash injection.
- Document limitations of local filesystem locking and distributed state.

#### HOST-14 — Live GitHub host verification
**Tracker status: BLOCKED**

Remaining work:
- Use an owner-authorized GitHub token with minimum required scopes.
- Verify authenticated read operations against a test repository.
- Verify only the already implemented approval-gated writes on a disposable
  test branch/repository; never test destructive operations on production data.
- Capture redacted request/response evidence, permission-denied paths, rate
  limiting and credential redaction.
- Do not claim live verification from mocked transport or an unauthenticated
  `credential_required` refusal.

#### HOST-15 — GitHub host CLI registration
**Tracker status: IMPLEMENTED**

Remaining work:
- Run current CLI registration/connect/list/status workflows on the current
  `main` checkout and confirm current evidence.
- Keep status distinct from live provider verification (`HOST-14`).

#### Additional provider-host gap — non-GitHub write operations
**Documented limitation; HOST-07 remains TESTED**

Remaining work:
- Six non-GitHub provider adapters currently have read surfaces and one
  approval-gated PR/MR operation, but branch creation, file writes, commit
  creation and push remain unsupported.
- For each provider, implement only operations supported by its API and
  security model; use validated plans, explicit approval, stale-ref checks and
  audit records.
- Add per-provider contract tests and live verification separately.
- Never emulate an operation that the provider does not actually support.

### D. Provider ecosystem

#### PROV-05 — Live provider integrations
**Tracker status: BLOCKED**

Remaining work:
- Configure authorized non-production accounts and per-provider credentials
  for GitHub, GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps and CodeCommit.
- Run bounded authenticated health/read/permission probes and supported
  approval-gated write probes.
- Verify rate-limit handling, permission-denied responses, timeouts, host
  pinning, redirects, pagination and redaction.
- Record each provider independently; one live provider does not verify all.

#### PROV-07 — Token refresh/rotation
**Tracker status: UNSUPPORTED**

Remaining work:
- Design secure credential refresh/rotation per provider.
- Keep secrets outside repositories and registry data.
- Define expiry, refresh failure, revocation, overlap windows, audit and
  operator recovery.
- Add tests for expired, revoked, malformed and rotated credentials.
- Do not log tokens or include them in error messages.

### E. AI engineering core

#### AI-04 — Generic automated patch generation from findings
**Tracker status: PARTIAL**

Remaining work:
- Expand beyond the narrow `.env` ignore fix to a general finding-to-patch-plan
  pipeline.
- Require exact-match hunks, pre-image hashes, path containment, symlink
  refusal, bounded file sizes and project-root checks.
- Produce explainable patch rationale, affected files, risk score, tests and
  rollback plan.
- Never auto-apply generated changes; require explicit approval.
- Add adversarial tests for ambiguous matches, stale hashes, path traversal,
  symlink swaps, malformed plans and partial failures.

#### AI-05 — Sandboxed test execution
**Tracker status: PARTIAL**

Remaining work:
- Complete live Docker sandbox verification described in SEC-12.
- Add test profiles, bounded output, timeouts, cancellation, resource caps,
  deterministic environment and cleanup verification.
- Ensure no network/host credential access and no host workspace mutation.
- Preserve fail-closed behaviour when Docker or the pinned image is missing.
- Add tests for daemon failure, timeout, cancellation and interrupted cleanup.

#### AI-07 — Review with diff/risk/approval
**Tracker status: PARTIAL**

Remaining work:
- Provide a complete review artifact: unified diff, changed-file list, rationale,
  risk/severity, security impact, test results, compatibility impact and
  rollback details.
- Require a human reviewer/approver for sensitive changes.
- Bind approval to the exact immutable patch-plan hash and repository revision.
- Reject stale approvals after the plan or base revision changes.
- Add CLI/API-ready structured output; dashboard presentation is tracked under
  UI-05.

#### AI-08 — Evolution with versioned changes and rollback
**Tracker status: PARTIAL**

Remaining work:
- Implement a versioned evolution/migration engine rather than only workflow
  summaries and patch audit.
- Define migration manifests, preconditions, checkpoints, compatibility,
  idempotency, rollback/forward-recovery and schema version policy.
- Integrate with Git history and patch plans without silently changing owner
  decisions D1–D12.
- Test interrupted migrations, partial state, downgrade refusal and recovery.

#### AI-10 — LLM code generation
**Tracker status: UNSUPPORTED**

Remaining work:
- Decide supported model/provider architecture and owner-approved privacy
  policy.
- Implement an explicit LLM integration boundary with timeouts, token/output
  bounds, retry policy and provider credential isolation.
- Treat repository content and model output as untrusted data.
- Require patch plans, tests, security checks and human approval; never treat
  generated code as verified.
- Add offline/mock tests and separately authorized live provider tests.
- Clearly disclose when no model is configured.

### F. Lifecycle orchestrator

#### ORC-13 — Real edit + test end-to-end
**Tracker status: BLOCKED**

Remaining work:
- Run a real workflow using a controlled patch plan and the pinned Docker
  sandbox image.
- Verify inspect → plan → approved edit → sandboxed test → secure → review →
  evolve with durable checkpoints and audit correlation.
- Test failed test, cancellation, crash, lease expiry, resume and rollback.
- Capture the exact environment and outputs; mocked stage tests do not satisfy
  this requirement.

### G. Agent and plugin runtime

#### PLG-02 — Plugin execution lifecycle
**Tracker status: UNSUPPORTED**

Remaining work:
- Define plugin install/validate/enable/disable/run/stop/update/uninstall
  lifecycle and state transitions.
- Add manifest schema, compatibility checks, dependency policy, lifecycle
  events, audit and failure recovery.
- Keep declarative skills separate from executable plugins.
- Refuse execution until trust, permission and isolation checks pass.

#### PLG-03 — Signature verification and publisher trust
**Tracker status: UNSUPPORTED**

Remaining work:
- Define signed package format, trusted publisher roots, signature validation,
  key rotation, revocation and offline verification.
- Bind signatures to immutable artifact digests and metadata.
- Reject unsigned, tampered, expired or revoked packages by default.
- Add negative tests and a documented trust-root bootstrap procedure.
- Do not invent production signing keys or mark packages trusted without
  explicit owner policy.

#### PLG-04 — Plugin revocation and permissions
**Tracker status: UNSUPPORTED**

Remaining work:
- Implement least-privilege capability declarations and per-plugin grants.
- Add permission review, approval, revocation, expiry and audit.
- Revoke running sessions when permissions or trust are withdrawn.
- Prevent privilege escalation through dependency/plugin chaining.
- Test denied, revoked, expired and cross-project permission scenarios.

#### PLG-05 — Plugin isolation and resource limits
**Tracker status: UNSUPPORTED**

Remaining work:
- Choose and implement a defensible sandbox boundary for plugin execution.
- Apply CPU, memory, process, file, network and execution-time limits.
- Prevent host filesystem, credentials, Docker socket and unrelated project
  access by default.
- Add escape-oriented negative tests and crash cleanup.
- Document residual host-kernel/runtime trust; do not overclaim isolation.

#### PLG-06 — Third-party skill execution
**Tracker status: ACCEPTED**

This is currently disabled by design. Do not change it without explicit owner
approval and completion of PLG-02 through PLG-05. Keep declarative inspection
separate from arbitrary code execution.

### H. Enterprise backend

#### ENT-02 — PostgreSQL and migrations
**Tracker status: UNSUPPORTED**

Remaining work:
- Add a PostgreSQL-backed store and versioned migration framework.
- Preserve SQLite for local mode if required; avoid duplicating business rules.
- Test transaction boundaries, concurrent writes, indexes, rollback and upgrade
  from existing local state.
- Add connection pooling, timeout and least-privilege DB roles.
- Verify against an authorized non-production PostgreSQL instance.

#### ENT-03 — OIDC/SAML/MFA
**Tracker status: UNSUPPORTED**

Remaining work:
- Implement real authentication; subject strings are not authentication.
- Select OIDC and/or SAML support and define MFA enforcement policy.
- Validate issuer, audience, signatures, nonce/state, redirect URI and token
  expiry; prevent replay and session fixation.
- Add login/logout, session expiry, account linking and recovery.
- Test IdP outage, invalid signatures, expired tokens and role mapping.
- Verify with a non-production identity provider.

#### ENT-04 — RBAC
**Tracker status: PARTIAL**

Remaining work:
- Move from local role labels to centralized, enforced authorization checks.
- Define resource/action matrix for organization, project, host, workflow,
  credentials, audit, plugin and administration operations.
- Enforce deny-by-default and least privilege at every API/service boundary.
- Add role inheritance/assignment policy only if explicitly designed.
- Test horizontal/vertical privilege escalation and permission revocation.

#### ENT-05 — Service identities and tenant isolation
**Tracker status: PARTIAL**

Remaining work:
- Implement non-human service identities, credential lifecycle and scoped
  permissions.
- Enforce tenant isolation in every query, cache, queue message, audit record,
  object store key and background job.
- Add cross-tenant negative tests and isolation tests under concurrency.
- Define tenant provisioning/deprovisioning and service identity revocation.

#### ENT-06 — Encryption, KMS and key rotation
**Tracker status: UNSUPPORTED**

Remaining work:
- Define encryption at rest and in transit for sensitive persisted data.
- Integrate an owner-approved KMS/secrets provider.
- Implement envelope encryption, key versioning, rotation, revocation and
  recovery.
- Never store plaintext credentials in project files or logs.
- Test unavailable KMS, expired keys, rotation and recovery.
- Keep local development mode explicit and appropriately scoped.

#### ENT-07 — Job queue, workers and DLQ
**Tracker status: UNSUPPORTED**

Remaining work:
- Implement durable asynchronous jobs, worker lifecycle and dead-letter queue.
- Define idempotency, retries, backoff, visibility timeout, poison-message
  handling and operator replay.
- Add bounded concurrency, cancellation, graceful shutdown and backpressure.
- Ensure tenant authorization is rechecked at execution time.
- Test duplicate delivery, worker crash, queue outage and DLQ recovery.

#### ENT-08 — Durable REST API
**Tracker status: UNSUPPORTED**

Remaining work:
- Design versioned REST endpoints for projects, hosts, identity, workflows,
  patches, approvals, audit and administration.
- Add authentication, RBAC, tenant scoping, input validation, rate limits,
  request IDs, pagination and bounded payloads.
- Ensure writes use the same approval, capability and audit path as the CLI.
- Publish OpenAPI documentation and contract tests.
- Add API compatibility and migration policy.

#### ENT-09 — Monitoring, metrics and tracing
**Tracker status: PARTIAL**

Remaining work:
- Add structured operational metrics, distributed tracing and health/readiness
  endpoints.
- Instrument API, workers, database, provider transport, sandbox and workflow
  stages.
- Redact secrets and sensitive project data from telemetry.
- Define service-level indicators, alert thresholds and retention policy.
- Test telemetry failure does not break core safety controls.

#### ENT-10 — Backup, restore and disaster recovery
**Tracker status: PARTIAL**

Remaining work:
- Extend beyond registry backup/recover to all durable state: database,
  identity, audit, workflow, artifacts, configuration and required metadata.
- Define encrypted backups, retention, integrity checks and off-host storage.
- Implement restore validation, point-in-time recovery where supported, RPO/RTO
  targets and documented disaster-recovery runbooks.
- Test full restore into a clean environment and verify identities/audit chain.
- Do not claim DR readiness based only on registry backup tests.

### I. User interfaces

#### UI-04 — Authenticated REST API
**Tracker status: UNSUPPORTED**

Remaining work:
- Coordinate with ENT-08; avoid building a second independent API/auth stack.
- Add authenticated, tenant-scoped endpoints for the supported CLI workflows.
- Enforce the same capability checks, approval gates and audit events.
- Add API contract, authz-negative, rate-limit and compatibility tests.

#### UI-05 — Dashboard
**Tracker status: UNSUPPORTED**

Remaining work:
- Build a dashboard for projects, hosts, workflow progress, diffs, risk,
  approvals, audit and operational health.
- Use the authenticated API; do not bypass backend authorization.
- Make unsupported/unverified capabilities visibly distinct from operational
  ones.
- Add accessible UI, loading/error states, session handling and security tests.
- Do not present mock data as live project state.

#### UI-06 — Unified authorization across CLI/API/dashboard
**Tracker status: PARTIAL**

Remaining work:
- Centralize authorization policy and enforce it consistently in CLI, API,
  dashboard and background workers.
- Bind approvals to user/service identity, resource, operation, exact plan hash
  and expiry.
- Add parity tests proving one interface cannot bypass another interface’s
  policy.

### J. Testing and compatibility

#### TST-06 — macOS
**Tracker status: UNSUPPORTED**

Remaining work:
- Add macOS CI coverage for supported Python versions.
- Verify path handling, permissions, symlinks, subprocess behaviour, SQLite,
  packaging and CLI.
- Record any platform-specific exclusions explicitly.

#### TST-07 — Live provider integration tests
**Tracker status: BLOCKED**

Remaining work:
- Use authorized test accounts and least-privilege credentials.
- Run a provider-specific test matrix; no single provider stands in for all.
- Cover health, reads, permissions, supported writes, rate limits, failure
  handling, secret redaction and cleanup.
- Keep secrets in CI secret storage and redact all evidence.

#### TST-11 — Cross-drive/USB relocation
**Tracker status: BLOCKED**

Remaining work:
- Execute the relocation matrix on actual second volume and removable media.
- Verify identity, recovery, rollback, path changes and conflict handling.
- Coordinate with ID-10 and ID-11; avoid duplicate or contradictory tests.

### K. Performance and reliability

#### PRF-03 — Queue backpressure
**Tracker status: UNSUPPORTED**

Remaining work:
- Implement bounded queue capacity, admission control and producer backpressure.
- Define overload responses, fairness, per-tenant quotas and cancellation.
- Test saturation, slow workers, queue outage, retry storms and recovery.
- Measure throughput/latency and document operational tuning.

#### PRF-04 — Caching and incremental analysis
**Tracker status: PARTIAL**

Remaining work:
- Add safe content-hash-based caching and incremental scan/analysis.
- Invalidate cache on config, tool version, rule set, dependency or file changes.
- Avoid cross-project and cross-tenant cache leakage.
- Add correctness tests proving cached results match full scans.
- Benchmark large repositories and monorepos; retain full-scan fallback.

#### PRF-05 — Large-repository behaviour
**Tracker status: TESTED**

No tracked implementation gap is asserted here. Remaining validation:
- Extend synthetic 1,021-file evidence to realistic large repositories and
  monorepos.
- Measure wall time, peak memory, file-size extremes, concurrency and cache
  effectiveness.
- Treat this as further capacity evidence, not as a currently incomplete
  requirement.

#### PRF-06 — Memory/database performance
**Tracker status: TESTED**

No tracked implementation gap is asserted here. Remaining validation:
- Add multi-process and concurrent-throughput measurements when the backend
  supports them.
- Keep benchmark environment and data set reproducible.

### L. Documentation and release engineering

#### DOC-05 — Historical documentation audit
**Tracker status: PARTIAL**

Remaining work:
- Audit historical milestone reports, architecture docs, README, changelog,
  host/provider docs and status tables.
- Mark historical snapshots with their commit/date and avoid presenting them
  as current.
- Reconcile stale branch names, HEAD SHAs, test counts, requirement totals and
  capability claims.
- Preserve historical evidence; add superseding corrections rather than
  silently rewriting history.
- Verify source/drop-in synchronization and all documentation links.

#### REL-02 — Artifact checksums
**Tracker status: PARTIAL**

Remaining work:
- Automate SHA-256 (and any approved stronger digest) generation for every
  release artifact.
- Publish checksum manifests tied to exact artifact names and versions.
- Verify checksums after download and in CI.
- Do not rely on a manually copied terminal hash.

#### REL-04 — Artifact signing and provenance
**Tracker status: UNSUPPORTED**

Remaining work:
- Define signing identity, key custody, rotation, revocation and provenance
  format.
- Generate attestations for source revision, build workflow, dependencies and
  artifact digest.
- Verify signatures and attestations before release.
- Requires explicit owner approval and authorized signing infrastructure;
  never create/use production signing keys without authorization.

#### REL-05 — Reproducible builds
**Tracker status: UNSUPPORTED**

Remaining work:
- Pin build toolchain and dependencies.
- Normalize timestamps, archive ordering, file metadata and environment inputs.
- Build independently in clean environments and compare artifact hashes.
- Document any unavoidable nondeterminism and eliminate it where possible.

#### REL-07 — Publish a release
**Tracker status: BLOCKED**

Remaining work:
- Requires explicit owner authorization.
- Complete release checklist, changelog, compatibility, security review,
  checksums, signing/provenance and reproducibility evidence first.
- Do not publish, tag or create a release without explicit authorization.

### M. Production readiness

#### PRD-01 — Architecture assessment
**Tracker status: PARTIAL**

Remaining work:
- Complete an architecture review against the intended portable,
  GitHub-connected, multi-host engineering system.
- Document system boundaries, data flows, failure modes, scaling limits,
  trust assumptions, upgrade strategy and operational ownership.
- Reconcile local-first CLI alpha status with any future SaaS claims.

#### PRD-02 — Independent high-risk review
**Tracker status: BLOCKED**

Remaining work:
- Obtain independent review of patching, sandboxing, provider writes, identity,
  tenancy, plugin isolation, authentication and release supply chain.
- Track findings and retest fixes.
- Coordinate with SEC-14 but retain the separate production/high-risk
  acceptance evidence.

#### PRD-03 — Production observability
**Tracker status: PARTIAL**

Remaining work:
- Implement production-grade metrics, logs, traces, alerting, dashboards and
  incident correlation.
- Define retention, privacy/redaction and access control.
- Add operational runbooks and alert tests.
- Local logs/audit alone do not satisfy this requirement.

#### PRD-04 — Production backup/restore/DR
**Tracker status: PARTIAL**

Remaining work:
- Implement and test end-to-end backup/restore/DR for all durable services and
  artifacts.
- Set owner-approved RPO/RTO targets.
- Run restore and disaster simulations.
- Coordinate with ENT-10 without treating a registry-only backup as complete.

#### PRD-06 — Production acceptance
**Tracker status: BLOCKED**

Remaining work:
- Reassess only after the required API/backend/SSO/HA/observability/security/
  backup/release capabilities are implemented and independently reviewed.
- Define measurable acceptance criteria, operating model, support, monitoring,
  incident response, capacity and rollback.
- Obtain explicit owner acceptance.
- Until then, classify the product as development/alpha, not production-ready.

---

## 4. Cross-cutting gaps not to lose during implementation

These are important consequences of the tracked rows and architecture documents.
They should be managed as linked work, not silently treated as completed:

1. **Source/drop-in parity:** keep canonical `src` and drop-in `core` copies
   synchronized where the project contract requires it; run the sync checker.
2. **Capability truth:** derive support from real adapter operations. A
   descriptor, manifest or mock is not an operational adapter.
3. **Approval integrity:** bind approvals to immutable plans and exact base
   revisions; revalidate stale refs and permissions immediately before writes.
4. **Audit durability:** current hash chaining is tamper-evident, not WORM,
   signed or externally anchored. Do not overstate it.
5. **Credential isolation:** never read credentials from the target repository,
   persist provider tokens in registry state, print secrets or reuse one
   provider’s credential for another.
6. **Local/GitHub parity:** keep project identity stable across local drives and
   GitHub while preserving host-specific identity and explicit clone/fork rules.
7. **Failure recovery:** test process crash, machine restart, partial writes,
   stale leases, network partition and interrupted relocation.
8. **External verification:** mocked tests, source inspection and unauthenticated
   refusal tests must never be reported as live integration verification.
9. **No unsafe feature shortcuts:** no arbitrary shell, `exec`, Docker socket
   access, Kubernetes Secret values, unsafe host mounts or unapproved
   destructive operations.
10. **Historical evidence:** retain prior reports but clearly label their
    commit/date and reconcile the current snapshot.

## 5. Owner decisions and external prerequisites

The following cannot be completed by code changes alone:

| Dependency | Needed for |
|---|---|
| Explicit owner decisions D1–D12 | ID-15 and identity/state migration semantics |
| Second drive and removable USB media | ID-10, ID-11, TST-11 |
| Second machine | ID-12 |
| Authorized GitHub and provider test credentials | HOST-14, PROV-05, TST-07, ID-13 |
| Authorized SSH target | HOST-08 live verification |
| Disposable Docker daemon/image | HOST-09 and SEC-12 / AI-05 / ORC-13 |
| Authorized non-production Kubernetes cluster | HOST-10 live verification |
| Cloud workspace account(s) | HOST-11 live verification |
| PostgreSQL instance | ENT-02 |
| OIDC/SAML identity provider | ENT-03 |
| Approved KMS/secrets service | ENT-06 |
| Queue infrastructure | ENT-07 and PRF-03 |
| Independent security reviewer | SEC-14 and PRD-02 |
| Signing infrastructure and owner approval | REL-04 |
| Explicit release authorization | REL-07 |
| macOS CI runner | TST-06 |

Where a dependency is absent, implement and test the offline code path as far as
possible, then mark only the external/live verification as blocked. Do not
fabricate credentials, infrastructure, test results or approvals.

## 6. Recommended implementation order

Use one agent only and work sequentially. This order prioritizes foundations
and avoids building UI/SaaS features on unverified host and security contracts.

| Order | Work package | Requirement IDs | Exit condition |
|---:|---|---|---|
| 0 | Fresh local workspace audit and preserve unpushed work | all | branch, HEAD, dirty files, local HOST-08/09/10 work and remote ancestry recorded |
| 1 | Reconcile tracker/report and create evidence baseline | DOC-05, P0 docs | current HEAD, counts, status and historical snapshots consistent |
| 2 | Finish remote host adapters | HOST-08, HOST-09, HOST-10, HOST-11 | real adapters registered, safe capabilities, tests and honest live-verification status |
| 3 | Cross-process lifecycle and host recovery | HOST-13 | multiprocess lease/fencing/recovery tests pass |
| 4 | Sandbox and workflow E2E | SEC-12, AI-05, ORC-13 | real isolated edit+test run with evidence |
| 5 | Generic patch/review/evolution | AI-04, AI-07, AI-08 | immutable plan, review/approval, migration and rollback workflow |
| 6 | Plugin trust and execution | PLG-02..05 | signed/trusted, permissioned, revocable, isolated plugin lifecycle |
| 7 | Enterprise persistence and auth | ENT-02..06 | PostgreSQL, migrations, real SSO/MFA, enforced RBAC and tenant isolation |
| 8 | Queue, API and interfaces | ENT-07..09, UI-04..06, PRF-03 | durable worker/API/dashboard with shared authz and backpressure |
| 9 | Backup, observability and performance | ENT-09..10, PRF-04, PRD-03..04 | operational telemetry, cache correctness, tested restore/DR |
| 10 | Portability and live provider verification | ID-10..13, HOST-14, PROV-05, TST-07/11 | real multi-volume/machine and provider evidence |
| 11 | Release engineering | REL-02, REL-04, REL-05, REL-07 | automated checksums, reproducible build, signed provenance; release only with approval |
| 12 | Independent review and acceptance | SEC-14/15, PRD-01/02/06 | independent findings closed, residual risk accepted, owner production decision |

This is a recommended sequence, not permission to make owner decisions or
perform externally visible actions.

## 7. Mandatory engineering rules for the next agent

- One agent only; no parallel subagents.
- Start with a fresh local `git status`, branch, HEAD and remote comparison.
- Never discard or overwrite existing local work; inspect and preserve it.
- Never edit `main` directly; use a feature branch.
- Do not push, open a PR, merge, tag, publish a release, change visibility or
  perform destructive Git operations without explicit owner authorization.
- Do not modify, create, restore or otherwise touch any `LICENSE` file or
  license metadata.
- Keep the repository public.
- Do not claim 100% completion, production readiness or live verification
  without direct evidence.
- Label every capability as implemented, tested, verified, partial,
  descriptor-only, unsupported or blocked.
- Tests must include negative/security/failure paths, not only happy paths.
- Keep secrets, tokens, private repository data and credentials out of reports.
- Do not replace real implementation with documentation-only stubs or mocks.
- If external infrastructure is unavailable, implement the safe offline
  adapter/code/test work and clearly record the exact live-verification blocker.
- Preserve existing architecture and compatibility; do not rewrite the project
  from scratch.

## 8. Definition of done for the overall project

The project must not be called complete until all of the following are true:

1. Every tracked requirement is either implemented and verified, or explicitly
   accepted by the owner with a documented rationale and scope.
2. No required host remains descriptor-only when the product claims that host
   is supported.
3. All advertised capabilities correspond to real operations and pass contract
   and security tests.
4. Local, removable, cross-drive, cross-machine and GitHub identity scenarios
   have executed successfully on real environments.
5. Plugin execution, if enabled, has signature/trust, permission, revocation
   and isolation controls.
6. Enterprise authentication, authorization, tenancy, persistence, queues,
   API, observability and backup/restore are implemented before SaaS claims.
7. Sandbox execution and a real end-to-end edit+test workflow have been
   verified.
8. Release artifacts have automated checksums, reproducible-build evidence and
   approved signing/provenance.
9. Independent security and high-risk reviews are completed and findings
   resolved or explicitly accepted.
10. The tracker/report counts, branch/commit references and test evidence match
    the actual audited revision.
11. Production acceptance is explicit, evidence-based and owner-approved.

**Current final classification remains: DEVELOPMENT INCOMPLETE.**
