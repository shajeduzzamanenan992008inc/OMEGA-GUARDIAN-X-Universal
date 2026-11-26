# Milestone I.5 — Documentation Audit

Date: 2026-11-26
Audited HEAD: `a004348cd371fc1a42aefcaacf7e31b577026476`

## 1. Method

1. Read the live root `README.md` and `GUARDIAN-X/README.md` in full.
2. Searched every tracked file for the stale tokens and figures:
   `324 files`, `305 from a clean`, `omega-shield/GUARDIAN-X/.github`,
   `757d6b6`, `245`, `RELEASE_NOTES`.
3. Searched for cross-references broken by commit `a004348`, which moved the
   I.3/I.4 reports out of root `docs/`:
   `git grep -n "\.\./\.\./docs/"`.
4. Ran a relative-link checker over `GUARDIAN-X/docs/**/*.md`.
5. Audited `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` against its
   canonical counterpart and against every tooling consumer.

## 2. Stale statements found and corrected

| File | Statement before | Correction | Evidence |
|---|---|---|---|
| `README.md` | “Drop-in zip — 324 files in this working tree (305 from a clean `757d6b6` checkout); it includes `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md`, which the CI forbidden-path assertion rejects.” | Replaced with **326 files** from a clean `a004348` export, 0 forbidden hits, required entries present, generator fix in `06ffef4`, and the new regression suite. | Clean-export build |
| `README.md` | “Hosted GitHub Actions has **not** executed a job for this working tree: push-triggered runs exist for `757d6b6`…” | Rewritten as an explicit local-only statement: last observed runs were for `757d6b6` and were refused before start; the current tree could not be queried (`gh` absent, unauthenticated API rate-limited). | Tool output |
| `README.md` | Drop-in suite “**245 tests**” | **258 tests**, with the 13 new archive tests named. | Suite run |
| `GUARDIAN-X/README.md` | “245 drop-in tests passed” and “245-test drop-in suite” | **258** in both places, noting the 13 new tests. | Suite run |
| `GUARDIAN-X/docs/MILESTONE_I4_PACKAGING_BASELINE.md` | HEAD `757d6b6`; fix uncommitted | Dated I.5 correction block: fix in `06ffef4`, HEAD/origin `a004348`, reports relocated by `a004348`. Original text preserved below. | `git show` |
| `GUARDIAN-X/docs/MILESTONE_I4_ARCHIVE_VERIFICATION.md` | “working-tree fix, uncommitted”; 304-file after archive | Dated correction block: committed generator yields **326 entries**, SHA-256 `21ebccb4…`, 0 forbidden; 304 was the `757d6b6` measurement before the reports shipped. | Clean-export build |
| `GUARDIAN-X/docs/MILESTONE_I4_FINAL_REPORT.md` | Limitation 4: “No archive-specific unit test exists” | Dated correction block: resolved by `_dev/tests/test_make_dist.py`; hosted CI and wheel remain unverified. | Test run |

## 3. Statements verified accurate and left unchanged

- `GUARDIAN-X/README.md`: “CI lives only in the repository root
  `.github/workflows/` … The nested `GUARDIAN-X/.github/` directory is reusable
  kit content and contains no active workflows.” True: the nested tree holds one
  Markdown file and no workflow.
- `GUARDIAN-X/README.md`: “LICENSE currently absent.” Matches the user's
  explicit instruction that LICENSE is not needed at this stage.
- Root `README.md`: release `0.8.0a1`, Alpha maturity, declared Python
  3.11–3.13. Matches `VERSION`, `pyproject.toml` and `src/ogx/__init__.py`.
- `GUARDIAN-X/README.bn.md`: no `324`/`305`/nested-`.github` archive claims, so
  no stale packaging statement to correct.

## 4. Cross-references broken by the report relocation

Commit `a004348` moved the I.3/I.4 reports from root `docs/` to
`GUARDIAN-X/docs/` but left seven `../../docs/MILESTONE_I3_*` references and one
description of the I.1 reports as living in the root tree. Those paths no longer
resolve. Corrected to same-directory references:

| File | Occurrences |
|---|---|
| `GUARDIAN-X/docs/CI_CD_VERIFICATION.md` | 1 |
| `GUARDIAN-X/docs/FINAL_PROJECT_COMPLETION_REPORT.md` | 2 path references + 1 layout description |
| `GUARDIAN-X/docs/FINAL_RELEASE_READINESS_REPORT.md` | 2 |
| `GUARDIAN-X/docs/PROJECT_COMPLETION_TRACKER.md` | 1 |
| `GUARDIAN-X/docs/TEST_RESULTS.md` | 1 |

`git grep -n "\.\./\.\./docs/"` now returns no hits. The remaining relative-link
checker findings are six intentionally old `docs/…` links inside
`GUARDIAN-X/docs/archive/README-Guardian-X-0.7.md`; that is an archived
document and was left unchanged.

## 5. Historical records preserved

The following keep their original dated figures and are not presented as
current:

- `GUARDIAN-X/docs/MILESTONE_I3_*.md` (5 files) — the 324/305-file pre-fix
  archive measurements and the zero-step hosted runs.
- `GUARDIAN-X/docs/TEST_RESULTS.md` — dated suite results.
- `GUARDIAN-X/docs/PROJECT_COMPLETION_TRACKER.md` — historical status entries.
- `GUARDIAN-X/docs/FINAL_PROJECT_COMPLETION_REPORT.md`,
  `FINAL_RELEASE_READINESS_REPORT.md`, `CI_CD_VERIFICATION.md` — their dated
  I.3 updates are preserved; only broken paths were repaired.
- `GUARDIAN-X/.github/RELEASE_NOTES.md` — its “in-window results for that pass,
  not a standing re-verified total” header is accurate history.

Current measurements are recorded only in the I.5 reports and in the README
“Verification” section, which now distinguishes current results from history.

## 6. Nested duplicate directory audit (Task 4)

Target: `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md`

| Question | Finding | Evidence |
|---|---|---|
| Is it a duplicate? | Near-duplicate **subset**. The body is identical to `GUARDIAN-X/.github/RELEASE_NOTES.md`; the canonical file has a 9-line tracking/re-verification header the nested copy lacks. 1595 vs 1010 bytes. | `diff -u`, `wc -c` |
| Does any tooling reference it? | **No.** No hits in `.github/`, `_dev/`, `scripts/`, `devkit/`, `link/` or `pyproject.toml`. `validate_config.rb` globs `.github/**`, `link/templates/**` and agent files — never the nested tree. | `git grep` |
| Is it referenced by documentation? | Only **observationally**, in the I.2/I.3/I.4 milestone reports as a path-layout or packaging finding. No document treats it as an input or deliverable. | `git grep` |
| Is it required by a future product layout? | **No declared role.** The documented layout has a single `GUARDIAN-X/` level; a second one has no consumer, and `.github` is excluded from the drop-in by policy, so it cannot be a distribution input. | `GUARDIAN-X/README.md` layout; `make_dist.py` |
| Was it accidentally tracked? | Consistent with an accidental copy: introduced in `e7e7686`, then its heading was edited independently in `757d6b6` (“committed” → “committed mirror”), diverging from the canonical file. No sync mechanism exists. | `git log --follow` |
| Legitimate ownership or distribution purpose? | **None found.** The canonical release notes are `GUARDIAN-X/.github/RELEASE_NOTES.md`. | Audit above |

## 7. Disposition and recommendation

**Disposition:** documented, not removed.

**Recommendation:** remove the file in a separately authorized change:

```bash
git rm GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md
```

That would leave `GUARDIAN-X/GUARDIAN-X/` empty; Git does not track directories,
so the stray level disappears from the tree.

It was **not** removed here because deleting a tracked file is outside this
pass's authorization and the directive asks for a recommendation. Packaging
correctness does not depend on the cleanup: the generator excludes `.github` at
any depth, `test_github_is_excluded_at_any_depth` pins it, and the unmodified CI
token assertion rejects it.

## 8. Remaining documentation risks

1. Milestone reports are now split: I.1–I.4 in `GUARDIAN-X/docs/`, I.5 in
   repository-root `docs/`. This follows the directive's output paths and the
   repository layout rule; consolidating them is a user decision.
2. The I.3 reports still contain the pre-fix 324/305 figures. They are dated
   records, not current claims; the README and I.4 corrections point to the
   current measurements.
3. `docs/archive/README-Guardian-X-0.7.md` retains six intentionally old
   relative links; archived content was not rewritten.
4. Hosted CI remains unverified, so no document claims a hosted pass for the
   current tree.
