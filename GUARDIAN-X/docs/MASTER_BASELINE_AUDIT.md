# Master Baseline Audit (Phase 0 / M0)

Date: 2026-11-26
Repository root: `D:/FTP/OMEGA-GUARDIAN-X-Universal`
Product root: `GUARDIAN-X/`
Branch: `main`
Audit result: **PASS for the local baseline reconciliation**; hosted CI
**BLOCKED**; Python/OS matrix **NOT RUN**

This is the Phase 0 baseline reconciliation required by the master roadmap. It
is a read-only audit of the repository plus documentation corrections; it is
not a feature milestone. No runtime source, test, workflow, `pyproject.toml` or
LICENSE file was changed.

## 1. Method

- One agent, sequential execution. No subagents, no parallel repository work.
- Every result below was produced by a command executed in this pass unless a
  row is explicitly labelled as prior or historical evidence.
- The working tree was clean at the start. No `reset`, `clean`, `checkout`,
  `restore`, `stash`, rebase or history rewrite was run.
- `git ls-remote origin refs/heads/main` was used to confirm the live remote
  default branch; no repository mutation occurred.
- Scratch exports, archives and the test wheel live under the gitignored
  `GUARDIAN-X/dist/p0-*` directories and are excluded from the drop-in archive.

## 2. Repository identity and Git baseline

| Item | Observed |
|---|---|
| `git rev-parse --show-toplevel` | `D:/FTP/OMEGA-GUARDIAN-X-Universal` |
| Branch | `main` |
| HEAD | `36e56c0c6de4f246626cd8f57f56e3fc5bbaa40f` |
| `origin/main` (tracking ref) | `36e56c0c6de4f246626cd8f57f56e3fc5bbaa40f` |
| Live `git ls-remote origin refs/heads/main` | `36e56c0c6de4f246626cd8f57f56e3fc5bbaa40f` |
| Ahead / behind | 0 / 0 |
| `git status --porcelain=v1` at audit start | empty |
| Staged / untracked at audit start | none / none |
| Tags | none |
| Version | `0.8.0a1` (`VERSION`, `src/ogx/__init__.py`, `core/ogx/__init__.py`) |
| Remote | `https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal.git` |

Branches: `main` at `36e56c0`; `milestone-i-runtime-unification` at `a026f7b`
(historical, not checked out).

### 2.1 Commit topology

| Commit | Parent | Content |
|---|---|---|
| `36e56c0` | `a004348` | Milestone I.5 delivery: 16 files, +915 / −18. Extracts `build_archive()` in `_dev/tools/make_dist.py`, adds `_dev/tests/test_make_dist.py` (13 tests), corrects README/I.4 statements, adds four I.5 reports. |
| `a004348` | `06ffef4` | 8 files, 0 insertions / 0 deletions: pure rename of the I.3/I.4 reports from repository-root `docs/` to `GUARDIAN-X/docs/`. |
| `06ffef4` | `757d6b6` | 31 files, +1172 / −29: I.4 packaging fix (`make_dist.py` depth-independent exclusions), I.3/I.4 report creation and I.1/I.2 report relocation. Recorded in the I.5 baseline report; not re-measured here. |
| `757d6b6` | `d55cea3` | I.4 baseline commit. |
| `d55cea3` | `e7e7686` | I.1 documentation corrections (docs-only). |
| `e7e7686` | earlier | Host lifecycle and provider-hardening tree; the canonical suite baseline cited by several dated reports. |

## 3. Execution baseline vs delivery history

Two different commits must not be conflated:

- **`a004348` — Milestone I.5 execution baseline.** It was HEAD and
  `origin/main` when the I.5 measurements were taken. Every I.5 figure
  (326-entry archive, 465/15 canonical, 258/9 drop-in, 13 archive tests) was
  produced against this tree.
- **`36e56c0` — Milestone I.5 delivery commit.** It is the child of `a004348`
  and contains exactly the I.5 work. It is now HEAD and `origin/main`.

The I.5 final report was written before the delivery commit existed, so its
statements "Final commit observed: `a004348` (no commit made)" and the
untracked-file list in its §12 describe the I.5 session, not the current
repository. A dated Phase 0 correction block now sits at the top of
`docs/MILESTONE_I5_FINAL_REPORT.md`; the original sections are preserved as the
dated I.5 record.

`36e56c0` does not change runtime behavior: `src/`, `core/`, `tests/`,
`pyproject.toml` and LICENSE are untouched. The only code change is the
behavior-preserving `build_archive()` extraction in a development tool, proven
byte-identical to the committed generator on the same export in I.5. Packaging
was nevertheless re-measured from `36e56c0` in this pass.

## 4. Repository and documentation layout

- Repository root: `.github/` (active workflows and reusable kit),
  `GUARDIAN-X/` (product), `docs/` (the four I.5 reports only), `README.md`.
- Product root: `src/ogx/` canonical package, `core/ogx/` hand-maintained
  stdlib drop-in, `tests/` pytest suite, `_dev/tests/` unittest drop-in and
  architecture suite, `docs/` (80 Markdown files plus `archive/`, `kit/`,
  `examples/`, `schemas/`), `release/`, `dist/` (gitignored).
- Milestone I.1–I.4 reports live in `GUARDIAN-X/docs/` (22 reports); Milestone
  I.5 reports live in repository-root `docs/`. This split is intentional and
  follows the I.5 directive's explicit output paths.
- `GUARDIAN-X/docs/archive/` holds three explicit 0.7-era snapshots
  (`ARCHITECTURE-0.7.md`, `PROJECT_ROOT_INTEGRATION-0.7.md`,
  `README-Guardian-X-0.7.md`).
- 103 Markdown files were included in the documentation audit: the root
  README, 15 product-root Markdown files, 83 product docs and 4 root docs.

## 5. Test baseline independently re-executed at `36e56c0`

Environment: Windows 10.0.19045 (AMD64), CPython 3.15.2, pip 26.3,
Git 2.56.0.windows.4, Git Bash. cwd = `GUARDIAN-X/` unless noted.

| Check | Command | Result | Exit |
|---|---|---|---|
| Canonical suite | `python -m pytest -q` | **465 passed, 15 skipped**, 90.58 s | 0 |
| Drop-in suite | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **Ran 258 tests, OK (skipped=9)**, 71.25 s | 0 |
| Archive regression suite | `PYTHONPATH=core python -m unittest discover -s _dev/tests -p "test_make_dist.py"` | **Ran 13 tests, OK**, 2.14 s | 0 |
| Compile | `python -m compileall -q src core tests _dev/tests` | no output | 0 |
| Source/drop-in sync | `python _dev/tools/sync_core_from_src.py --check` | OK, versions `0.8.0a1` | 0 |

The 15 canonical and 9 drop-in skips are the pre-existing Windows platform
gates (symlink creation, POSIX file modes, and one drive-relative case covered
by a separate test). No test was skipped, removed or weakened in this pass.

Python 3.11, 3.12 and 3.13, Linux and macOS were not available locally and were
not executed. CPython 3.15.2 is outside the declared classifier range, so this
is evidence that the code runs on a newer interpreter, not proof for 3.11–3.13.

## 6. Packaging baseline at `36e56c0`

### 6.1 Drop-in archive

Built from a clean `git archive HEAD` export with the committed generator
(`GUARDIAN-X/_dev/tools/make_dist.py`), extracted under
`GUARDIAN-X/dist/p0-export/`.

| Metric | Observed |
|---|---|
| Entries | **326** |
| SHA-256 | `a65a2c807bf7d0d3e3375eed91ee5e49ee92ca6b5cdce3caf92f6507e3b34342` |
| Forbidden hits (exact `ci.yml` token tuple) | **0** |
| Required entries (`init.py`, `core/ogx/__init__.py`, `VERSION`, `shield.toml`) | all present |
| `omega-shield/docs/` entries | 98 |
| Duplicate entries | 0 |
| Nested `GUARDIAN-X/.github` mirror | absent |
| Two consecutive builds | byte-identical; identical `namelist()` |
| Entry order | deterministic per platform; not a plain ASCII sort |

The entry-order caveat is real: the generator sorts `Path` objects, and Windows
path comparison is case-insensitive, so `namelist()` is deterministic on this
host but is not lexicographically sorted. The entry **set** is what must match
across platforms; a fixed cross-platform sequence is not asserted. ZIP headers
also record extraction mtimes, so byte-level reproducibility across independent
exports is not guaranteed.

Smoke tests from the extracted archive:

| Check | Result |
|---|---|
| `OGX_HOME=<sibling of host> python init.py status` | exit 0; state outside the host; core `ogx 0.8.0a1` |
| `PYTHONPATH=core python -m ogx --version` | `ogx 0.8.0a1`, exit 0 |

### 6.2 Wheel

Built from `GUARDIAN-X/` with `python -m pip wheel . --no-deps -w dist/p0-wheel`.

| Metric | Observed |
|---|---|
| Wheel | `omega_guardian_x-0.8.0a1-py3-none-any.whl` |
| Entries | **167** |
| Size | 402,363 bytes |
| SHA-256 | `04243ea0a23f4c5da38151d0c1928f9b646c598f98a1d95f96fdd2e29bbe06fc` |
| Forbidden paths (`tests/`, `_dev/`, `.github/`, `core/`, `src/`, caches, `.env`) | 0 |
| `ogx/cli.py`, `ogx/__init__.py` | present |
| Console entry points | `*.dist-info/entry_points.txt` present |
| Top-level directories | `ogx/`, `omega_guardian_x-0.8.0a1.dist-info/` |
| License file | **absent** |
| Metadata | `Name: omega-guardian-x`, `Version: 0.8.0a1`, `Requires-Python: >=3.11`, `License-Expression: LicenseRef-OMEGA-GUARDIAN-X-Proprietary-Alpha` |

The build emits `Pattern 'LICENSE' did not match any files.` because
`GUARDIAN-X/LICENSE` is absent by user instruction. The wheel still builds,
installs and runs; packaging success is not a license-compliance claim.

Clean-environment installation:

| Check | Result |
|---|---|
| `python -m venv dist/p0-venv` + `pip install --no-deps` the wheel | exit 0 |
| `import ogx` | `0.8.0a1`, exit 0 |
| `ogx --version` | `ogx 0.8.0a1`, exit 0 |
| `ogx --help`, `providers list`, `platform status`, `host list` | all exit 0 |

### 6.3 README reconciliation

The root README previously attributed the archive measurement to a clean
`a004348` export. It now states the clean `36e56c0` export, the measured
326 entries / 0 forbidden / SHA-256 `a65a2c80…`, the rebuilt 167-entry wheel
and the re-executed test timings. The product README now names HEAD `36e56c0`
and the 326-entry clean-export archive. Both READMEs keep the explicit
statement that hosted CI has not produced a verified run.

## 7. Documentation link and path audit

Two checks were run over the 103 Markdown files:

1. **Markdown links** `[text](target)`: 6 relative links, **0 missing**.
2. **Backticked repository-path tokens**: 1,282 checked; 122 unresolved
   occurrences (101 distinct file/target pairs). Classification:

| Category | Occurrences | Explanation |
|---|---|---|
| Intentional LICENSE absence | 23 | `GUARDIAN-X/LICENSE` is absent by user instruction; not a broken link. |
| Archive-member paths | 38 | `omega-shield/...` names describe zip entries, not repository paths. |
| Historical or removed paths | 35 | Removed nested tree (`core/ogx/core/...`), deleted temporary script, old root `docs/` layout, nested mirror path, `GUARDIAN-X/TODO.md`, and old engine locations inside dated reports. |
| Artifact-internal paths | 10 | `ogx/...` and `*.dist-info/...` names describe wheel members, not repository files. |
| Generated project-state paths | 10 | `.guardian-x/identity.json` is created in an analyzed project, not in this repository. |
| Non-path identifiers | 4 | Pytest node IDs (`::test_...`), `file.py:108`, a bare `.md`, and an extension list. |
| Planned next-milestone report | 2 | `docs/PHASE_1_CORE_ARCHITECTURE_REPORT.md` is the declared Phase 1 deliverable and does not exist yet. |

One genuinely stale **live** path was found and fixed:
`GUARDIAN-X/docs/ENGINE_DEVELOPMENT.md` described the engine package as
`core/ogx/engines/`, but engines live in `src/ogx/engines/` and the drop-in
`core/ogx` ships no `engines/` package. The layout block and the "add an
engine" instruction now point at `src/ogx/engines/`, with an explicit note
about the drop-in.

No unresolved live documentation link remains. The remaining tokens are
historical records, intentional absences, archive-member names, or prose
references whose targets exist.

Naming note: `GUARDIAN-X/docs/RELEASE_REVIEW_INSPECTION.md` is titled
"Release Review Inspection (Phase 0)" and is dated 2026-11-24. It is a prior,
differently scoped release-review inspection, not this baseline Phase 0.

## 8. Stale-claim audit

Searches covered `production-ready`, `ready for production`, `fully
operational`, `100% complete`, `complete and operational`, `RELEASE_READY`,
`OPERATIONAL`, and the stale test counts `245` / `340`.

| Finding | Disposition |
|---|---|
| `PROVIDER_COMPATIBILITY.md` labelled a mocked `compatible` verdict "✅ Ready for production" and carried a 2024-11-24 verification matrix predating this repository | **Corrected.** The cell now says "Contract-compatible (mocked contract suite; not a live-API or production-readiness claim)", and a dated note explains that no provider credential or live API call exists and that Forgejo/AWS CodeCommit are missing from the table. |
| `CORE_CONTRACTS.md` said the host contract suite "does not cover connect/read/disconnect/health/recovery, so no adapter is declared operational" | **Corrected.** The lifecycle suites (13 + 16/3 + 21 + 6) cover those paths; the document now scopes OPERATIONAL to the two read-only local hosts and still denies writes/remote/branch operations. |
| Living docs still reported 245 drop-in tests | **Corrected** in `HOST_ARCHITECTURE.md`, `PROJECT_COMPLETION_TRACKER.md`, `CORE_CONTRACTS.md`, `TEST_RESULTS.md`, `CI_CD_VERIFICATION.md`, `CROSS_PLATFORM_VERIFICATION.md`, and the two completion/readiness reports (dated update blocks). Historical milestone reports keep their dated 245 figures. |
| I.5 final report said no commit was made | **Corrected** with a dated block naming `36e56c0` as the delivery commit and preserving `a004348` as the execution baseline. |
| Root README attributed packaging to `a004348` | **Corrected** to the clean `36e56c0` export and rebuilt wheel. |
| "Production-ready" claims | No project document claims production readiness. Every occurrence is a disclaimer ("not automatically production-ready") in the reusable kit. |
| `RELEASE_READY` | Appears only as a label the reports explicitly reject in favour of `READY_FOR_RELEASE_REVIEW`. |
| `OPERATIONAL` | Used only for the two local read-only hosts, scoped in `HOST_ARCHITECTURE.md` and `PLATFORM_SUPPORT_MATRIX.md`, and backed by the host lifecycle/adapter/CLI suites. No cloud, remote, write-capable or Docker/Kubernetes host is called operational. |

## 9. Duplicate documentation audit

| Duplicate family | Evidence | Assessment |
|---|---|---|
| `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` vs `GUARDIAN-X/.github/RELEASE_NOTES.md` | 1,010 vs 1,595 bytes; the nested copy lacks the I.2 re-verification block; no tooling reads it; excluded from the archive by the depth-independent `.github` rule | **Accidental diverged near-duplicate.** Tracked at HEAD. Removal recommended in a separately authorized change; not deleted here. |
| Kit docs: `.github/README-Github.md` vs `GUARDIAN-X/.github/README-Github.md` | Identical (MD5 `0e3cf665…`, 9,111 bytes); `GUARDIAN-X/docs/kit/README-Github.md` is a 9,409-byte variant | Intentional distribution copies of the reusable kit; the product README documents the nested `.github` as inert kit content. |
| `copilot-instructions.md` in `.github/`, `GUARDIAN-X/.github/`, `GUARDIAN-X/devkit/agents/` | All three identical (MD5 `b756dc0a…`) | Intentional agent-kit mirrors. |
| `KIT-VALIDATION-REPORT.md` in `.github/`, `GUARDIAN-X/.github/`, `GUARDIAN-X/_dev/release/` | All three identical (MD5 `783dc05b…`) | Intentional validation-record copies. |
| Nested workflows `GUARDIAN-X/.github/workflows/*.yml` | Four files with the same names as the root workflows; GitHub does not execute nested `.github` workflows | Inert copies, documented; root `.github/workflows/` is authoritative. |
| `GUARDIAN-X/docs/archive/*-0.7.md` | Three files explicitly suffixed `0.7` | Intentional historical snapshots. |
| Duplicate doc basenames across `docs/` and `GUARDIAN-X/docs/` | 0 duplicates | The root/product split is intentional. |

No duplicate file was deleted. The only removal recommendation is the nested
`RELEASE_NOTES.md` mirror.

## 10. Security review

- No runtime source, test, workflow, `pyproject.toml` or LICENSE file changed;
  the diff is documentation and two reports.
- The archive and wheel contain no test, CI, release, `src/`, `_dev/`, cache,
  secret, database or Git metadata entry; both report 0 forbidden hits.
- No secret was read, printed or written. The live remote check used the
  existing Git credential helper without displaying credentials; no provider
  API call was made.
- Workflow permissions and pinned action SHAs are untouched.
- Scratch exports, archives and the venv are under gitignored `dist/` and are
  excluded from the drop-in archive by the existing policy.
- No destructive Git command, no staging of unrelated files, no repository
  setting change.

## 11. Git safety

At the end of this audit the worktree contains 13 modified documentation files
and 2 new Phase 0 reports. No commit, push, pull request, merge, release,
publication, tag or visibility change was performed. `git diff --check` is
clean, and `git diff --name-only` over `.github/`, `GUARDIAN-X/src/`,
`GUARDIAN-X/core/`, `GUARDIAN-X/tests/`, `pyproject.toml` and `LICENSE` is
empty.

The starting tree was clean, so no user modification could be lost. No reset,
clean, destructive checkout, restore, stash or rebase was run.

## 12. Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| All current status claims match source and test evidence | **PASS** | Living docs corrected; historical reports labelled and preserved; stale-claim audit §8 |
| Packaging evidence applies to the actual current HEAD | **PASS** | Archive and wheel rebuilt from clean `36e56c0`; §6 |
| No unexplained broken documentation links remain | **PASS** | 6/6 Markdown links resolve; all 77 backticked tokens classified; one live stale path fixed; §7 |
| Historical reports remain identifiable as historical | **PASS** | Dated blocks added; original sections and dates preserved |
| No user changes are lost | **PASS** | Clean baseline; no destructive command; §11 |
| Hosted CI verified | **BLOCKED** | No hosted run exists for `36e56c0`; `gh` is not installed; the earlier billing restriction is unchanged |
| Declared Python 3.11–3.13 / Linux / macOS executed | **NOT RUN** | Only CPython 3.15.2 on Windows is available |

## 13. Residual issues carried forward

1. Hosted GitHub Actions is still unverified for `36e56c0`; `gh` is not
   installed and no run URL exists. The earlier `757d6b6` runs were refused
   before starting by the account billing/spending limit.
2. Python 3.11–3.13, Linux and macOS have not been executed.
3. `GUARDIAN-X/LICENSE` is absent by user instruction; the wheel carries no
   license file and setuptools warns about the declared pattern.
4. `GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md` remains tracked. It no
   longer ships and has no consumer; removal needs separate authorization.
5. ZIP bytes embed extraction mtimes, so archive SHA-256 is per-export; entry
   sets and repeated builds are deterministic.
6. Historical reports intentionally retain their dated 245/163/280 figures;
   they are not current claims.
7. `PROVIDER_COMPATIBILITY.md` remains a historical contract template; its
   matrix is now explicitly labelled as mocked, not live-verified.
8. `.cursor/rules/ogx.mdc` still ships as tracked editor guidance.

## 14. Next milestone

Phase 1 (M1) — Core Architecture Completion, delivering
`docs/PHASE_1_CORE_ARCHITECTURE_REPORT.md`. Phase 0 does not resolve the
hosted-CI or cross-platform blockers; those remain external checks for the QA
and release-readiness phases.
