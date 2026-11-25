# Milestone I.2 — Report Directory Layout

**Report date:** 2026-11-25
**Scope:** locate and classify every milestone report; no file was moved,
renamed or deleted.

---

## 1. The three candidate locations, inspected separately

### 1.1 Repository-root `docs/`

```
$ ls -la docs
MILESTONE_I1_FINAL_REPORT.md           21529 bytes
MILESTONE_I1_INDEPENDENT_REVIEW.md     19177 bytes
MILESTONE_I1_REPOSITORY_STATE.md        5165 bytes
MILESTONE_I1_SECURITY_EVIDENCE.md      14868 bytes
MILESTONE_I_BASELINE_REPORT.md         14154 bytes
MILESTONE_I_CI_READINESS_REPORT.md      9097 bytes
MILESTONE_I_DOCUMENTATION_AUDIT.md     10686 bytes
MILESTONE_I_FINAL_REPORT.md            11136 bytes
```

Status: **untracked, not ignored.** `git ls-files docs` is empty;
`git status --porcelain -uall docs` lists all eight as `??`;
`git check-ignore` matches nothing. This directory exists only on this
machine and in this working tree.

### 1.2 `GUARDIAN-X/docs/`

80 tracked Markdown/JSON/SKILL files, including the whole project documentation
set (`README.md`, `TEST_RESULTS.md`, `HOST_ARCHITECTURE.md`,
`FINAL_PROJECT_COMPLETION_REPORT.md`, `PROJECT_COMPLETION_TRACKER.md`, …) plus
`archive/`, `examples/`, `kit/` and `schemas/` subdirectories.

Status: **tracked.** `git ls-files GUARDIAN-X/docs` lists every file, and
`git status` reports no modification, deletion or addition in this tree.

This directory contains **no** `MILESTONE_I*` file.

### 1.3 `GUARDIAN-X/GUARDIAN-X/docs/`

**Does not exist.** The only contents of `GUARDIAN-X/GUARDIAN-X/` are:

```
$ ls -la GUARDIAN-X/GUARDIAN-X
.github

$ find GUARDIAN-X/GUARDIAN-X/.github -type f
GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md
```

Status: **tracked**, one file. A duplicate mirror of
`GUARDIAN-X/.github/RELEASE_NOTES.md`, not a documentation tree.

---

## 2. Location of each required report

| Required report | Location | Tracking state |
| --- | --- | --- |
| Milestone I baseline report | `docs/MILESTONE_I_BASELINE_REPORT.md` (root) | untracked |
| Milestone I documentation audit | `docs/MILESTONE_I_DOCUMENTATION_AUDIT.md` (root) | untracked |
| Milestone I CI readiness report | `docs/MILESTONE_I_CI_READINESS_REPORT.md` (root) | untracked |
| Milestone I final report | `docs/MILESTONE_I_FINAL_REPORT.md` (root) | untracked |
| Milestone I.1 independent review | `docs/MILESTONE_I1_INDEPENDENT_REVIEW.md` (root) | untracked |
| Milestone I.1 final report | `docs/MILESTONE_I1_FINAL_REPORT.md` (root) | untracked |

All six are present, readable and untracked. None is ignored. None is missing.

There are two further I.1 reports not in the required list —
`MILESTONE_I1_REPOSITORY_STATE.md` and `MILESTONE_I1_SECURITY_EVIDENCE.md` —
also at the root `docs/`, also untracked.

---

## 3. Classification of every candidate path

| Path | Tracked | Untracked | Ignored | Absent |
| --- | --- | --- | --- | --- |
| root `docs/MILESTONE_I_*.md` (4 files) | — | ✔ | — | — |
| root `docs/MILESTONE_I1_*.md` (4 files) | — | ✔ | — | — |
| `GUARDIAN-X/docs/**` (80 files) | ✔ | — | — | — |
| `GUARDIAN-X/docs/MILESTONE_I*` | — | — | — | ✔ |
| `GUARDIAN-X/GUARDIAN-X/docs/` | — | — | — | ✔ |
| `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` | ✔ | — | — | — |
| root `docs/` as a directory | — | ✔ | — | — |

`GUARDIAN-X/docs/` and root `docs/` are **different trees with the same
basename**. A relative `docs/...` path therefore means one thing from the
repository root and another from `GUARDIAN-X/`. That ambiguity has already
produced at least one wrong citation: the Milestone I.1 commit wrote
`docs/../docs/MILESTONE_I1_*.md` into
`FINAL_PROJECT_COMPLETION_REPORT.md`, which resolves to
`GUARDIAN-X/docs/../docs/`, i.e. `GUARDIAN-X/docs/`, where no Milestone report
exists. This milestone corrected that citation.

---

## 4. Canonical location for future milestone reports

**`docs/` at the repository root is the canonical location for milestone
reports.** Rationale, in order of weight:

1. **Product versus process.** `GUARDIAN-X/docs/` is the documentation that
   ships with the product: it is inside the package workspace, referenced by
   `GUARDIAN-X/README.md`, and included in release/audit evidence.
   Milestone reconciliation reports are project-process records, not product
   documentation, and do not belong in the shipped set.
2. **Precedent.** Every existing Milestone I and I.1 report is at the root
   `docs/`. Moving them now would break the citations in the I.1 reports and in
   `FINAL_PROJECT_COMPLETION_REPORT.md`.
3. **Path scope.** The root `docs/` is unambiguous from the repository root and
   cannot be confused with the product doc tree.

**`GUARDIAN-X/docs/` remains the canonical location for product documentation**
(architecture, contracts, test results, host, release, security). Corrections to
those files stay in `GUARDIAN-X/docs/`; this milestone followed that rule.

### Citation convention to adopt

- From the repository root: `docs/MILESTONE_I2_FINAL_REPORT.md`
- From `GUARDIAN-X/`: `../docs/MILESTONE_I2_FINAL_REPORT.md`, and product docs
  as `docs/TEST_RESULTS.md`

Never write `docs/...` for a milestone report while the shell is inside
`GUARDIAN-X/`, and never cite a milestone report as `GUARDIAN-X/docs/...`.

---

## 5. Tracking state is a separate problem from location

The canonical location is correct; its contents are simply not committed.
All eight prior reports and the five reports produced by this milestone are
**untracked**. Consequences until someone commits them:

- A fresh clone has no milestone history at all.
- The reports exist only in this working tree and are not backed up by the
  remote.
- Any reviewer cloning the repository will not find the reports the I.1 review
  and this milestone reference.

Committing them is a decision for the owner; this milestone did not commit, per
its boundaries. Because they are untracked and not ignored, they are preserved
across `git status` and cannot be lost by a checkout of tracked files — but they
*can* be lost by a `git clean -fdx`, which is one more reason not to run one.

---

## 6. Actions deliberately not taken

- No report was moved between `docs/` trees.
- No report was renamed.
- No report was deleted.
- No report was staged or committed.
- No `.gitignore` rule was added or changed.

---

## 7. Verdict

The report layout is **internally consistent but uncommitted**. Six of six
required reports exist at the repository-root `docs/`; none is ignored; none is
missing. `GUARDIAN-X/GUARDIAN-X/docs/` does not exist. The canonical location
going forward is the repository-root `docs/` for milestone reports and
`GUARDIAN-X/docs/` for product documentation.

*Point-in-time layout record. Not a commit or push authorization.*
