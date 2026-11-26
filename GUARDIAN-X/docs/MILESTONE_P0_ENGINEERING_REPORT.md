# Milestone P0 — Identity Foundation and Canonical/Drop-in Parity Harness

Engineering report for the P0 phase defined in
[`UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md`](UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md)
section 14.

**Status: COMPLETE.** P0 changed no production behavior. It added shared
fixtures and two test suites that record today's behavior — including the
behavior the design says must change later — so that every following phase has a
stable, executable baseline to move.

| Item | Value |
| --- | --- |
| Repository root | `D:\FTP\OMEGA-GUARDIAN-X-Universal` |
| Product root | `D:\FTP\OMEGA-GUARDIAN-X-Universal\GUARDIAN-X` |
| Branch | `main` |
| HEAD at start and end | `24c954e711924dd4ee8f5be4a74c05958d21d1c0` |
| `origin/main` | `24c954e711924dd4ee8f5be4a74c05958d21d1c0` (0 ahead, 0 behind) |
| Host | Windows 10.0.19045, Git Bash, CPython 3.15.2 |
| Production files changed | none |
| Files added | 7 (2 fixtures, 1 support module, 1 fixture builder, 2 test modules, 1 report — see §8) |

---

## 1. Scope and constraints honored

P0 is a **harness-only** milestone. The following were explicitly not done:

- No implementation of P1–P6: no identity recovery wiring, no host identity
  file, no workspace binding, no relocation command, no state-layout or registry
  migration.
- No production behavior change: `git status` shows only new untracked files;
  every tracked file is byte-identical to HEAD.
- No schema, registry or state-layout migration; no user data touched.
- `LICENSE` and license metadata untouched.
- No staging, commit, push, merge, branch creation or PR.
- No destructive Git operation (`reset`, `rebase`, `force-push`, `clean`).
- No existing test, fixture or user file modified, overwritten or removed.

Repository and HEAD were verified **before** any write, and compared against
`origin/main` (§2). The remote URL is
`https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal.git`.

---

## 2. Baseline verification (before any change)

```
$ git rev-parse HEAD
24c954e711924dd4ee8f5be4a74c05958d21d1c0
$ git rev-parse origin/main
24c954e711924dd4ee8f5be4a74c05958d21d1c0
$ git rev-list --left-right --count origin/main...HEAD
0       0
$ git branch --show-current
main
$ git status --porcelain=v1
(empty)
$ git stash list
(empty)
```

Baseline test results at `24c954e`, measured for this milestone rather than
copied from an earlier report:

| Suite | Command | Result | Duration |
| --- | --- | --- | --- |
| Canonical (full) | `python -m pytest -q` | **465 passed, 15 skipped** | 128.19 s |
| Drop-in (full) | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **Ran 258 tests — OK (skipped=9)** | 102.34 s |
| Focused identity/registry | `python -m pytest tests/test_identity.py tests/test_registry.py -q` | **67 passed** | 4.67 s |
| Source/drop-in sync | `python _dev/tools/sync_core_from_src.py --check` | **OK** | — |

The 15 + 9 skips are platform skips (symlink creation unavailable on this
Windows host, plus one drive-relative case covered elsewhere).

---

## 3. What was inspected

Source read before writing any test (no claim in this report rests on an earlier
report):

| File | What was taken from it |
| --- | --- |
| `src/ogx/pap/identity.py` | `ProjectIdentity`, canonical JSON rules, `compute_hash`, `read_identity`, `recover_identity`, `create_identity` |
| `src/ogx/pap/identity_schema.py` | Strict v1.0 schema, field limits, secret patterns, `MAX_IDENTITY_FILE_SIZE` |
| `src/ogx/pap/exceptions.py` | Exact error classes used by the vectors |
| `src/ogx/cli.py` | `cmd_identity` verb set, `cmd_host_register` UUID-per-call, `_external_report_dir` 16-hex key |
| `src/ogx/config.py` | `state_dir()` and the `OGX_HOME` contract |
| `src/ogx/registry/v2.py` | Workspace rows, location lookup, no uniqueness on `normalized_location` |
| `src/ogx/hosts/registry.py` | `register_host` accepts an explicit id (the CLI is what mints a new one) |
| `src/ogx/platform/identity.py` | `normalize_repository_url`, `deterministic_repository_id` |
| `core/ogx/cli/common.py` | Drop-in report dir, 16-hex key, symlink refusal |
| `init.py` | `ALIASES`, `state_home()` 12-hex key, `_prepare_env` refusal |
| `_dev/tests/helpers.py`, `_dev/tests/test_architecture.py`, `tests/*` | Existing conventions, import rules, `pythonpath` config |

The design report was read in full for §§8–15 (relocation matrix, migration
sequence, threats, parity contract and gates, regression matrix, phase plan,
risks and the twelve open decisions).

---

## 4. Shared fixtures

Both distributions read the **same** files. They live under `_dev/`, which is
part of neither shipped distribution; a test asserts that.

### 4.1 `_dev/fixtures/identity_vectors.json` — 27 golden vectors

| Check | Vectors | Count |
| --- | --- | --- |
| `validate_json` accept | V01–V08 | 8 |
| `validate_json` reject | V09–V23 | 15 |
| `file_size` reject | V24 | 1 |
| `read_identity` accept | V26 | 1 |
| `read_identity` reject | V25, V27 | 2 |

Design §13.3 required cases and where they live:

| Required case | Vector |
| --- | --- |
| Minimal valid identity | `V01-minimal` |
| Maximal valid identity (description at limit) | `V03-max-description` |
| Unicode project name and description | `V05-unicode` |
| UUIDv1 / UUIDv5 / malformed UUID | `V09`, `V10`, `V11` |
| Missing or non-UTC timestamp | `V17` (missing timezone), `V18` (non-ISO); `V06`/`V08` cover accepted `Z` and `+05:30` spellings |
| Unknown field | `V13` |
| File above 64 KiB | `V24` |
| Credential-like value | `V19` |
| Hash mismatch | `V25` |
| CRLF vs LF input | `V26` |

Each accepted vector stores the input object, the exact canonical JSON, and the
expected `sha256:` identity hash. Each rejected vector stores the expected error
class and field. The file also records the canonicalization rules and the two
implementations' declared status, so the vectors are self-describing rather than
tied to one test module.

The golden values were produced by `_dev/tools/build_identity_vectors.py`
against the shipped implementation, so no hash was transcribed by hand. The file
says in its own header that it must never be regenerated to make a failing test
pass.

### 4.2 `_dev/fixtures/relocation_scenarios.json` — 22 rows, 6 conflicts, 11 probes

- One scenario per row of the design's 22-row relocation matrix, each carrying
  the abstract `project_id` / `host_id` / `workspace_id` / state / repository
  transitions and whether the operation is automatic and whether it needs
  confirmation.
- Six conflict scenarios: one project at two locations, one location reused by a
  different project, repeated host registration, same folder name in two
  parents, case-only path difference, state root inside the project.
- Eleven probe definitions, each declaring which distribution can execute it.
- Nine invariants transcribed from design §8.2 and §9.1.

Every scenario carries an `observation` value, and the fixture states plainly
that no scenario may be reported as passing while its observation is
`REAL_MEDIA` or `NOT_IMPLEMENTED`.

### 4.3 `_dev/fixtures/vectors.py` — shared loader and oracle

Standard-library only, so the drop-in suite can use it while staying
stdlib-only. It provides fixture loading, structural validation and
`assert_golden_consistency`, which re-derives the canonical bytes and hash from
the recorded rules **independently of either implementation**. Its purpose is to
prove the frozen values were not hand-edited or corrupted; it is explicitly not
a third production implementation and is imported by no shipped code.

---

## 5. Tests added

### 5.1 Canonical: `tests/test_identity_vectors.py` (15 tests)

| Class | Asserts |
| --- | --- |
| `FixtureIntegrityTests` | Fixture structure; golden bytes/hashes match the recorded rules; required design cases present; only canonical claims identity vectors |
| `AcceptedVectorTests` | Each accepted vector validates, canonicalizes to the exact bytes, hashes to the exact value, verifies, and is stable across recomputation |
| `RejectedVectorTests` | Each rejected vector raises the recorded class and field, and never produces a hash |
| `FileLevelVectorTests` | Oversize file refused before parsing; hash mismatch refused **and the file not rewritten**; missing hash refused; CRLF file reads identically and is not rewritten; CRLF and LF share one identity |
| `RecordedDivergenceTests` | UUID case changes the hash; timestamp spelling changes the hash |

### 5.2 Canonical: `tests/test_relocation_fixtures.py` (36 tests + 1 platform skip)

| Class | Covers |
| --- | --- |
| `FixtureCoverageTests` | All 22 matrix rows present and unique; PID immutability rule; WID retained only for moves; copy/clone/fork never reuse a WID; real-media rows marked not run; not-implemented rows name a missing surface; invariants recorded |
| `IdentityPreservedTests` | RX-01 rename, RX-02 move, RX-06 copy, RX-12/13 restore — `project_id`, `identity_hash` and identity file bytes identical afterwards |
| `IdentityAbsentTests` | RX-10: missing identity raises; recovery refuses without confirmation; confirmation mints a UUIDv4; second call is stable |
| `MissingSurfaceTests` | `identity fork`, `identity explain` and a `relocate` domain are rejected by the CLI; every not-implemented row maps to a missing surface |
| `RepositoryUrlTests` | RX-14: HTTPS and SSH normalize to one key and one deterministic repository id; embedded credentials refused |
| `StateIsolationTests` | RX-18, CX-04, CX-06: state root inside the project refused; outside accepted; distinct locations distinct; same folder name does not collide; symlinked state root refused |
| `StateKeyCollisionTests` | CX-05: case-only difference maps to one state directory; key is 16 hex characters; collision is recorded as undetected |
| `HostIdentityTests` | CX-03: registering the same local host twice produces two host ids (real CLI, isolated `OGX_HOME`) |
| `WorkspaceConflictTests` | CX-01/CX-02: two locations for one project; one location reused by another project returns the newest row and refuses nothing; location update keeps the project; missing location retains state |

### 5.3 Drop-in: `_dev/tests/test_identity_parity.py` (20 tests)

| Class | Asserts |
| --- | --- |
| `SharedFixtureTests` | The same fixtures load and validate; golden values pass the same oracle; 22 rows present; fixtures live outside both distributions |
| `RecordedDistributionGapTests` | Drop-in identity status is `not-implemented`; **no vector may claim a distribution whose status is not implemented**; canonical runs every vector; no `ogx.kernel.identity` is importable; probes declare their distribution and real-media probes prove nothing |
| `DropinAliasTests` | No identity/workspace/relocate/host aliases exist; the existing alias order is unchanged |
| `DropinStateKeyTests` | 12-hex key; canonical 16-hex key differs; distinct hosts distinct; case-only difference collides; same folder name does not |
| `DropinStateIsolationTests` | State root inside the host tree refused; outside accepted |
| `DropinIdentityFilePreservationTests` | A drop-in run creates no `.guardian-x` at all, and leaves an existing identity file byte-identical |

The parity gate is the second row: if P1 or P2 gives the drop-in an identity
module, the test fails until both the declared status and the per-vector
`applies_to` lists are updated in the same change set.

---

## 6. Verification actually run

All commands were run from `GUARDIAN-X` on Windows/CPython 3.15.2.

| Check | Command | Result |
| --- | --- | --- |
| Canonical full suite | `python -m pytest -q` | **516 passed, 16 skipped, 102 subtests passed** (223.58 s) |
| Drop-in full suite (final) | `PYTHONPATH=core python -m unittest discover -s _dev/tests -v` | **Ran 278 tests — OK (skipped=9)** (69.73 s) |
| Drop-in suite, repeat 1 | same, no `-v` | Ran 278 — OK (skipped=9) |
| Drop-in suite, repeat 2 | same | Ran 278 — OK (skipped=9) |
| Drop-in suite concurrent with sync check | sync check in background + suite | Ran 278 — OK (skipped=9) |
| Source/drop-in sync | `python _dev/tools/sync_core_from_src.py --check` | **OK**: structure, versions and packaging agree |
| Syntax check of every added Python file | `compile()` on the five files | OK, no trailing whitespace |
| Fixture encoding | bytes inspection | UTF-8, LF only, trailing newline |
| Whitespace check | `git diff --check` / `git diff --cached --check` | exit 0, no output |

Increments against the measured baseline:

| Distribution | Baseline | After P0 | Delta |
| --- | --- | --- | --- |
| Canonical | 465 passed, 15 skipped | 516 passed, 16 skipped | +51 passed, +1 skipped (52 collected) |
| Canonical subtests | not reported | 102 | +102 (all in the new files) |
| Drop-in | 258 OK, 9 skipped | 278 OK, 9 skipped | +20 |

The single new skip is `test_symlinked_state_root_is_refused`, which skips on
this host for the same reason the 15 existing skips do: symlink creation is not
available. It is not counted as passing anywhere in this report.

---

## 7. Findings recorded by P0

P0 is not allowed to fix these; it records them so the phases that own them must
decide them deliberately.

| # | Finding | Evidence | Owner |
| --- | --- | --- | --- |
| F1 | UUID letter case is part of the identity: the same UUID in upper and lower case produce different `identity_hash` values | `V06-uppercase-uuid` vs `V01-minimal`; `RecordedDivergenceTests` | P1 (canonicalization), D5/T5 |
| F2 | Timestamps are validated but not normalized to UTC, so `Z`, `+00:00` and `+05:30` spellings all hash differently | `V06`, `V07`, `V08` | P1, D5 |
| F3 | A directory copy keeps the `project_id` and raises no conflict; two live trees share one project | `IdentityPreservedTests.test_copy_duplicates_the_project_id_at_the_new_location`, CX-01 | P3/P4, D4 |
| F4 | `identity recover` consults no registry or manifest evidence; with `--confirm` it mints a fresh identity | `IdentityAbsentTests`, `src/ogx/pap/identity.py` | P1, RT-09…RT-11 |
| F5 | `identity fork` and `identity explain` do not exist; there is no `relocate` domain | `MissingSurfaceTests` | P1/P4 |
| F6 | Registering the same local host twice creates two different host ids | CX-03, `src/ogx/cli.py` `cmd_host_register` | P2, RT-16 |
| F7 | The registry has no uniqueness constraint on `workspaces.normalized_location`; one location can hold two projects and lookup returns the newest | CX-02, `src/ogx/registry/v2.py` | P3, RT-27 |
| F8 | Both distributions casefold the location before hashing, so case-only-different paths share one state key | CX-05, `StateKeyCollisionTests`, `DropinStateKeyTests` | P5, T5/RT-48 |
| F9 | The state key is 16 hex characters in canonical and 12 in the drop-in, from the same SHA-256 digest | `StateKeyCollisionTests`, `DropinStateKeyTests`, fixture `implementations` | P5 |
| F10 | The drop-in distribution has no identity surface at all | `RecordedDistributionGapTests` | P1/P2 parity |

F9 and F10 are the two places where the distributions still disagree
observably. Both are recorded in the fixture, so a parity test — not a report —
is what will notice when they change.

---

## 8. Changed paths

Added (untracked; nothing staged or committed):

| Path | Purpose |
| --- | --- |
| `GUARDIAN-X/_dev/fixtures/identity_vectors.json` | 27 shared golden identity vectors + canonicalization rules + implementation status |
| `GUARDIAN-X/_dev/fixtures/relocation_scenarios.json` | 22 matrix scenarios, 6 conflict scenarios, 11 probes, 9 invariants |
| `GUARDIAN-X/_dev/fixtures/vectors.py` | Shared stdlib-only loader, structure validator and golden-value oracle |
| `GUARDIAN-X/_dev/tools/build_identity_vectors.py` | Provenance builder for the golden values |
| `GUARDIAN-X/tests/test_identity_vectors.py` | Canonical golden-vector and identity-integrity suite |
| `GUARDIAN-X/tests/test_relocation_fixtures.py` | Canonical relocation, conflict and state-isolation suite |
| `GUARDIAN-X/_dev/tests/test_identity_parity.py` | Drop-in parity suite over the same fixtures |
| `GUARDIAN-X/docs/MILESTONE_P0_ENGINEERING_REPORT.md` | This report |

Modified or deleted: **none.**

---

## 9. Exit gate

| Gate | Status | Evidence |
| --- | --- | --- |
| 1. Golden vectors are deterministic | PASS | `assert_golden_consistency` in both suites; repeated computation test; `AcceptedVectorTests` |
| 2. Both distributions execute the applicable parity tests | PASS | Canonical 516/16; drop-in 278/9. Identity vectors are canonical-only because the drop-in has no identity module (F10); the drop-in executes the shared-fixture, state-isolation, state-key and identity-preservation parity tests |
| 3. No production behavior changed | PASS | `git status` lists only new untracked files; no tracked file differs from HEAD |
| 4. Existing tests remain green | PASS | 465 → 516 passed (only additions); drop-in 258 → 278 OK |
| 5. New tests are collected and actually executed | PASS | 52 canonical (51 pass + 1 platform skip), 20 drop-in; names verified in the `-v` log |
| 6. No user changes lost | PASS | Worktree was clean at start and only additions exist at end |
| 7. Complete P0 engineering report | PASS | This document |

P0 is complete. P1 has not been started.

---

## 10. Not run, and not claimed

| Item | Status | Why |
| --- | --- | --- |
| Cross-drive move (C: → D:) | **NOT RUN** | No second local volume is dedicated to this suite; rows RX-03 marked `REAL_MEDIA` |
| USB copy, removal, reinsertion | **NOT RUN** | No removable media available; RX-04, RX-36 |
| Project on USB opened on a second machine | **NOT RUN** | No second machine; RX-05, RT-37 |
| Read-only media | **NOT RUN** | RX-19 |
| Real `git clone` with a tracked identity and a known remote | **NOT RUN** | RX-08; no remote clone was performed and no network operation was part of this milestone |
| Actual power loss or real crash during relocation | **NOT RUN** | Shipped code has no relocation journal yet (F5); RT-32 remains unimplemented |
| Linux and macOS | **NOT RUN** | Windows 10 / CPython 3.15.2 only. The suites use only portable APIs, but that is a design statement, not a result |
| Python 3.11–3.13 | **NOT RUN** | Single interpreter (3.15.2) |
| Hosted CI | **NOT RUN** | No hosted run was performed in this milestone; no CI result is claimed |

Two disclosures:

1. **One transient failure was observed and not reproduced.** During the first
   full drop-in discovery run after the new module was added, the summary read
   `FAILED (failures=1, skipped=9)`; the failing test name was not captured
   because the output was filtered. Four subsequent full runs (including one
   concurrent with the sync check) returned `OK (skipped=9)`, and no failing
   test can be identified from the code. The most plausible cause was the
   fixture loader writing `_dev/fixtures/__pycache__` while the directory was
   being enumerated. That bytecode cache has been removed and both fixture
   loaders now set `sys.dont_write_bytecode` while executing, so no `.pyc` is
   written into `_dev/fixtures` at all. This is reported as an unexplained
   transient, not as a pass.
2. **Design open decisions are still open.** D1–D12 in §15.3 of the design have
   not been answered. P0 depends on none of them, but P1 does: D2 (authenticity
   wording) and D9 (two projects claiming one repository URL) constrain it most.

---

## 11. Recommended next step

P1 as scoped in design §14, and only after owner approval: wire registry and
manifest evidence into `identity recover`, add `identity explain` and
`identity fork`, and replace the bare random UUID in the v1→v2 registry
migration with a deterministic provisional id. P1 should flip the expectations
P0 recorded — `MissingSurfaceTests`, `IdentityAbsentTests` and the
`recorded gap` assertions — in the same change set, which is exactly why they
were written as explicit expectations rather than absences.
