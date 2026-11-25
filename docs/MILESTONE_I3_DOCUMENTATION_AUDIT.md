# Milestone I.3 — Documentation Audit

Date: 2026-11-26
Audited commit: `757d6b6c519a30487a3bd46db30011ece30b1bdb`
Parent: `d55cea3721f73174c1337eecca1054793dc26bc3`

## 1. Documents reviewed

- Root `README.md`
- `GUARDIAN-X/README.md`
- `GUARDIAN-X/docs/FINAL_PROJECT_COMPLETION_REPORT.md`
- `GUARDIAN-X/docs/FINAL_RELEASE_READINESS_REPORT.md`
- `GUARDIAN-X/docs/PROJECT_COMPLETION_TRACKER.md`
- `GUARDIAN-X/docs/HOST_ARCHITECTURE.md`
- `GUARDIAN-X/docs/HOST_CONTRACT_VERIFICATION.md`
- `GUARDIAN-X/docs/HOST_LIFECYCLE_VERIFICATION_REPORT.md`
- `GUARDIAN-X/docs/CI_CD_VERIFICATION.md`
- Repository-root `docs/MILESTONE_I_*`, `MILESTONE_I1_*`, `MILESTONE_I2_*`

## 2. The host-lifecycle contradiction

### 2.1 The two claims

`FINAL_PROJECT_COMPLETION_REPORT.md` §6 says the local filesystem and local Git
hosts are "operational (read-only): they connect, read, report health, recover,
and run through the CLI". The same table then says of the seven
provider-verified Git adapters that "the full host
session/health/read/disconnect/recovery lifecycle is still not implemented".

`HOST_ARCHITECTURE.md` §1 was stronger: "**No host adapter is declared
operational in `0.8.0a1`.**" immediately above a table that labels both local
hosts `OPERATIONAL (read-only)`.

### 2.2 Source evidence

| Capability | Source | Observation |
| --- | --- | --- |
| Lifecycle states and transitions | `GUARDIAN-X/src/ogx/hosts/lifecycle.py` | 12 `HostLifecycleState` values, `ALLOWED_TRANSITIONS`, `HostSessionManager` with `create_session`, `connect`, `disconnect`, `health`, `recover`, `shutdown`, `dispose`, `list_sessions`, `effective_capabilities` |
| Filesystem host | `GUARDIAN-X/src/ogx/hosts/adapters/filesystem_runtime.py` | `validate`, `connect`, `disconnect`, `health`, `capabilities`, `metadata`, `list_directory`, `read_file`, containment and symlink-escape rejection |
| Git host | `GUARDIAN-X/src/ogx/hosts/adapters/git_runtime.py` | `validate`, `connect`, `disconnect`, `health`, `capabilities`, `metadata`, `list_branches`, `list_commits`, `list_tree`, `read_file`, read-only Git commands |
| CLI surface | `GUARDIAN-X/src/ogx/cli.py` | `cmd_host_connect` (1329), `cmd_host_disconnect` (1348), `cmd_host_health` (1372), `cmd_host_recover` (1401), `cmd_host_sessions` (1423); parsers at 2856, 2873, 2880 |
| Test evidence | `tests/test_host_lifecycle.py`, `test_host_filesystem_runtime.py`, `test_host_git_runtime.py`, `test_host_cli_lifecycle.py`, `test_host_contract.py` | 68 passed, 3 skipped, 43.29 s (this milestone) |

### 2.3 Finding

The lifecycle **is** implemented for the two local hosts. The "not implemented"
statement is true only for the seven provider-verified Git adapters, which are
wired to the provider engine and the registry/metadata contract but not to the
`HostSessionManager` lifecycle. The sentence lost that scope and read as a
global denial, contradicting the same report and `HOST_ARCHITECTURE.md`.

The docs were also stale in a second way: they said the shared contract suite
does not cover connect/read because adapters do not implement those operations.
That was true at Phase 3, before the lifecycle milestone; it is no longer true
for the local hosts, which are covered by four dedicated suites.

### 2.4 Corrections applied

- `FINAL_PROJECT_COMPLETION_REPORT.md` §6 table row now limits the
  "not implemented" statement to the provider-verified adapters and states that
  the lifecycle is implemented only for the two local hosts.
- `HOST_ARCHITECTURE.md` §1 now says no **cloud, remote or write-capable** host
  is operational, and that the local hosts are operational for their documented
  read-only surface.
- `HOST_ARCHITECTURE.md` §4 now distinguishes in-process recovery (implemented
  and tested) from cross-process session persistence and failover (not
  implemented).
- `HOST_ARCHITECTURE.md` duplicate headings were renumbered: §6 is now
  "Host session lifecycle", §7 "Security boundaries", §8 "Contract suite",
  §9 "Adding a host". The empty duplicate "Capability negotiation" heading was
  removed.

## 3. Other stale or contradictory claims

| Document | Claim | Evidence | Action |
| --- | --- | --- | --- |
| `FINAL_RELEASE_READINESS_REPORT.md` §13 | Blocker 4: "No operational host / lifecycle suite" | Contradicts its own §5 and 68 passing host tests | Reworded to "No operational cloud/remote host; only read-only local hosts" |
| `CI_CD_VERIFICATION.md` §3 | "No hosted run exists" | GitHub API lists 25 runs; 3 for `757d6b6` | Kept the historical pass statement, added a dated I.3 update |
| `FINAL_PROJECT_COMPLETION_REPORT.md` §12 | "No hosted run exists" | Same API evidence | Added a dated I.3 update |
| `FINAL_RELEASE_READINESS_REPORT.md` §8 | "No hosted run exists; CI is UNVERIFIED" | Same API evidence; jobs never started | Added a dated I.3 update; CI still unverified |
| `PROJECT_COMPLETION_TRACKER.md` P11-3, RR-12 | "no run URL exists" | Runs exist; all refused at startup | Updated evidence cells |
| `TEST_RESULTS.md` "Not verified" | Hosted run "not observed" | Runs observed, zero jobs executed | Replaced with observed facts |
| `FINAL_PROJECT_COMPLETION_REPORT.md` §13 | "`LICENSE` bundled" | LICENSE absent; wheel has no license file | Added dated I.3 packaging update |
| `FINAL_RELEASE_READINESS_REPORT.md` §10 | "LICENSE bundled" | Same | Added dated I.3 packaging update |
| `GUARDIAN-X/README.md` layout | Lists `LICENSE` in the tree | `GUARDIAN-X/LICENSE` is absent | Removed the entry and marked it currently absent |
| Root `README.md` verification | Wheel 163 files; drop-in 279 files, no CI | Reproduced 167 wheel entries; 324 working-tree / 305 clean-checkout drop-in files, including a `.github` path | Updated counts and the forbidden-path finding |
| Root `README.md` CI sentence | Hosted Actions "has not been executed" | Runs exist but no job executed | Reworded to distinguish runs from executed jobs |

## 4. Claims re-verified as accurate

- Canonical suite: 465 passed, 15 skipped (reproduced this milestone).
- Drop-in suite: 245 tests, OK, 9 skipped (reproduced).
- Host lifecycle/adapter/CLI/contract suites: 68 passed, 3 skipped.
- `tests/test_host_contract.py`: 12 passed.
- Local hosts are read-only: both adapters return `unsupported` for writes,
  execution, branch mutation, push, fetch, clone, merge and deployment.
- Registration starts at `registered`, not `active`; `connected` is never
  persisted; sessions are process-local.
- 28 provider descriptors, 7 verified and 21 descriptor-only.
- No live provider API call was made and no credentials were used.

## 5. Historical snapshots preserved

- `HOST_CONTRACT_VERIFICATION.md` retains its "Historical snapshot (Phase 3)"
  banner and its original section 3 text.
- The Milestone I, I.1 and I.2 reports were not rewritten. Their statements
  such as "no hosted run was observed" remain true for the pass they describe.
- Dated I.3 updates were appended rather than replacing the historical text in
  `CI_CD_VERIFICATION.md`, `FINAL_PROJECT_COMPLETION_REPORT.md` and
  `FINAL_RELEASE_READINESS_REPORT.md`.

## 6. Residual documentation risks

- The working tree moves the 14 milestone reports into `GUARDIAN-X/docs/`,
  while `origin/main` keeps them in root `docs/`. References in
  `FINAL_PROJECT_COMPLETION_REPORT.md` to `../docs/MILESTONE_I1_*.md` are
  correct for the committed tree and broken for this uncommitted working tree.
  The move was preserved as user work and not "fixed".
- Hosted CI wording must continue to distinguish "a run object exists" from
  "a job executed". At `757d6b6` the former is true and the latter is false.
- The LICENSE absence is a packaging fact, not a license-compliance finding.
  Nothing in this milestone resolves it.
