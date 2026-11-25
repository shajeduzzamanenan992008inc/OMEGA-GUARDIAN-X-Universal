# Milestone I.2 — Documentation Reconciliation

**Report date:** 2026-11-25
**Scope:** product documentation under `GUARDIAN-X/docs/`, `GUARDIAN-X/README.md`,
the repository-root `README.md`, the release-note copies, and the historical
Milestone I report. **No source, test, workflow, configuration or packaging file
was modified.**

---

## 1. The rule applied

Three separate ideas were kept apart throughout:

| Idea | How it is recorded |
| --- | --- |
| A result that this milestone executed | stated as reproduced, with command, exit status, time and environment |
| A result that an earlier pass executed | kept, attributed to that pass, and labelled historical |
| A result nobody executed | stated as NOT_RUN or UNKNOWN, never as passing |

An old report that is a snapshot of an earlier state is labelled as a snapshot
rather than rewritten to a different conclusion. Current totals were corrected
where a document presented a stale number **as current**. Historical numbers were
never deleted; where a stale current number was replaced, the superseded value
was kept in the same sentence as an explicit historical note.

---

## 2. Verification environment

```
OS:        Windows (Git Bash)
Python:    CPython 3.15.2  (C:\Program Files\Python315\python.exe)
pytest:    9.2.0
Ruby:      not installed (ruby: command not found)
PyYAML:    not installed in the system interpreter
Git HEAD:  d55cea3  (parent e7e7686 = origin/main)
Tree:      d55cea3 changes documentation only, so code under test == e7e7686
```

Every figure below comes from a command run in this milestone. Nothing in this
report is copied forward from an earlier pass.

---

## 3. The ten reconciliation items

### 3.1 Canonical test result — 465 passed, 15 skipped

**Reproduced exactly.**

```
$ cd GUARDIAN-X && python -m pytest -q
465 passed, 15 skipped in 77.82s (0:01:17)
exit 0
```

Documentation state: already current in `TEST_RESULTS.md`,
`CI_CD_VERIFICATION.md`, `CROSS_PLATFORM_VERIFICATION.md`,
`FINAL_PROJECT_COMPLETION_REPORT.md`, `FINAL_RELEASE_READINESS_REPORT.md`,
`PROJECT_COMPLETION_TRACKER.md`, `RELEASE_BASELINE_VERIFICATION.md`,
`HOST_LIFECYCLE_CI_VALIDATION.md`, `HOST_LIFECYCLE_VERIFICATION_REPORT.md`,
`HOST_ARCHITECTURE.md`, `GUARDIAN-X/README.md`.

Documentation state: **stale** in the two files `d55cea3` missed —
`README.md` (root) and the "Honest limits" paragraph of `GUARDIAN-X/README.md`,
both still showing `340 passed, 11 skipped` as the current total. **Both
corrected** in this milestone, with the 340/11 figure retained as history.

### 3.2 Drop-in test result — 245 tests, 9 skipped

**Reproduced exactly.**

```
$ PYTHONPATH=core python -m unittest discover -s _dev/tests
Ran 245 tests in 95.557s
OK (skipped=9)
exit 0
```

All documents that carry this figure are already correct. No change needed.

### 3.3 Focused test result — 78 passed, 1 skipped

**Reproduced exactly, but only for the specific five suites the claim names.**

```
$ python -m pytest -q tests/test_host_contract.py tests/test_audit_chain.py \
    tests/test_transport_bounds.py tests/test_platform_engine.py tests/test_identity_ports.py
78 passed, 1 skipped in 4.00s
```

The 78/1 figure is therefore accurate but **under-specified as written**: it is
not "the host and security regression set", it is those five files. A wider
selection that adds the host lifecycle, filesystem, Git and CLI suites yields
`134 passed, 4 skipped`. This is recorded here rather than edited into
documents, because no document currently quotes 78/1 without context; the figure
appears only in the `d55cea3` commit message, and the Milestone I.2 commit
verification report records the qualifier.

### 3.4 Contract suite — `tests/test_host_contract.py`, 12 tests

**Reproduced exactly.**

```
$ python -m pytest -q tests/test_host_contract.py
12 passed in 0.90s
```

The file is present and tracked. This contradicts the Milestone I report's claim
that the file was absent; the Milestone I.1 review already caught this, and the
addendum added to `docs/MILESTONE_I_FINAL_REPORT.md` in this milestone records it
against the original section rather than rewriting it.

`GUARDIAN-X/docs/HOST_CONTRACT_VERIFICATION.md` was checked against this run: its
"12 tests, 12 passed" and its per-test coverage table are accurate. It was **not**
rewritten. It did need one thing: a historical-snapshot banner, because its
section 3 says the host session lifecycle is "not implemented", which was true
when written and is false now (see 3.8).

### 3.5 Historical results — preserved with provenance

Every superseded figure remains in place with an explicit label. Examples of the
retained provenance after this milestone:

- `340 passed, 11 skipped` — the completion-pass baseline, still cited in
  `TEST_RESULTS.md`, `FINAL_PROJECT_COMPLETION_REPORT.md`,
  `RELEASE_BASELINE_VERIFICATION.md`, `PROJECT_COMPLETION_TRACKER.md`, the root
  `README.md` and `GUARDIAN-X/README.md`, each labelled as the earlier pass.
- `409 passed, 12 skipped` — the release-hardening pass, still cited in
  `FINAL_RELEASE_READINESS_REPORT.md` and `PROJECT_COMPLETION_TRACKER.md` (RR-9),
  labelled as the earlier hardening pass.
- `340 passed` at `dbf8874` in `GUARDIAN-X/CHANGELOG.md` — an entry about a
  specific historical regression fix, correctly historical and left untouched.

No historical number was deleted or silently replaced.

### 3.6 Hosted CI — no success claimed

No hosted run was observed. Details and the classification of each workflow job
are in `docs/MILESTONE_I2_CI_EVIDENCE_REPORT.md`. The documentation was checked
for any claim of a passing CI run; every file that addresses hosted CI already
says `NOT_RUN`, `UNVERIFIED` or `BLOCKED`. One wording in `TEST_RESULTS.md`
implied that a run URL simply had not been recorded; it now states plainly that
no run was executed or observed and that the workflow-run API is not readable
without credentials from this environment.

### 3.7 Cross-platform — Windows CPython 3.15.2 vs the declared 3.11–3.13 matrix

This distinction was already handled correctly in
`CROSS_PLATFORM_VERIFICATION.md` and `FINAL_PROJECT_COMPLETION_REPORT.md` §11:
3.15.2 is outside the declared classifier range, so it is evidence on an
unsupported interpreter and not proof for 3.11–3.13. Nothing overstated it.

Two numeric defects were found and fixed in that file:

- The skip heading said `Skips (12 canonical, 9 drop-in)`. The canonical count is
  **15**. Corrected.
- The skip reasons listed only symlinks, POSIX modes and directory symlinks.
  The measured breakdown of the 15 canonical skips is: 12 symlink/directory
  symlink, 2 POSIX file mode, and 1 where "Windows drive-relative rejection is
  covered separately". Corrected to the measured breakdown.

`TEST_RESULTS.md` had the same two defects in its own skip section, plus a
"All 20 skips" total that no longer summed (2 + 9 + 9). Corrected to
`All 24 skips (15 canonical, 9 drop-in)` with the real breakdown.

### 3.8 Host lifecycle — Phase 3 state vs Milestone H implementation

This was the most substantive documentation defect, and the only one where two
committed files contradicted each other.

The Phase 3 state: a host **registry/metadata** contract only, 12 tests, no
session lifecycle. `tests/test_host_contract.py` records it.

The Milestone H state: a real session lifecycle plus two read-only local hosts.
Evidence present in the tree:

- `src/ogx/hosts/lifecycle.py` — `HostSessionManager`, 12 states, health,
  recovery, audit sink
- `src/ogx/hosts/adapters/filesystem_runtime.py`, `git_runtime.py`
- test suites: `test_host_lifecycle.py` (13), `test_host_filesystem_runtime.py`
  (16, 3 skipped), `test_host_git_runtime.py` (21), `test_host_cli_lifecycle.py`
  (6)

`GUARDIAN-X/README.md` and `GUARDIAN-X/docs/HOST_ARCHITECTURE.md` describe the
Milestone H state. But `FINAL_PROJECT_COMPLETION_REPORT.md` §23 still listed as
blocker 4 — written by `d55cea3` — that "no full host
session/health/read/disconnect/recovery lifecycle is implemented". That commit
edited `HOST_ARCHITECTURE.md` in the same diff, so the two files disagreed within
one commit.

Corrections made:

- `FINAL_PROJECT_COMPLETION_REPORT.md` §23 item 4 rewritten to separate the two
  snapshots explicitly: Phase 3 had the registry/metadata contract only;
  Milestone H implemented the lifecycle and the two local hosts; no host is
  operational for writes, execution, remote or branch operations; live-latency
  health, cross-process failover and remote/SSH/Docker/Kubernetes hosts remain
  unimplemented.
- `HOST_CONTRACT_VERIFICATION.md` given a historical-snapshot banner naming the
  superseding suites, without altering sections 1 and 2.
- `PROJECT_COMPLETION_TRACKER.md` RR-6 and P3-4 annotated to distinguish the
  hardening-point state from the later lifecycle, and to state that no contract
  suite covers the cloud/remote families.

### 3.9 Read-only local hosts — no implied write, execution, remote or branch support

Checked in every place a host is described. The documents already say
"read-only" and enumerate the unsupported operations. No document implies
write, execution, push, branch or remote support for a local host.

Two additions were made to keep this explicit after the lifecycle correction:
`TEST_RESULTS.md` now states that the lifecycle exists **and** that the hosts are
read-only with no write/exec/remote/branch support and are not declared
operational in that sense; `GUARDIAN-X/README.md`'s corrected limits paragraph
repeats that remote, SSH, Docker and Kubernetes hosts are not implemented.

### 3.10 Release notes — historical local-only notes vs committed release documentation

`GUARDIAN-X/.github/RELEASE_NOTES.md` and its mirror
`GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` both described themselves as
"branch-local, uncommitted" / "local only — do not ship or commit". Both files
are **tracked in Git** and present at `e7e7686`:

```
$ git ls-files | grep RELEASE_NOTES
GUARDIAN-X/.github/RELEASE_NOTES.md
GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md
```

The label was demonstrably false, so it was corrected:

- `GUARDIAN-X/.github/RELEASE_NOTES.md` — heading changed to "committed"; the
  "local note only / do not commit" block replaced with a tracking-status block
  that states the file is tracked at `e7e7686` and carried into `d55cea3`, that
  the old wording described the moment before the Milestone H work was committed
  and is superseded, that the figures below are the in-window results for that
  pass rather than a standing total, and that Milestone I.2 re-verified them at
  the same tree (465/15, 245 OK 9 skipped, 68/3) with hosted CI still not run.
- `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` — heading changed to
  "committed mirror". Its body is a duplicate and was otherwise left alone.

Neither file is a release artefact and neither is published anywhere.

---

## 4. Additional corrections found during the pass

### 4.1 A cross-reference that resolved to a non-existent path

`FINAL_PROJECT_COMPLETION_REPORT.md` cited the Milestone I.1 review as:

```
`docs/../docs/MILESTONE_I1_*.md` from the repository root
```

`docs/../docs/` is `docs/`, so the expression is a no-op that resolves to the
**repository-root** `docs/` only when the reader is at the root. Read from
`GUARDIAN-X/`, it resolves to `GUARDIAN-X/docs/`, where no Milestone report
exists. Corrected to `../docs/MILESTONE_I1_*.md` from `GUARDIAN-X/`, with an
explicit note that this is the repository-root `docs/`, not `GUARDIAN-X/docs/`.

This is the same class of defect the Milestone I.1 review identified; it
survived into the commit and is now fixed.

### 4.2 One residual discrepancy recorded but deliberately not "fixed"

The drop-in archive file count is stated as **279** in the root `README.md` and
`GUARDIAN-X/docs/TEST_RESULTS.md`, and as **280** in
`FINAL_PROJECT_COMPLETION_REPORT.md` (twice), `PROJECT_COMPLETION_TRACKER.md`
(P13-3) and `RELEASE_BASELINE_VERIFICATION.md`.

Only one of those can be right, and which one cannot be established without
running `_dev/tools/make_dist.py`, which writes an archive and was outside this
milestone's documentation-only scope. Rewriting three files to match two others
would have replaced a documented observation with a guess.

Recorded instead: the figures disagree, the archive was not rebuilt, and the
number should be settled by one `make_dist.py` run in the pass that is allowed
to build artefacts. This is the one known documentation inconsistency this
milestone intentionally left open.

Also checked and confirmed correct: the root `README.md` line "Architecture and
distribution checks (included in the suites) — 17 passed" reproduces exactly
(`python -m pytest -q _dev/tests/test_architecture.py` → `17 passed`).

### 4.3 `HOST_LIFECYCLE_VERIFICATION_REPORT.md` left as a historical record

Its §I decision `READY_FOR_EXTERNAL_VALIDATION` and its §D numbers are a
point-in-time record of that milestone. Its content is consistent with what this
milestone re-verified, so it was **not** edited. It is noted here only so that a
future pass does not treat its "not re-installed or re-extracted by this
execution" caveat as current.

---

## 5. Exact list of documentation files changed

| File | Change |
| --- | --- |
| `README.md` (repository root) | canonical total 340/11 → 465/15 with reproduction provenance and historical label; skip explanation 11 → 15 with breakdown |
| `GUARDIAN-X/README.md` | "Honest limits" totals 340 (11) → 465 (15), historical label, read-only/remote-host reminder |
| `GUARDIAN-X/docs/TEST_RESULTS.md` | added re-verification provenance line; "All 20 skips" → "All 24 skips (15 canonical, 9 drop-in)"; skip breakdown corrected and completed; host-adapters paragraph replaced with the contract-suite + read-only-lifecycle statement; hosted-CI wording made explicit |
| `GUARDIAN-X/docs/CROSS_PLATFORM_VERIFICATION.md` | skip heading 12 → 15; skip reason bullets given measured counts and the drive-relative skip |
| `GUARDIAN-X/docs/FINAL_PROJECT_COMPLETION_REPORT.md` | malformed Milestone I.1 cross-reference fixed; §21 "11 canonical skips" → measured 15 with breakdown; §23 blocker 4 rewritten to separate Phase 3 from Milestone H |
| `GUARDIAN-X/docs/HOST_CONTRACT_VERIFICATION.md` | historical-snapshot banner added; sections 1–2 untouched |
| `GUARDIAN-X/docs/PROJECT_COMPLETION_TRACKER.md` | RR-6 and P3-4 annotated for the Phase 3 → Milestone H transition |
| `GUARDIAN-X/.github/RELEASE_NOTES.md` | "uncommitted / local only" corrected to tracked + superseded note + re-verification line |
| `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` | mirror heading corrected to "committed mirror" |
| `docs/MILESTONE_I_FINAL_REPORT.md` | correction addendum appended; original body and decision untouched |

Ten files, **all documentation**. No source, test, workflow, configuration or
packaging file was touched, which is re-verified in §7.

## 6. Files reviewed and deliberately left unchanged

| File | Why unchanged |
| --- | --- |
| `GUARDIAN-X/docs/CI_CD_VERIFICATION.md` | already 465/15; already states no workflow was executed and YAML was not parsed in that pass |
| `GUARDIAN-X/docs/FINAL_RELEASE_READINESS_REPORT.md` | already 465/15 with the 409/12 historical note; blockers already accurate |
| `GUARDIAN-X/docs/RELEASE_BASELINE_VERIFICATION.md` | already 465/15 with the 340/11 historical note |
| `GUARDIAN-X/docs/HOST_ARCHITECTURE.md` | already describes the lifecycle as implemented and labels the local hosts read-only |
| `GUARDIAN-X/docs/HOST_LIFECYCLE_CI_VALIDATION.md` | already 465/15, 245 OK (9 skipped) |
| `GUARDIAN-X/docs/HOST_LIFECYCLE_VERIFICATION_REPORT.md` | accurate point-in-time record; §4.2 above |
| `GUARDIAN-X/CHANGELOG.md` | its only test figure is a historical entry at `dbf8874` and is labelled as such |
| `GUARDIAN-X/docs/ROADMAP.md` | its `336 passed, 11 skipped` line sits in a roadmap whose own status banner already declares the whole document a historical roadmap written against `0.7.1a1`; the number is scoped by that banner and is not a current claim |
| `GUARDIAN-X/docs/PROJECT_COMPLETION_BASELINE.md` | describes an earlier baseline; its numbers are contextual, not current claims |
| `GUARDIAN-X/docs/RELEASE_INTEGRITY_REPORT.md` | its "uncommitted hardening changes" phrase is a true description of the artefacts it hashed, not a tracking claim about itself |
| `GUARDIAN-X/docs/UNIVERSAL_AUDIT.md` | its "uncommitted moves/deletions" line describes a past worktree state, not the file's own tracking status |
| root `docs/MILESTONE_I1_*.md` | the I.1 review's own conclusions are left as written; its imprecision about the nested workflows is recorded in this milestone's CI report and in the Milestone I addendum |

Note on the three `uncommitted` hits: only the two release-note files were
claiming their **own** files were uncommitted, and those were false. The other
three describe the state of *other* content at a past point in time and are
accurate.

## 7. Confirmation that nothing else changed

```
$ git status --porcelain=v1
 D GUARDIAN-X/LICENSE
 M GUARDIAN-X/.github/RELEASE_NOTES.md
 M GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md
 M GUARDIAN-X/README.md
 M GUARDIAN-X/docs/CROSS_PLATFORM_VERIFICATION.md
 M GUARDIAN-X/docs/FINAL_PROJECT_COMPLETION_REPORT.md
 M GUARDIAN-X/docs/HOST_CONTRACT_VERIFICATION.md
 M GUARDIAN-X/docs/PROJECT_COMPLETION_TRACKER.md
 M GUARDIAN-X/docs/TEST_RESULTS.md
 M README.md
?? docs/
```

Every `M` is a Markdown file. The `D GUARDIAN-X/LICENSE` entry pre-dates this
milestone and was left exactly as found (`docs/MILESTONE_I2_GIT_TOPOLOGY_REPORT.md`
§2.6). No `src/`, `core/`, `tests/`, `_dev/`, `.github/workflows/` or
`pyproject.toml` path appears.

The harness used to apply the edits (`docs/_i2_apply_edits.py`) was a scratch
script and was deleted after the run; it is not part of the repository.

## 8. Verdict

Documentation reconciliation is **complete for every item in scope**. Ten
documentation files corrected, eleven reviewed and left unchanged with reasons,
no source or test touched, no historical result deleted, no hosted CI success
claimed, and no host capability overstated.

The two defects that mattered most were substantive rather than cosmetic: the
committed claim that the host lifecycle is unimplemented (contradicted by code
and by another file in the same commit), and the two files that still carried
`340 / 11` as the current total.

*Point-in-time documentation reconciliation. Not a commit or push authorization.*
