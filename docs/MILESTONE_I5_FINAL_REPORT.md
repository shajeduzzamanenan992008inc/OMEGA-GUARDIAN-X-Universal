# Milestone I.5 — Final Report: Packaging Regression Protection

Date: 2026-11-26
Baseline commit: `a004348cd371fc1a42aefcaacf7e31b577026476`
Final commit observed: `a004348cd371fc1a42aefcaacf7e31b577026476` (no commit made)
Milestone status: **PASS for the local acceptance criteria**; hosted CI **BLOCKED**

> **Phase 0 correction (2026-11-26).** The I.5 changes described here were
> committed after this report was written, in
> `36e56c0c6de4f246626cd8f57f56e3fc5bbaa40f` ("chore: update repository files",
> parent `a004348`), which is now HEAD and `origin/main`. `a004348` remains the
> **execution baseline** for every measurement below; `36e56c0` is the
> **delivery commit**. The statements in §1, §12 and §14 that no commit was made,
> and the untracked-file list in §12, describe the I.5 session state only, not
> the current repository. Phase 0 independently re-executed at `36e56c0`:
> canonical 465 passed / 15 skipped, drop-in 258 tests OK (9 skipped), archive
> suite 13 OK, clean-export archive 326 entries / 0 forbidden, wheel 167 entries
> with a clean-venv install. See `docs/MASTER_BASELINE_AUDIT.md` and
> `docs/PHASE_0_COMPLETION_REPORT.md`.

## 1. Summary

The drop-in archive generator now has a dedicated regression suite. The stale
I.3 packaging statements in the root README and the stale I.4 baselines are
corrected against measured evidence. The nested duplicate directory has a
documented disposition and a removal recommendation. No CI assertion, workflow,
test, runtime source file, `pyproject.toml` or LICENSE entry was weakened or
changed.

## 2. What changed

| File | Status | Change |
|---|---|---|
| `GUARDIAN-X/_dev/tools/make_dist.py` | modified | Extracted `build_archive(root, out)`; `main()` reads VERSION at call time. Exclusion policy, prefix and CLI unchanged. |
| `GUARDIAN-X/_dev/tests/test_make_dist.py` | new | 13-test archive regression suite using synthetic fixtures. |
| `README.md` | modified | Corrected the drop-in archive statement (326 files, 0 forbidden), the hosted-CI statement, and the drop-in test count (258). |
| `GUARDIAN-X/README.md` | modified | Drop-in test count 245 → 258 in both places. |
| `GUARDIAN-X/docs/MILESTONE_I4_PACKAGING_BASELINE.md` | modified | Dated I.5 correction: fix committed in `06ffef4`, HEAD `a004348`. |
| `GUARDIAN-X/docs/MILESTONE_I4_ARCHIVE_VERIFICATION.md` | modified | Dated I.5 correction: 326 entries, SHA-256 `21ebccb4…`, 0 forbidden. |
| `GUARDIAN-X/docs/MILESTONE_I4_FINAL_REPORT.md` | modified | Dated I.5 correction: archive-test limitation resolved. |
| `GUARDIAN-X/docs/CI_CD_VERIFICATION.md` | modified | Repaired a cross-reference broken by the report relocation. |
| `GUARDIAN-X/docs/FINAL_PROJECT_COMPLETION_REPORT.md` | modified | Repaired 2 path references and 1 layout description. |
| `GUARDIAN-X/docs/FINAL_RELEASE_READINESS_REPORT.md` | modified | Repaired 2 path references. |
| `GUARDIAN-X/docs/PROJECT_COMPLETION_TRACKER.md` | modified | Repaired 1 path reference. |
| `GUARDIAN-X/docs/TEST_RESULTS.md` | modified | Repaired 1 path reference. |
| `docs/MILESTONE_I5_BASELINE_REPORT.md` | new | Baseline evidence. |
| `docs/MILESTONE_I5_REGRESSION_TEST_REPORT.md` | new | Test design, mapping, results. |
| `docs/MILESTONE_I5_DOCUMENTATION_AUDIT.md` | new | Stale-statement audit and nested-duplicate disposition. |
| `docs/MILESTONE_I5_FINAL_REPORT.md` | new | This file. |

Not changed: `.github/workflows/*`, `GUARDIAN-X/tests/*`, `GUARDIAN-X/src/*`,
`GUARDIAN-X/core/*`, `GUARDIAN-X/pyproject.toml`, `GUARDIAN-X/LICENSE`,
application behavior, license metadata.

## 3. Preserved user changes

The user's earlier work — the 14 relocated I.1/I.2 reports, the I.3/I.4 report
creation, and the eight documentation corrections — is already committed in
`06ffef4` and `a004348`. The baseline tree was clean, and no reset, clean,
destructive checkout, restore or stash was run. Nothing user-owned was removed
or overwritten.

## 4. Implemented fixes

1. **Archive regression protection.** `build_archive(root, out)` is exercised by
   13 tests covering depth-independent `.github`/`.git`/`_dev`/`tests`/`src`/
   cache exclusion, `*.egg-info`, required runtime entries, the `omega-shield/`
   prefix, exact content equality, traversal safety, the CI token policy and
   deterministic ordering.
2. **Accurate README.** The claim that the fixed archive still contains
   `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md` and the 324/305-file
   figures are gone. The README now separates clean-export from working-tree
   results and states that verification was local.
3. **Corrected I.4 baselines.** All three I.4 reports carry a dated correction
   naming `06ffef4` as the fix commit and `a004348` as HEAD/origin, with current
   archive and test evidence. Original dated records are preserved.
4. **Repaired relocation links.** Seven `../../docs/` references and one layout
   description no longer point at the removed root tree.
5. **Nested duplicate disposition.** Documented as an accidental, diverged
   near-duplicate with no consumer, plus an authorized-removal recommendation.

## 5. Tests executed and results

cwd = `GUARDIAN-X/` unless noted.

| Check | Command | Result | Classification |
|---|---|---|---|
| New archive suite | `PYTHONPATH=core python -m unittest discover -s _dev/tests -p "test_make_dist.py"` | Ran 13, OK, 1.795 s, exit 0 | PASS |
| Drop-in suite | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | Ran 258, OK (skipped=9), 59.092 s, exit 0 | PASS |
| Canonical suite | `python -m pytest -q` | 465 passed, 15 skipped, 74.76 s, exit 0 | PASS |
| Compile | `python -m compileall -q src core tests _dev/tests _dev/tools` | exit 0 | PASS |
| Sync | `python _dev/tools/sync_core_from_src.py --check` | OK, `0.8.0a1`, exit 0 | PASS |
| Clean-export archive | `git archive HEAD` → committed generator | 326 entries, SHA-256 `21ebccb4…`, 0 forbidden | PASS |
| Refactor equivalence | same export → I.5 generator | 326 entries, identical list, identical bytes | PASS |
| CI forbidden assertion | exact `ci.yml` token tuple | 0 hits | PASS (local) |
| CI required assertion | `omega-shield/init.py`, `omega-shield/core/ogx/__init__.py` | both present | PASS (local) |
| Archive install smoke | extract archive; `OGX_HOME=<outside host> python init.py status` | exit 0, core `0.8.0a1`, state outside host | PASS |
| Archive CLI | `PYTHONPATH=core python -m ogx --version` from the extraction | `ogx 0.8.0a1`, exit 0 | PASS |
| Hosted CI | `gh run list` / Actions API | `gh` absent; API HTTP 403 rate-limited | BLOCKED |
| Wheel build/install | — | not rebuilt in I.5 | NOT_RUN |
| Python 3.11/3.12/3.13 | — | only 3.15.2 available | NOT_RUN |
| Linux / macOS | — | not available locally | NOT_RUN |

The 15 canonical and 9 drop-in skips are the pre-existing Windows platform
gates. No test was skipped to make this milestone pass.

## 6. Archive evidence

| Build source | Entries | SHA-256 | Forbidden | Required |
|---|---|---|---|---|
| Clean `a004348` export, committed generator | 326 | `21ebccb43d330264892bfdf9bc1f0abecb566057fa2c91c70850a8125e9ba7ee` | 0 | present |
| Same export, I.5 refactored generator | 326 | `21ebccb43d330264892bfdf9bc1f0abecb566057fa2c91c70850a8125e9ba7ee` | 0 | present |
| Working tree, I.5 generator | 326 | (mtimes differ; entry list identical) | 0 | present |

`omega-shield/docs/` contains 98 entries. The nested `.github` mirror is absent
from every archive. The SHA-256 values are per-export: ZIP headers record
extraction mtimes, and a second clean export of `a004348` produced
`403f683dfff25022fd1ab5c79bde3a3dbb04d4b9038da703484c737a362f22c9` with the same
326-entry list. Entry-list determinism is the stable property; byte equality
holds for repeated builds of one export.

## 7. Platform and environment

Windows 10.0.19045 (AMD64), CPython 3.15.2, pip 26.3, Git 2.56.0.windows.4,
bash (Git Bash). The declared support range is Python 3.11–3.13; those versions
were not executed. Linux and macOS were not executed.

## 8. Security findings

- No new security defect was introduced. The change narrows what ships.
- The archive contains no `.env`, `.pyc`, database, wheel, zip, Git metadata,
  test, CI or release entry; this is enforced by policy tests and the CI tokens.
- No secret was read, printed or written. The unauthenticated GitHub API call
  used no credentials.
- CI workflow permissions and pinned actions were not modified.

## 9. Remaining defects

1. `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` is still tracked. It no
   longer ships; removal is recommended but not authorized here.
2. Hosted CI has not been verified for `a004348`.
3. The wheel was not rebuilt in this milestone; its last verified state is the
   I.3 167-file build with a clean-venv install.
4. ZIP bytes record source mtimes, so byte-level reproducibility across exports
   is not guaranteed. Entry order is deterministic per platform.
5. Cross-platform entry order can differ for mixed-case names (case-insensitive
   Windows vs case-sensitive POSIX) even though the entry set is identical.
6. LICENSE remains absent by user instruction. The wheel's missing-license
   warning is unchanged.
7. `.cursor/rules/ogx.mdc` still ships as tracked editor guidance.

## 10. Blocked items

| Item | Exact reason | Owner / next action |
|---|---|---|
| Hosted GitHub Actions | `gh` is not installed; the unauthenticated Actions API returned HTTP 403 rate limit; earlier runs for `757d6b6` were refused before start by the account billing/spending-limit restriction. | Repository owner: resolve billing, then run the existing workflows. Milestone I.6, Task 5. |
| Python 3.11/3.12/3.13 | Only CPython 3.15.2 is installed locally. | Milestone I.6, Task 6 (or the hosted matrix). |
| Linux / macOS | Not available locally. | Milestone I.6, Task 7 (or hosted runners). |
| Wheel verification | Out of I.5 scope; no wheel input changed. | Milestone I.6, Task 8. |

No blocker is reported as a pass.

## 11. Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Dedicated archive regression test exists | **PASS** | `_dev/tests/test_make_dist.py`, 13 tests |
| Existing CI assertions remain strict | **PASS** | `ci.yml` unmodified; token tuple re-run verbatim |
| Required archive entries remain present | **PASS** | `init.py`, `core/ogx/__init__.py`, VERSION, `shield.toml` |
| Forbidden paths are absent | **PASS (local)** | 0 hits on clean-export and working-tree archives |
| README and I.4 reports reflect actual evidence | **PASS** | README corrected; three dated I.4 corrections |
| Nested duplicate directory has a documented disposition | **PASS** | Documentation audit §6–§7 |
| Existing user changes remain intact | **PASS** | Clean baseline; changes already committed; no destructive command |
| No unauthorized commit or push | **PASS** | No commit, push, PR, merge or release performed |

## 12. Git status

`git status --porcelain` at the end of this milestone:

```
 M GUARDIAN-X/README.md
 M GUARDIAN-X/_dev/tools/make_dist.py
 M GUARDIAN-X/docs/CI_CD_VERIFICATION.md
 M GUARDIAN-X/docs/FINAL_PROJECT_COMPLETION_REPORT.md
 M GUARDIAN-X/docs/FINAL_RELEASE_READINESS_REPORT.md
 M GUARDIAN-X/docs/MILESTONE_I4_ARCHIVE_VERIFICATION.md
 M GUARDIAN-X/docs/MILESTONE_I4_FINAL_REPORT.md
 M GUARDIAN-X/docs/MILESTONE_I4_PACKAGING_BASELINE.md
 M GUARDIAN-X/docs/PROJECT_COMPLETION_TRACKER.md
 M GUARDIAN-X/docs/TEST_RESULTS.md
 M README.md
?? GUARDIAN-X/_dev/tests/test_make_dist.py
?? docs/MILESTONE_I5_BASELINE_REPORT.md
?? docs/MILESTONE_I5_DOCUMENTATION_AUDIT.md
?? docs/MILESTONE_I5_FINAL_REPORT.md
?? docs/MILESTONE_I5_REGRESSION_TEST_REPORT.md
```

`GUARDIAN-X/dist/i5-baseline/` holds this pass's ignored scratch archives and
export; it is covered by the existing `dist/` ignore rule and does not affect
the drop-in (the generator excludes `dist` at any depth).

No commit, push, PR, merge, release or publication occurred.

## 13. Next milestone

**Milestone I.6 — CI Recovery and Cross-platform Verification**, starting with
Task 5 (hosted GitHub Actions review and blocker confirmation), then Task 6
(Python matrix), Task 7 (OS matrix) and Task 8 (wheel and drop-in packaging
audit from clean exports).

## 14. Handoff

1. The nested `.github` defect is fixed in committed code (`06ffef4`); the
   current generator excludes repository directory names at any depth and
   `*.egg-info`, and the clean-export archive has 326 entries with 0 forbidden.
2. The CI forbidden-path and required-file assertions pass locally, unmodified.
3. The drop-in suite runs 258 tests, OK, 9 skipped, exit 0; 13 are the new
   archive regression tests. The canonical suite is unchanged at 465 passed,
   15 skipped.
4. The refactored generator is byte-identical to the committed generator on the
   same clean export.
5. README and I.4 reports now state the actual commit, counts and local-only
   verification. The nested mirror has a documented disposition and a removal
   recommendation.
6. No push, PR, merge, release or commit occurred. Hosted CI remains blocked and
   is not claimed.
