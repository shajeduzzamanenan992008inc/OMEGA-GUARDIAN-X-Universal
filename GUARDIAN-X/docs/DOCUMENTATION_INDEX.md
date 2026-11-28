# Documentation index and historical audit (DOC-05)

**Audit date:** 2026-11-28
**Scope:** every Markdown file under `GUARDIAN-X/` (root plus `docs/`,
`docs/archive/`, `docs/examples/`, `docs/kit/`), the diagrams and example
configurations they reference.
**Status:** **PARTIAL** — the inventory, classification method and corrections
below are complete for current-state documents; the ~70 milestone/release
reports were classified but not re-verified line by line against current code.

## 1. Method

1. Enumerate every `*.md` file (`find GUARDIAN-X -name '*.md'`).
2. Classify each as **current**, **historical**, **superseded** or
   **archive** from its own header/status banner and its subject.
3. Grep current documents for stale artefacts: commit SHAs, branch names, test
   counts, version strings, package paths and capability claims.
4. Verify commands in the current documents against `ogx --help` and the CLI
   parser, and paths against the tree.
5. Correct current documents in place; never rewrite history — historical
   reports keep their text and gain a dated correction where a claim is now
   wrong.
6. Record what was *not* verified instead of implying a complete audit.

## 2. Classification

### Current (authoritative for behaviour)

| Document | Subject | Verified in this pass |
| --- | --- | --- |
| `README.md`, `README-Guardian-X.md`, `README.bn.md` | product overview, install, commands | commands spot-checked; counts updated only where stated |
| `SECURITY.md` | disclosure policy | reviewed, unchanged |
| `CHANGELOG.md` | release notes | updated with this pass |
| `docs/ARCHITECTURE.md`, `docs/CORE_CONTRACTS.md`, `docs/DEPENDENCY_BOUNDARIES.md` | component map and import contracts | import boundaries re-checked by `_dev/tests/test_architecture.py` |
| `docs/ENTERPRISE_API_AND_AUTHZ.md` | ENT-04/05/08, UI-04/05/06 | new; matches code |
| `docs/KMS_AND_ENCRYPTION.md` | ENT-06 | new; matches code and RFC vectors |
| `docs/BACKUP_AND_RESTORE.md` | ENT-10 | new; matches code and restore tests |
| `docs/HOST_CLOUD_WORKSPACE.md` | HOST-11 | new; matches code, live step marked BLOCKED |
| `docs/HOST_ARCHITECTURE.md` | host adapters and status | stale rows corrected (see §3) |
| `docs/SECURITY_MODEL.md` | threat model, controls | TB-11/TB-12 and T-21..T-28 added; ENT claim corrected |
| `docs/MASTER_COMPLETION_TRACKER.md` | requirement status | 2026-11-28 addendum added |
| `docs/MASTER_COMPLETION_REPORT.md` | completion report | 2026-11-28 addendum added |
| `docs/PRODUCTION_READINESS_ASSESSMENT.md` | PRD-01 gap matrix | new; source-derived |
| `docs/POSTGRESQL_BACKEND.md` | ENT-02 opt-in PostgreSQL store, migrations and queue semantics | matches `enterprise/database.py`; live server step marked BLOCKED |
| `docs/OWNER_DECISIONS_AND_EXTERNAL_PREREQUISITES.md` | D1–D12 and every external blocker with minimum owner action | new; source- and environment-derived |
| `docs/evidence/*.json` | Executed evidence: GitHub read-only probe, dashboard browser E2E, cross-volume relocation, backup/restore drill | produced by committed tools on 2026-11-28; paths and digests only, no secrets |
| `docs/OBSERVABILITY.md`, `docs/TROUBLESHOOTING.md`, `docs/INSTALLATION.md`, `docs/MIGRATION.md` | operations | reviewed for broken commands; no change required |
| `docs/RELEASE_GUIDE.md`, `docs/PERFORMANCE_BASELINE.md` | release and performance | reviewed; figures retain their original dates |

### Historical (kept, not current truth)

`docs/MASTER_BASELINE_AUDIT.md`, `docs/UNIVERSAL_AUDIT.md`,
`docs/PROJECT_COMPLETION_BASELINE.md`, `docs/PROJECT_COMPLETION_TRACKER.md`,
`docs/MEGA_ULTRA_*`, `docs/FINAL_*`, `docs/PHASE_0_COMPLETION_REPORT.md`,
`docs/MILESTONE_*` (30 files), `docs/CI_*`, `docs/*_VERIFICATION_REPORT.md`,
`docs/RELEASE_*_REPORT.md`, `docs/archive/`.

These describe the state at their own commit. They are evidence of what was
true then; they are **not** status. Test counts and SHAs inside them must not be
quoted as current. None were edited (history is preserved); the tracker and this
index are the current authority.

### Superseded (explicitly labelled)

| Document | Superseded by |
| --- | --- |
| `docs/CAPABILITY_MATRIX.md` (0.7.1a1 banner) | `docs/PLATFORM_SUPPORT_MATRIX.md` + this pass's module docs |
| `docs/TRINITY_TEST_PLAN.md` | current test suites and CI workflow |
| `docs/PRD.md` early scope | `docs/PRODUCTION_READINESS_ASSESSMENT.md` for the remaining gaps |

### Archive

`docs/archive/` is retained unmodified; nothing there is referenced by current
documentation.

## 3. Stale claims found and corrected (2026-11-28)

| Document | Stale claim | Correction |
| --- | --- | --- |
| `docs/HOST_ARCHITECTURE.md` | "GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps, CodeCommit host sessions — UNSUPPORTED" while another row said "PROVIDER VERIFIED" | merged into one row: provider host sessions implemented and tested against a mocked transport, no live credential |
| `docs/HOST_ARCHITECTURE.md` | "GitHub Codespaces, Gitpod, Kubernetes — DESCRIPTOR_ONLY" | split: Kubernetes adapter implemented (HOST-10), Codespaces adapter implemented (HOST-11, mocked), Gitpod remains descriptor |
| `docs/HOST_ARCHITECTURE.md` | adapter tree listed only `local_filesystem, local_git, github` | tree now lists ssh, docker, kubernetes, cloud and provider adapters |
| `docs/SECURITY_MODEL.md` | "Live enterprise SSO/MFA, real tenant isolation and KMS-backed encryption (ENT-02..06 remain not started)" | corrected: RBAC, tenant-scoped service identities and local-KMS envelope encryption are implemented and tested; SSO (ENT-03), PostgreSQL (ENT-02) and a managed KMS remain open |
| `docs/SECURITY_MODEL.md` | threat model stopped at T-20 / TB-10 | added TB-11 (API + service tokens), TB-12 (key store), T-21..T-28 |

Claims checked and found correct: the stdlib-only promise
(`test_architecture.test_src_uses_only_the_standard_library`), canonical/drop-in
split (`sync_core_from_src.py --check`), the `ogx patch|workflow|host|release|
sandbox` command surface, and the "no OS-level isolation claim" statement.

## 4. Commands and paths verified

```bash
ogx --help ; ogx host --help ; ogx enterprise --help ; ogx authz --help
ogx serve --help ; ogx patch --help ; ogx workflow --help
python _dev/tools/sync_core_from_src.py --check     # OK
python -m pytest -q                                 # 1182 passed, 23 skipped
```

Every path referenced by the new documents exists in the tree
(`src/ogx/api/`, `src/ogx/enterprise/{authz,crypto,secrets,service_identity}.py`,
`src/ogx/hosts/adapters/cloud_runtime.py`,
`tests/test_{authz,api,crypto_kms,enterprise_identity,cloud_host}.py`).

## 5. Remaining work for a complete DOC-05

* Re-verify the ~70 historical/milestone reports' internal SHAs and counts
  (classification is done; line-by-line reconciliation is not).
* Verify every command and example in `docs/examples/` and `docs/kit/`.
* Add runbooks for incident response, KMS recovery and queue backlog operations
  (they do not exist yet; PRD-03/PRD-04 remain PARTIAL).
* Refresh `README` screenshots/diagrams after the dashboard is browser-verified.

This document is the review record; a future pass must extend §3 rather than
rewrite it, so the correction history stays auditable.
