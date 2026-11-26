# Master Completion Tracker

**Scope:** OMEGA-GUARDIAN-X Universal, repository
`shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal`.

**Snapshot:** 2026-11-27. Starting `main` was `71fcbbc` (merge of PR #18);
the owner merged PR #19 during this pass, so current `origin/main` is
`8645385`. Implementation branch: `feat/universal-lifecycle-orchestration`
(created from `71fcbbc`; no direct `main` edits).

This tracker is an evidence table, not a roadmap. A row is only `VERIFIED`
when a command or external system was checked in this pass (or an explicitly
linked, still-current evidence file). Source code alone never earns
`VERIFIED`. `TESTED` means automated tests pass but the environment is
limited (for example mocked transport or one OS).

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `VERIFIED` | Executed evidence in this pass confirms the stated behaviour or state. |
| `TESTED` | Automated tests pass, but a stated environment limit remains. |
| `IMPLEMENTED` | Code exists and is wired, with no current end-to-end evidence. |
| `PARTIAL` | Only part of the requirement is implemented; the rest is named. |
| `DESCRIPTOR_ONLY` | A provider/host descriptor exists; operations are declared unsupported. |
| `UNSUPPORTED` | No implementation; the product does not perform this operation. |
| `BLOCKED` | Cannot progress without credentials, hardware, authorization or an external account. |
| `NOT_STARTED` | No work has begun. |
| `ACCEPTED` | Risk or limitation explicitly accepted by the owner, with justification. |

## Evidence executed in this pass

| Check | Result |
| --- | --- |
| Canonical suite `python -m pytest -q` | **627 passed, 18 skipped, 119 subtests passed**, exit 0 (Windows, CPython 3.15.2, 140.40 s) |
| Drop-in suite `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **289 tests, OK (9 skipped)**, exit 0 (73.86 s) |
| `python -m compileall -q src core tests _dev/tests` | exit 0 |
| `python _dev/tools/sync_core_from_src.py --check` | `OK: source and drop-in structure, versions and packaging declarations agree.` |
| Wheel build `python -m pip wheel . --no-deps` | exit 0; 140 entries; new modules present; no `tests/` or `_dev/` entries |
| `ogx patch --help`, `ogx workflow --help` | exit 0 |
| Drop-in `init.py status` | exit 0 |
| Code scanning (GitHub UI) | **0 Open / 13 Closed** after branch head `b13a53e`, "All alerts are resolved." |
| Dependabot vulnerabilities (GitHub UI) | **0 Open / 0 Closed** |
| Dependabot malware (GitHub UI) | **0 Open / 0 Closed** |
| Secret scanning (GitHub UI) | **0 Open / 0 Closed**, "No secrets found." |
| Hosted CI at `71fcbbc` (merge of PR #18) | OGX CI #84 success (31 s), OGX Code Security #83 success (1 m 6 s), GitHub Configuration Validation #13 success (10 s) |
| Hosted CI at `b13a53e` and final PR head | 16/16 checks passed on both: OGX CI (3.11/3.12/3.13, drop-in, wheel, profile; #91 on the fix), CodeQL Python analysis (push + PR), Dependency Review, code scanning results |

Not executed in this pass: Python 3.11–3.13 locally (hosted CI runs them on
`ubuntu-latest`), macOS, a second drive/removable volume, Docker sandbox
execution, any live provider API call with credentials, clean-venv wheel
installation, release signing, performance measurement.

## Phase 0 — Repository inventory

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| P0-01 | Fresh inventory of source, tests, workflows, docs and configuration | `src/ogx` (28 top-level modules + 13 packages), `core/ogx` (10 packages), 42 canonical test files, 23 drop-in test files, root `.github/workflows` | this pass; `sync_core_from_src.py --check` | VERIFIED |
| P0-02 | Classify every TODO/FIXME/XXX/NotImplementedError | 4 abstract `NotImplementedError` sites (`engines/contract.py`, `integration/models.py`), 7 test-only `NotImplementedError` catches, **no TODO/FIXME/XXX markers** | `grep -rn` output in this pass | VERIFIED |
| P0-03 | Maintain a master completion tracker | this document | file | VERIFIED |
| P0-04 | Maintain a master completion report | `docs/MASTER_COMPLETION_REPORT.md` | file | VERIFIED |
| P0-05 | Defect repair: four CodeQL alerts | scope config on `main`; alert dispositions in GitHub UI | 0 open / 13 closed | VERIFIED |
| P0-06 | Record unresolved architecture decisions | `docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md` D1–D12 + decision register below | this document §Decision register | VERIFIED |

## Phase 1 — Security

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| SEC-01 | Resolve CodeQL alerts #8, #9, #10, #13 | `.github/codeql/codeql-config.yml` scope + UI dismissals with justification | GitHub UI 0 Open / 13 Closed | VERIFIED |
| SEC-02 | Stop synthetic test fixtures from raising alerts | `paths-ignore` for `GUARDIAN-X/tests` and `GUARDIAN-X/_dev/tests` | config on `main`; Code Security run #83 success | VERIFIED |
| SEC-03 | Proxy request-target/SSRF audit | `core/ogx/runtime/proxy.py` origin-form checks, absolute/network-path/encoded-authority rejection; `tests/test_proxy.py` | canonical + drop-in suites | TESTED |
| SEC-04 | API-token hashing design | SHA-256 over 256-bit CSPRNG tokens + `hmac.compare_digest` in `core/ogx/runtime/access.py`; justification retained | dismissal comment; tests | ACCEPTED |
| SEC-05 | Endpoint validation, DNS pinning, redirect refusal | `platform/security.py`, `platform/transport.py` | `tests/test_network_policy.py`, `test_transport_bounds.py` | TESTED |
| SEC-06 | Bounded request/response handling | transport byte caps, retries, timeouts | `test_transport_bounds.py` | TESTED |
| SEC-07 | Secret redaction in reports/logs/errors | `platform.security.redact*`, audit redaction | suite + `test_patching.py` audit assertion | TESTED |
| SEC-08 | Controlled patch writes cannot escape the project | `src/ogx/patching.py` containment, symlink refusal, hash preconditions | `tests/test_patching.py` (12 passed, 1 skipped); Linux CI caught and verified the symlink-order fix | TESTED |
| SEC-09 | Approval-gated remote writes | `platform/operations.py`; GitHub host `create_pull_request(approved=...)` | `tests/test_host_github_runtime.py` | TESTED |
| SEC-10 | Differential secret scanning | GitHub secret scanning | UI 0 open / 0 closed | VERIFIED |
| SEC-11 | Dependency vulnerability/malware scanning | GitHub Dependabot | UI 0 open / 0 closed each | VERIFIED |
| SEC-12 | Container sandbox for project execution | `sandbox.py` immutable image + no network + dropped capabilities | no Docker locally; fail-closed code only | PARTIAL |
| SEC-13 | OS-level isolation claim | none claimed; container is documented as a restricted boundary | `docs/SECURITY_MODEL.md` | VERIFIED |
| SEC-14 | Independent security review | not performed | requires an external reviewer | BLOCKED |
| SEC-15 | Threat model | `docs/SECURITY_MODEL.md` | document exists; not re-audited line by line | PARTIAL |

## Phase 2 — Universal project identity

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| ID-01 | Permanent project ID independent of path | `.guardian-x/identity.json`, UUID v4 + integrity hash | `tests/test_identity.py`, `test_host_identity.py` | TESTED |
| ID-02 | Host ID (HID) | `pap/host_identity.py`, privacy-conscious binding hash | `tests/test_host_identity.py`, `host identity` CLI test | TESTED |
| ID-03 | Workspace ID (WID) | Registry V2 `workspaces` rows with mutable location | registry tests | TESTED |
| ID-04 | Identity manifests and registries | identity file, integration manifest, Registry V2 | `test_manifest.py`, `test_registry.py` | TESTED |
| ID-05 | Discovery and reconciliation | `discovery.py`, `intelligence/integration.py` | discovery/intelligence tests | TESTED |
| ID-06 | Relocation journal and crash recovery | `relocation.py` atomic journal; `recover` resumes | `test_relocation_engine.py` | TESTED |
| ID-07 | Move / copy / clone / fork distinction | relocation operations + explicit `identity fork` | `test_identity_lifecycle_cli.py` | TESTED |
| ID-08 | Conflict detection and duplicate prevention | `DuplicateEntityError`, normalized repository keys | `test_registry.py` | TESTED |
| ID-09 | Identity explainability | `identity explain` evidence report | CLI test `verified` classification | TESTED |
| ID-10 | C: → D: relocation | no second volume available | — | BLOCKED |
| ID-11 | USB/removable relocation | no removable volume available | — | BLOCKED |
| ID-12 | Machine-to-machine migration | single machine | — | BLOCKED |
| ID-13 | Local ↔ GitHub end-to-end identity | link/duplicate logic only; no live provider credentials | — | BLOCKED |
| ID-14 | Signed identity attestation | not implemented (D2 recommendation: optional later) | — | UNSUPPORTED |
| ID-15 | Owner ratification of D1–D12 | recommendations documented only | §Decision register | BLOCKED |

## Phase 3 — Universal host engine

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| HOST-01 | Host contract and explicit capabilities | `hosts/interfaces.py`, `capabilities.py`, lifecycle `HostAdapter` | `test_host_contract.py`, lifecycle tests | TESTED |
| HOST-02 | Local filesystem host (read-only) | `filesystem_runtime.py` containment + bounds | 16 tests (3 skipped) | TESTED |
| HOST-03 | Local Git host (read-only) | `git_runtime.py`, no writes/remotes | 21 tests | TESTED |
| HOST-04 | GitHub host read surface | `hosts/adapters/github_runtime.py` over canonical transport; requires `OGX_GITHUB_TOKEN` because the provider engine refuses unauthenticated repository operations | `tests/test_host_github_runtime.py` (mocked transport) | TESTED |
| HOST-05 | GitHub host approval-gated PR creation | validated plan + explicit approval + audit | same suite: unapproved refused, approved executed | TESTED |
| HOST-06 | GitHub host branch/file/push writes | not implemented or advertised | capability map returns `unsupported` | UNSUPPORTED |
| HOST-07 | GitLab/Bitbucket/Gitea/Forgejo/Azure/CodeCommit host adapters | provider adapters exist; no lifecycle host adapter | `runtime_factory.py` rejects them | UNSUPPORTED |
| HOST-08 | Remote SSH host | descriptor only | `platform/families.py` | DESCRIPTOR_ONLY |
| HOST-09 | Docker host | descriptor only | `platform/families.py` | DESCRIPTOR_ONLY |
| HOST-10 | Kubernetes host | descriptor only | `platform/families.py` | DESCRIPTOR_ONLY |
| HOST-11 | Cloud workspace hosts | descriptor only | `platform/families.py` | DESCRIPTOR_ONLY |
| HOST-12 | Health, recovery, session lifecycle | `hosts/lifecycle.py` 12 states, bounded health/recovery | lifecycle suite | TESTED |
| HOST-13 | Cross-process failover/reconnect | process-local sessions only | — | UNSUPPORTED |
| HOST-14 | Live GitHub host verification | mocked transport only. An unauthenticated live probe was attempted on 2026-11-27 and the adapter failed closed with `credential_required`; no credential was supplied, so the authenticated probe was not run | probe output; `test_connect_without_a_credential_fails_closed` | BLOCKED |
| HOST-15 | GitHub host registered by CLI | `host register --type github --provider github --identifier owner/repo` | parser + factory | IMPLEMENTED |

## Phase 4 — Provider ecosystem

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| PROV-01 | Seven Git-hosting adapters | GitHub, GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps, CodeCommit | `providers list`; mocked contract suite | TESTED |
| PROV-02 | Descriptor-only families are honest | 21 families with unsupported operations | `platform status`: verified 7, descriptor-only 21 | VERIFIED |
| PROV-03 | Capability truth by introspection | `platform/base.descriptor()` | `test_provider_contract.py`, `test_platform_engine.py` | TESTED |
| PROV-04 | Rate limits, retries, health checks | transport + adapters | transport/provider tests | TESTED |
| PROV-05 | Live provider integrations | no credentials configured (`platform status`: 0) | — | BLOCKED |
| PROV-06 | Webhook verification | not implemented | — | UNSUPPORTED |
| PROV-07 | Token refresh/rotation | env credentials only | — | UNSUPPORTED |
| PROV-08 | Credential isolation between providers | per-provider env policy | `test_platform_engine.py` | TESTED |
| PROV-09 | No meaningless stubs added | 21 families remain descriptors | `platform status` | VERIFIED |

## Phase 5 — AI engineering core

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| AI-01 | Repository inspection and intelligence | `intelligence/`, scanner, architecture, SBOM | intelligence suites | TESTED |
| AI-02 | Root-cause/planning/impact | `impact.py`, `guardian.plan_safe_fixes`, triage | impact/pipeline tests | TESTED |
| AI-03 | Controlled patch application and rollback | `patching.py` (exact-match, hash, backup, audit) | 11 tests + CLI tests | TESTED |
| AI-04 | Generic automated patch generation from findings | only narrow `.env` ignore fix exists | `guardian.apply_safe_fixes` | PARTIAL |
| AI-05 | Sandboxed test execution | orchestrator `test` stage uses pinned-image sandbox | Docker unavailable locally; stage fails closed | PARTIAL |
| AI-06 | Static security gate | `secure` stage + scanner | orchestrator tests | TESTED |
| AI-07 | Review with diff/risk/approval | workflow `review` stage, approvals, audit | orchestrator tests | PARTIAL |
| AI-08 | Evolution with versioned changes and rollback | patch plans + audit + workflow summary; no migration engine | tests | PARTIAL |
| AI-09 | AI output is never auto-verified | policy: every change requires tests/approval | `docs/AI_AGENT_GUIDE.md` | VERIFIED |
| AI-10 | LLM code generation | none integrated; product does not call a model | — | UNSUPPORTED |

## Phase 6 — Lifecycle orchestrator

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| ORC-01 | Validated state machine | `orchestrator.py` workflow/stage states + guards | `tests/test_orchestrator.py` (11 passed) | TESTED |
| ORC-02 | Durable workflow persistence | SQLite in `<state>/workflows/orchestrator.db`, WAL | reopen test | TESTED |
| ORC-03 | Checkpoints and resume | per-stage rows, resume from pending | resume test | TESTED |
| ORC-04 | Cancellation | terminal cancel, unfinished stages cancelled | cancel test | TESTED |
| ORC-05 | Retry with attempt limit | failed stage reset, `max_attempts=2` | retry test | TESTED |
| ORC-06 | Failure recovery | failed stage recorded with stable code; resume after approval | failure test | TESTED |
| ORC-07 | Idempotency | explicit workflow ID and idempotency key | idempotency test | TESTED |
| ORC-08 | Concurrency control | `BEGIN IMMEDIATE` + running claim | code inspection; no multi-process test | PARTIAL |
| ORC-09 | Approval gates | `edit`/`test` require recorded approver | gate test | TESTED |
| ORC-10 | Event history | `events` table + CLI | event test | TESTED |
| ORC-11 | Audit correlation | workflow ID mirrored to hash-chained audit | audit assertions | TESTED |
| ORC-12 | Crash recovery after killed process | not exercised | — | UNVERIFIED |
| ORC-13 | Real edit+test end-to-end | requires patch plan + Docker + image | — | BLOCKED |
| ORC-14 | CLI create/run/status/approve/cancel/retry/events | `ogx workflow ...` | `tests/test_cli_patch_workflow.py` | TESTED |

## Phase 7 — Agent and plugin runtime

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| PLG-01 | Agent/skill manifests and inspection | `agents/`, `skills/`, `skillpacks/` | CLI tests | TESTED |
| PLG-02 | Plugin execution lifecycle | none | — | UNSUPPORTED |
| PLG-03 | Signature verification and publisher trust | none | — | UNSUPPORTED |
| PLG-04 | Plugin revocation and permissions | none | — | UNSUPPORTED |
| PLG-05 | Isolation and resource limits for plugins | none | — | UNSUPPORTED |
| PLG-06 | Third-party skill execution | disabled by design | capability matrix | ACCEPTED |

## Phase 8 — Enterprise backend

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| ENT-01 | Local tenant/organization model | `enterprise/store.py` SQLite | `test_enterprise_governance.py` | TESTED |
| ENT-02 | PostgreSQL and migrations | SQLite only | — | UNSUPPORTED |
| ENT-03 | OIDC/SAML/MFA | subject strings only, not authenticated | `docs/ENTERPRISE_READINESS.md` | UNSUPPORTED |
| ENT-04 | RBAC | local roles (owner/admin/developer/auditor/viewer) | enterprise tests | PARTIAL |
| ENT-05 | Service identities and tenant isolation | local organization scoping | enterprise tests | PARTIAL |
| ENT-06 | Encryption, KMS, rotation | none | — | UNSUPPORTED |
| ENT-07 | Job queue/workers/DLQ | none | — | UNSUPPORTED |
| ENT-08 | Durable REST API | none | — | UNSUPPORTED |
| ENT-09 | Monitoring/metrics/tracing | local audit/logs only | `docs/OBSERVABILITY.md` | PARTIAL |
| ENT-10 | Backup/restore/DR | registry backup/recover only | registry tests | PARTIAL |

## Phase 9 — Interfaces

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| UI-01 | CLI project/host/identity/security surface | `src/ogx/cli.py` | `test_cli.py`, `test_cli_universal.py` | TESTED |
| UI-02 | CLI patch and workflow surface | `ogx patch`, `ogx workflow` | `test_cli_patch_workflow.py` | TESTED |
| UI-03 | CLI audit/recovery/diagnostics | audit, registry backup/recover, observe | tests | TESTED |
| UI-04 | Authenticated REST API | none | — | UNSUPPORTED |
| UI-05 | Dashboard | none | — | UNSUPPORTED |
| UI-06 | One authorization model across interfaces | platform approval model shared by CLI/host; no API/UI | code inspection | PARTIAL |

## Phase 10 — Testing and compatibility

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| TST-01 | Canonical unit/integration suite | 42 test files | 627 passed, 18 skipped; Linux CI runs the symlink tests as well | VERIFIED |
| TST-02 | Drop-in suite | 23 test files | 289 tests, OK (9 skipped) | VERIFIED |
| TST-03 | Python 3.11/3.12/3.13 | CI matrix | all three matrix jobs green on PR #20 (OGX CI #91 on the fix, green again on the final head) | VERIFIED |
| TST-04 | Linux | `ubuntu-latest` CI | green on PR #20; Linux also executed the symlink tests skipped on Windows | VERIFIED |
| TST-05 | Windows | local execution | this pass | VERIFIED |
| TST-06 | macOS | not configured in CI | — | UNSUPPORTED |
| TST-07 | Live provider integration tests | no credentials | — | BLOCKED |
| TST-08 | Contract tests label mocks honestly | adapter contract suite; docs say mocked | `test_provider_contract.py` | VERIFIED |
| TST-09 | Performance baselines | none measured | — | NOT_STARTED |
| TST-10 | Failure injection | stage failure, transport bounds, proxy negatives | tests | PARTIAL |
| TST-11 | Cross-drive/USB relocation | no second volume | — | BLOCKED |
| TST-12 | Clean-venv wheel install | wheel built only | — | NOT_STARTED |

## Phase 11 — Performance and reliability

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| PRF-01 | Bounded scans/reads | file/size/depth limits | scanner/transport tests | TESTED |
| PRF-02 | Timeouts and bounded concurrency | transport/subprocess/sandbox timeouts | tests | TESTED |
| PRF-03 | Queue backpressure | no server/queue | — | UNSUPPORTED |
| PRF-04 | Caching/incremental analysis | partial (scan limits; no cache) | — | PARTIAL |
| PRF-05 | Large-repository behaviour | not benchmarked | — | NOT_STARTED |
| PRF-06 | Memory/database performance | not measured | — | NOT_STARTED |

## Phase 12 — Documentation

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| DOC-01 | Tracker and completion report | this file + report | files | VERIFIED |
| DOC-02 | README verification figures current | updated 2026-11-27 | `README.md` | VERIFIED |
| DOC-03 | Host documentation matches adapters | `HOST_ARCHITECTURE.md` updated | file | VERIFIED |
| DOC-04 | Changelog entry for new capabilities | `CHANGELOG.md` | file | VERIFIED |
| DOC-05 | Every historical document re-audited | not done (many milestone reports) | — | PARTIAL |
| DOC-06 | Unsupported operations documented | host/provider/security docs | files | VERIFIED |

## Phase 13 — Release and supply chain

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| REL-01 | Wheel build includes new modules | setuptools stdlib-only package | 140 entries, required modules present, no tests | VERIFIED |
| REL-02 | Artifact checksums | computed manually | SHA-256 shown in build output | PARTIAL |
| REL-03 | SBOM generation | `ogx sbom` | existing suite | TESTED |
| REL-04 | Artifact signing / provenance | none | — | UNSUPPORTED |
| REL-05 | Reproducible builds | not established | — | UNSUPPORTED |
| REL-06 | Dependency review | GitHub Dependency Review workflow succeeded | run #17 at PR #19 | VERIFIED |
| REL-07 | Publish a release | requires owner authorization | — | BLOCKED |

## Phase 14 — Production readiness

| ID | Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- | --- |
| PRD-01 | Architecture assessment | documented limits | docs | PARTIAL |
| PRD-02 | Independent high-risk review | none | — | BLOCKED |
| PRD-03 | Observability in production | local logs/audit only | — | PARTIAL |
| PRD-04 | Backup/restore/DR at scale | registry backup only | — | PARTIAL |
| PRD-05 | Upgrade/migration safety | additive registry migration + tests | registry tests | TESTED |
| PRD-06 | Production acceptance | unmet: no API/backend/SSO/HA | report §Final acceptance | BLOCKED |

## Decision register (owner approval required)

The relocation/identity design records recommendations, not owner-ratified
decisions. This pass did not silently implement the unresolved ones.

| ID | Decision | Recorded recommendation | Ratification |
| --- | --- | --- | --- |
| D1 | Where host identity lives | `host.json` as authority, registry mirrors it | not recorded |
| D2 | Project identity authenticity | integrity hash now; keyed attestation later; no mandatory signing | not recorded |
| D3 | State layout v2 activation | opt-in migration only | not recorded |
| D4 | PID on a filesystem copy | same PID/new WID by default, conflict check | not recorded |
| D5 | WID on same-volume rename | retain WID | not recorded |
| D6 | Registry schema v3 timing | with the state-layout phase | not recorded |
| D7 | Drop-in storage | atomic JSON indexes | not recorded |
| D8 | Machine-binding sources | OS ID + state-root ID | not recorded |
| D9 | Two PIDs claiming one repository URL | refuse by default; explicit fork record only | not recorded |
| D10 | Repository URL change | require confirmation | not recorded |
| D11 | Default relocation semantics | copy with source retained | not recorded |
| D12 | Cross-host import contents | reports/audit first; registry only by explicit adoption | not recorded |

## Blocked register

| Blocker | Exact missing input | Impact | Safe unblock |
| --- | --- | --- | --- |
| Live provider verification | Provider accounts and tokens for GitHub/GitLab/Bitbucket/Gitea/Forgejo/Azure/CodeCommit | All live adapter claims remain unverified; only mocks pass | Owner sets per-provider env vars; run contract probes with read-only scopes |
| Remote/cloud hosts | SSH targets, Docker/Kubernetes clusters, cloud accounts | No host adapter beyond local + GitHub read | Provide a non-production target and authorization |
| Enterprise backend | PostgreSQL instance, IdP (OIDC/SAML), KMS | No SaaS/HA/SSO production readiness | Owner approves architecture and provides non-production instances |
| Independent security review | External reviewer | High-risk components not independently reviewed | Commission review; no substitute claimed |
| Cross-drive/USB/machine relocation | Second volume and second machine | Three relocation scenarios unexecuted | Attach media/second host; run `relocate` plan/apply/verify |
| Release publication/signing | Owner authorization and signing keys | No signed release or provenance | Owner authorizes; otherwise artifacts remain local |
| Real merge | Owner authorization | Branch cannot land itself | Owner reviews and merges the pull request |

## Requirement counts

Counts are generated from the rows above during verification; see
`docs/MASTER_COMPLETION_REPORT.md` for the summary and the final acceptance
classification. No row is deleted when superseded; superseded rows record the
replacement instead.
