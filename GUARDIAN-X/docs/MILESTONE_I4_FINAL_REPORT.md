# Milestone I.4 — Final Report: Drop-in Packaging Hygiene

> **Milestone I.5 correction (2026-11-26).** The `DROP_IN_VERIFIED` decision
> still holds for the packaging defect, and the fix is committed in `06ffef4`
> (HEAD/`origin/main` `a004348`). Limitation 4 below — "no archive-specific unit
> test exists" — is resolved by `GUARDIAN-X/_dev/tests/test_make_dist.py`, a
> 13-test suite that builds synthetic fixtures and asserts depth-independent
> exclusions, `*.egg-info` exclusion, required runtime entries, the
> `omega-shield/` prefix and deterministic ordering. Hosted CI and the wheel
> remain unverified in this milestone. Current state:
> `../../docs/MILESTONE_I5_FINAL_REPORT.md`.

Date: 2026-11-26
Decision: **DROP_IN_VERIFIED**

## 1. Repository state

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `757d6b6c519a30487a3bd46db30011ece30b1bdb` |
| `origin/main` | `757d6b6c519a30487a3bd46db30011ece30b1bdb` (read-only `git ls-remote`) |
| Ahead / behind | 0 / 0 |
| Commit made | none |
| Push / PR / merge / release | none |

## 2. Files changed

Code:

- `GUARDIAN-X/_dev/tools/make_dist.py` — packaging exclusion rule.

Reports created (untracked):

- `docs/MILESTONE_I4_PACKAGING_BASELINE.md`
- `docs/MILESTONE_I4_ARCHIVE_VERIFICATION.md`
- `docs/MILESTONE_I4_FINAL_REPORT.md` (this file)

Not changed: `.github/workflows/*`, `GUARDIAN-X/tests/*`, `GUARDIAN-X/_dev/tests/*`, `GUARDIAN-X/src/*`, `GUARDIAN-X/core/*`, `GUARDIAN-X/pyproject.toml`, `GUARDIAN-X/LICENSE`, application behavior, license metadata.

## 3. Root cause

`make_dist.py` walks `ROOT.rglob("*")` and applied its exclusion set only to `rel.parts[0]`:

```python
if path.is_dir() or rel.parts[0] in EXCLUDE_TOP:
    continue
```

The repository tracks a stray nested mirror, `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md`. Its first path component is `GUARDIAN-X`, not `.github`, so the `.github` component was never inspected. The broad recursive copy wrote it to `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md`, which the CI token `"/.github/"` correctly rejects.

The CI assertion expressed the intended policy correctly. The generator did not.

## 4. Packaging fix

`GUARDIAN-X/_dev/tools/make_dist.py` now:

- excludes repository/development directory names at **any depth** (`_dev`, `.github`, `.git`, `build`, `dist`, `release`, `tests`, `src`, `.pytest_cache`, `__pycache__`), matching the archive policy asserted in `ci.yml`;
- excludes `*.egg-info` directories as build metadata;
- keeps `pyproject.toml` excluded as a top-level packaging file;
- keeps the existing name, suffix, `.env`, and `results.txt` rules;
- keeps the `omega-shield/` archive prefix, sorted entry order, and all runtime content.

No assertion, required-file list, workflow, or test was modified.

## 5. Before / after archive

Clean export of exact HEAD `757d6b6` with the fixed generator overlaid:

| Metric | Before | After |
|---|---|---|
| Files | 305 | **304** |
| Bytes | 527,494 | 526,699 |
| SHA-256 | `96fef0ac1e061f5a97e11d4ebf79d445b17a1f27b9b8d44e1c2f828e6742324b` | `cbb9ffc366da407d2bdc4df6071c5b1e7f30588dd20ebabee6124d3781b39203` |
| Forbidden entries | 1 | **0** |

Removed exactly one entry: `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md`. Nothing was added.

Working tree (includes the user's 14 relocated reports):

| Metric | Before | After |
|---|---|---|
| Files | 324 | **318** |
| SHA-256 | `2808da3b0240a328d11866bee39ce127ef5c2130142ef8cb5081d3b12141d2bb` | `a393515a475adb3c61a70de9b0e7b67cf3fb7352c30116a4f42d84f40f4cf4f7` |

The working tree additionally dropped five `core/omega_guardian_x.egg-info/` build-metadata files.

## 6. Verification results

All commands were run from `GUARDIAN-X/` unless noted. Exit codes are the command's own.

| Check | Command | Result |
|---|---|---|
| Drop-in suite | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **Ran 245 tests — OK (skipped=9)** in 72.595 s, exit 0 |
| Canonical suite | `python -m pytest -q` | **465 passed, 15 skipped** in 95.10 s, exit 0 |
| Source/drop-in sync | `python _dev/tools/sync_core_from_src.py --check` | OK, versions `0.8.0a1`, exit 0 |
| Compile | `python -m compileall -q src core tests _dev/tests _dev/tools` | exit 0 |
| CLI version | `PYTHONPATH=core python -m ogx --version` | `ogx 0.8.0a1`, exit 0 |
| Status | `OGX_HOME=<temp> python init.py status` | exit 0, state outside host |
| Archive forbidden assertion | exact `ci.yml` snippet on clean-export archive | **PASS**, 0 forbidden |
| Archive required assertion | exact `ci.yml` snippet | **PASS**, both entries present |
| Archive install smoke | extract, `python init.py status` | exit 0 |
| Archive CLI smoke | `PYTHONPATH=<archive>/core python -m ogx --version` | `ogx 0.8.0a1`, exit 0 |

No test was added, removed, skipped, or weakened. The 15 canonical and 9 drop-in skips are the pre-existing platform gates on Windows.

## 7. Packaging regression review

| Requirement | Result |
|---|---|
| Normal wheel path unchanged | No change to `pyproject.toml`, `src/`, wheel build configuration, or wheel assertion. The wheel was not rebuilt because no wheel input changed. |
| Drop-in independently installable | Verified from a fresh extraction; `init.py status` and `ogx --version` exit 0. |
| Entry points still work | `python -m ogx --version` works from both the source tree and the archive. |
| No runtime source files missing | Set comparison shows exactly one removal and zero additions; `core/` remains 117 entries and `docs/` 76 entries. |
| No unnecessary files shipped | 0 hits for `.github`, tests, `_dev`, `src`, `release`, caches, egg-info, Git metadata, secrets, or binary artifacts. |
| Generator works from clean export | Verified with `git archive HEAD`; the working-tree result is consistent. |

## 8. Remaining limitations

1. **Hosted CI has not run for this change.** The repository's GitHub Actions jobs for `757d6b6` were refused before startup by the account billing/spending-limit restriction. No claim of hosted success is made. The CI assertion was executed locally, verbatim, against the generated archive.
2. **The stray tracked mirror remains in the repository.** `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` is still tracked at HEAD. The generator now excludes it, so it no longer ships, but repository hygiene would be improved by removing the stray file in a separately authorized change. It was not deleted here to avoid destructive cleanup.
3. **Byte-level archive reproducibility is not guaranteed.** Entry order is deterministic and two builds of the same export were byte-identical; the ZIP format still records source mtimes, so hashes can differ across exports. This matches the prior implementation.
4. **No archive-specific unit test exists.** The effective regression check remains the inline CI assertion. Adding a packaging test would require a test change outside this milestone's minimal scope.
5. **`.cursor/rules/ogx.mdc` still ships.** It is tracked editor guidance, not a cache or temporary file; it was intentionally left unchanged.
6. **LICENSE remains absent.** It is outside this milestone's scope and was neither restored nor modified. The wheel's missing license file reported in Milestone I.3 is unchanged.

## 9. Hosted CI status

- Workflow files were not modified.
- No workflow run exists for this uncommitted change.
- Runs for `757d6b6` (`45670893677`, `45670893573`, `45670893599`) still show 0 executed steps and the billing annotation.
- Local verification does not substitute for a hosted run.

## 10. Final handoff

1. **Nested `.github` defect:** fixed. The generator excludes `.github` at any depth; the clean-export archive contains 304 files with no nested mirror.
2. **CI forbidden-path assertion:** passes locally, unmodified, against the generated archive.
3. **Drop-in tests:** 245 ran, OK, 9 skipped, exit 0. No failures.
4. **Clean-export archive:** verified from `git archive HEAD`; 304 files, SHA-256 `cbb9ffc3…39203`, required entries present, install and CLI smoke pass.
5. **User changes:** preserved. The 14 deleted root reports, 14 untracked relocated copies, and 8 documentation edits are unchanged.
6. **No push, PR, merge, release, or commit occurred.** All changes remain uncommitted in the working tree.

Decision: **DROP_IN_VERIFIED** (local packaging verification complete; hosted CI remains blocked by the account billing restriction and is not claimed as passed).

## 11. Milestone I.5 status correction (2026-11-26)

This table supersedes the corresponding statements in §1, §2, §5, §6, §8 and §9
for the current repository state. The original sections remain as the dated I.4
record.

| Item | Status at `a004348` |
|---|---|
| Commit containing the fix | `06ffef44f179068d0b9b016d77b51c34e6bdb121` |
| Current HEAD / `origin/main` | `a004348cd371fc1a42aefcaacf7e31b577026476`, 0 ahead / 0 behind |
| Report location | Relocated from root `docs/` to `GUARDIAN-X/docs/` by `a004348` (0 content change) |
| Archive regression test | `_dev/tests/test_make_dist.py`, 13 tests, OK |
| Drop-in suite | 258 tests, OK (9 skipped), exit 0 |
| Canonical suite | 465 passed, 15 skipped, exit 0 |
| Clean-export archive | 326 entries, 0 forbidden hits, required entries present |
| Working-tree archive | 326 entries, 0 forbidden hits, identical entry list to the clean export |
| Refactor equivalence | I.5 `build_archive()` produces byte-identical output to the committed generator on the same export |
| Archive install smoke | `init.py status` exit 0; `ogx --version` reports `0.8.0a1` |
| Wheel | **NOT_RUN** in I.5; last verified in Milestone I.3 (167 files, clean-venv install) |
| Hosted CI | **BLOCKED** — `gh` not installed; unauthenticated Actions API returned HTTP 403 rate limit; earlier runs were refused before start by the billing/spending-limit restriction |
| Cross-platform | Windows 10 / CPython 3.15.2 only; Python 3.11–3.13, Linux and macOS **NOT_RUN** |
| Remaining limitations | Stray nested mirror still tracked (no longer ships); ZIP bytes vary with extraction mtimes; LICENSE absent by user direction |

Full evidence: `../../docs/MILESTONE_I5_BASELINE_REPORT.md`,
`../../docs/MILESTONE_I5_REGRESSION_TEST_REPORT.md` and
`../../docs/MILESTONE_I5_FINAL_REPORT.md`.
