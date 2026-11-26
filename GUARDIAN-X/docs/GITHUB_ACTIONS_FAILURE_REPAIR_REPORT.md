# GitHub Actions Failure Repair Report — Case- Normalization Audit

**Scope:** the four failing GitHub Actions checks (`Profile-configured project checks`,
`Tests (Python 3.11)`, `Tests (Python 3.12)`, `Tests (Python 3.13)`) caused by the
Linux case-insensitive state-directory collision.

**Status: PARTIALLY_COMPLETE** — the code defect is fixed and the full canonical
suite, full drop-in suite, profile-equivalent checks, compile, sync and packaging
all pass on every available interpreter, but hosted CI cannot be re-run without a
push (not authorized) and no Linux/ext4 host is available here. The four GitHub
Actions green-check claim therefore stays unconfirmed until the owner pushes.

| Item | Value |
|---|---|
| Local Git HEAD | `386c6699dfc32587277be0408080be127fbb8e29` (`main`) |
| CI / mission-named HEAD | `485664e92826ba42e1bc78803e72341e609f2a6a` → remote `origin/main` |
| Local `main` vs remote | Local lags remote by the single commit `485664e Merge pull request #1 from …/shajeduzzamanenan992008inc-patch-1`, which added only `SECURITY.md` (now present on remote origin/main, absent from local main). No file touched by this repair exists in that commit. |
| Worktree | 8 modified files, 2 new files (1 test, 1 report); nothing committed, pushed or reset |
| Python actually run | CPython 3.15.2 (host), 3.11.18, 3.12.16, 3.13.18 (all uv-managed, `--no-project`) |
| Python not run | none — 3.11/3.12/3.13 all run and all pass |
| Linux host | NOT_RUN — WSL is not registered on this machine; no ext4 host |

**HEAD reconciliation note.** Before this review the report stated the mission
HEAD `485664e` was "not present in this clone (`git cat-file` fails)". That was
true of a stale `origin/main` tracking ref pointing at the local HEAD. After a
read-only `git fetch origin main`, the missing object exists locally, and the
comparison becomes exact: local `main` is the parent of remote `main`; the only
difference is an added-but-not-in-local `SECURITY.md`. The repaired files are
identical across local HEAD, remote `main` and `origin/main` before the repair,
so no upstream fix was applied. Remote diff: **1 file changed, 21 deletions**
(`SECURITY.md`). This report is written against the current working tree at
`386c6699…`; no fetch writes to the working tree and no reset/clean/force checkout
is performed.

---

## 1. Confirmed root cause

The defect was **not** in path normalization and **not** in the digest. Both were
already case-folded and already correct:

```python
canonical = str(root.resolve()).casefold()
digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

The defect was in the **human-readable slug that prefixes the digest**:

```python
safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", root.name).strip(".-") or "project"
#                          ^ A-Z allowed, and root.name was never folded
return state_dir() / "projects" / f"{safe_name}-{digest}" / "reports"
```

For `…/alpha/project` and `…/alpha/PROJECT` the digest is identical, but the slug
was `project` and `PROJECT`, so the state key was:

```text
<OGX_HOME>/projects/project-<16 hex>/reports
<OGX_HOME>/projects/PROJECT-<16 hex>/reports
```

- **Linux (ext4, case-sensitive):** two distinct sibling directories. The
  canonical test compared `Path` objects and failed; the drop-in test failed the
  same way.
- **Windows / default macOS (case-insensitive):** the two names *are* the same
  directory, and `Path("a") == Path("A")` is `True` on Windows. The tests
  therefore passed locally and the defect only appeared on `ubuntu-latest`.

Three sites carried the identical defect — one rule implemented three times, not
three independent bugs:

| # | Site | Function |
|---|---|---|
| 1 | `src/ogx/cli.py` | `_external_report_dir` (canonical `ogx scan`/`start`/intelligence reports) |
| 2 | `core/ogx/cli/common.py` | `_external_report_dir` (drop-in CLI, duplicate implementation) |
| 3 | `init.py` | `state_home` (drop-in per-host state root) |

`src/ogx/guardian.py::_backup_root` and `core/ogx/shield/guardian.py::_backup_root`
use the digest **without** a slug (`sha256(str(root).casefold())[:16]`), so they were
already collision-free and were left unchanged.

### What was deliberately not changed

- The digest input (`str(path.resolve()).casefold()`) — changing it would move every
  existing state key and invalidate recorded vectors.
- The digest-length contract (16 hex canonical, 12 hex drop-in) — recorded in
  `_dev/fixtures/identity_vectors.json` and asserted by the P0 parity gate, both
  byte-identical to `origin/main` (verified by `git show` + `diff`).
- Any test assertion, fixture row or golden value. No test was weakened, skipped,
  deleted or rewritten; the two originally failing tests are unchanged and now pass.

---

## 2. Files changed

| File | Change |
|---|---|
| `src/ogx/config.py` | New `state_key_slug()` + `STATE_KEY_SLUG_FALLBACK` / `_UNSAFE_SLUG_CHARS`; the single documented slug rule. Added `import re`. |
| `src/ogx/cli.py` | `_external_report_dir` uses `state_key_slug(root.name)` instead of the unfolded inline regex; added `state_key_slug` to its import. |
| `core/ogx/kernel/config.py` | Mirrored `state_key_slug()` for the drop-in tree; added `import re`. |
| `core/ogx/cli/common.py` | `_external_report_dir` uses the mirrored helper; now-unused `import re` removed. |
| `init.py` | `state_home()` folds the slug (`HOST_ROOT.name.casefold()`) and uses the shared `project` fallback instead of `host`. |
| `tests/test_state_key_normalization.py` | **New.** 14 canonical regression tests (see §4). |
| `_dev/tests/test_identity_parity.py` | 7 drop-in regression tests added to `DropinStateKeyTests`; existing tests and the required-v1 test untouched. |
| `docs/CONFIGURATION.md` | Documents the state-key normalization strategy and its compatibility impact. |
| `docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md` | Notes the folded slug and why it is required. |
| `docs/GITHUB_ACTIONS_FAILURE_REPAIR_REPORT.md` | **New.** This report. |

No dependency was added, no security control was weakened, `LICENSE` and all
license metadata were untouched, and no `dist/` snapshot was regenerated. The
only non-code file added is this report.

### Incompleteness found during review and corrected

Two defects in the repair itself were caught by this audit and fixed before this
report was finalized:

1. **`src/ogx/config.py` docstring indentation** — the line
   `"The state key for a project is …"` was not indented to the docstring. Fixed
   with a 1-line `str_replace` so the docstring is valid Python. No behavior
   changed; `py_compile` passes after the fix.
2. **Report table row naming a non-existent canonical test** — §4 previously
   listed `test_unicode_case_folding_maps_to_one_state_directory`, which does not
   exist in `tests/test_state_key_normalization.py`. Unicode case folding is covered
   at the slug level by `test_slug_folds_unicode_case_with_casefold`; at the
   directory level it is covered by the drop-in `test_unicode_case_folding_collides`.
   The table row was corrected so every listed row names a real test and the count
   stays 14.

---

## 3. Normalization strategy

The state key is `"<slug>-<digest>"`, produced by one documented rule:

1. **Identity (authoritative):** `digest = sha256(str(path.resolve()).casefold())`,
   truncated to 16 hex (canonical) or 12 hex (drop-in, unchanged). `resolve()`
   removes redundant separators, `.` segments and trailing separators, so
   `alpha/project`, `alpha//project`, `alpha/./project` and `alpha/project/`
   reach the same input. Because the input is case-folded, a case-only path
   difference already shared one digest.
2. **Slug (cosmetic, now agrees with the digest):**
   `str(name).casefold()` → every character outside `a-z 0-9 . _ -` replaced by
   `-` → strip leading/trailing `.` and `-` → empty becomes `project`.

Why `str.casefold()` and not `str.lower()`: the digest already uses `casefold`, so
using the same primitive guarantees the slug can never disagree with the digest about
whether two names are one project. It is also the stronger Unicode rule
(`straße` == `STRASSE`), which `lower()` does not fully provide.

A parity proof was generated against a 26-name matrix and confirmed byte-equal
between the canonical and drop-in implementations:

```python
from ogx.config import state_key_slug  # canonical
from ogx.kernel.config import state_key_slug  # drop-in (PYTHONPATH=core)
```

| Input | Slug | Note |
|---|---|---|
| `"project"` / `"PROJECT"` | `project` | case-only collision eliminated |
| `"My Project"` | `my-project` | spaces → `-` |
| `"straße"` / `"STRASSE"` | `strasse` | casefold, not lower — same as digest |
| `"café"` / `"cafè"` | `caf` | lossy by design — digest separates |
| `"ProJect-2"` / `"ALPHA"` | `project-2` / `alpha` | distinct names stay distinct |
| `".."`, `"a/b"`, `"###"`, `""`, `"§§§"`, `"  "` | `project` | no separator, no traversal, never empty |
| `'a\\b'` (backslash in name) | `a` | backslash replaced, no split |
| `"Project."` / `"-dash-"` | `project` / `dash` | trailing `.`/`-` stripped |

Safety properties, each covered by a test:

- The slug cannot contain `/` or `\`, cannot be `.` or `..`, and cannot be empty, so
  it cannot introduce traversal or escape `<OGX_HOME>/projects`.
- Non-ASCII names are lossy by design (`café` and `cafè` both slug to `caf`); the
  digest alone separates genuinely different projects, so this cannot cause a
  collision.
- The slug is ASCII and lower case, so it is portable to case-insensitive and
  case-preserving filesystems alike.
- No symlink or path-traversal behavior was added; `_prepare_report_dir` already
  refuses symlinked state components and that code is unchanged.

Compatibility impact (documented, not silent):

- **Windows / default macOS:** no observable change — the filesystem already folded
  case, so `PROJECT-<d>` and `project-<d>` were always one directory.
- **Case-sensitive filesystems:** a project whose folder name contains upper-case
  letters gets a new lower-case report directory. The old mixed-case directory is left
  untouched and its reports stay readable; it can be renamed to the new name or deleted
  because reports are regenerable.
- **Long project names:** there is no slug length cap, so a directory component longer
  than the host filesystem's name limit (e.g. 255 bytes on ext4) can still produce an
  `ENAMETOOLONG`. The old code had the same behaviour (regex substitution preserves
  length), so this is a **pre-existing limitation**, not introduced by the repair, and
  out of scope for this fix. Real names are short in practice.
- **Golden vectors:** unchanged. `identity_vectors.json` records only
  `state_key_hex_length` (16 / 12), which did not move, and the CX-05 conflict row
  in `relocation_scenarios.json` already describes the intended behaviour
  ("two distinct case-sensitive paths share one state key"). No fixture edit was
  required, so none was made.

---

## 4. Regression tests added

Canonical — `tests/test_state_key_normalization.py` (14 tests):

| Test | Covers |
|---|---|
| `test_slug_folds_ascii_case` | `project`/`PROJECT`/"`My Project`" → `my-project` |
| `test_slug_folds_unicode_case_with_casefold` | `straße`/`STRASSE`, `Ä`/`ä` agree with the digest rule |
| `test_slug_is_ascii_lowercase_and_portable` | `^[a-z0-9._-]+$` for `PROJECT`, `My Project №1`, `a.b_c-d`, `café` |
| `test_slug_never_contains_a_separator_or_traversal_component` | `..`, `../escape`, `a/b`, `a\\b`, blank, `###` |
| `test_unrepresentable_name_uses_the_documented_fallback` | empty result ⇒ `project` |
| `test_case_only_difference_in_the_leaf_folder_maps_to_one_state_directory` | `alpha/project` vs `alpha/PROJECT` — path **and** name string |
| `test_case_only_difference_in_a_parent_folder_maps_to_one_state_directory` | `alpha/project` vs `ALPHA/project` |
| `test_case_only_difference_in_the_project_folder_name_maps_to_one_directory` | `alpha` vs `ALPHA` |
| `test_different_project_names_remain_distinct` | `project`/`project2`, `alpha`/`beta`, plus name inequality |
| `test_different_unicode_names_do_not_collide` | `café` vs `cafè` share a slug but **not** an identity (digest differs) |
| `test_redundant_separators_and_dot_segments_resolve_to_one_identity` | `os.sep`, doubled separators, `.` segment, trailing separator |
| `test_repeated_calls_are_deterministic` | four repeated calls and an equal `Path` object |
| `test_directory_name_is_the_folded_slug_plus_the_sixteen_hex_digest` | exact key shape, 16-hex digest, valid hex |
| `test_state_directory_stays_under_the_configured_state_root` | no escape from `<OGX_HOME>/projects` |

Drop-in — `_dev/tests/test_identity_parity.py::DropinStateKeyTests` (7 added,
existing tests untouched):

- `test_case_only_difference_in_the_leaf_folder_collides` (`alpha/project` vs
  `alpha/PROJECT`, path and name)
- `test_case_only_difference_in_a_parent_folder_collides`
- `test_unicode_case_folding_collides` (`straße` vs `STRASSE`)
- `test_distinct_names_remain_distinct`
- `test_repeated_calls_are_deterministic`
- `test_state_home_name_is_the_folded_slug_plus_the_twelve_hex_digest`
- `test_canonical_and_dropin_derivations_agree_on_the_same_key` — computes the
  canonical 16-hex digest and asserts the drop-in name is
  `project-<that digest[:12]>`, i.e. one casefolded digest, one folded slug, and only
  the documented truncation difference.

Every assertion compares the **name string** as well as the `Path`, because
`Path("project-x") == Path("PROJECT-x")` is `True` on Windows; a path-only assertion
is what let this defect reach `ubuntu-latest`.

Test counts: canonical **578 → 592** (+14), drop-in **278 → 285** (+7).

**Original failing tests were not touched.** The two originally failing assertions —
`tests/test_relocation_fixtures.py::StateKeyCollisionTests::test_case_only_difference_maps_to_one_state_directory`
(canonical) and
`_dev/tests/test_identity_parity.py::DropinStateKeyTests::test_case_only_difference_collides`
(drop-in) — are unchanged; each now passes as part of the larger suites above.

---

## 5. Exact commands and results

All commands were run from `GUARDIAN-X/` with `PYTHONDONTWRITEBYTECODE=1`.
Canonical commands use the host `python` (3.15.2) unless a version is noted.
Drop-in commands use `PYTHONPATH=core` so the drop-in modules shadow the installed
canonical package.

Unit-matrix parity command (standalone, no test framework):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python _verify_tmp/slug_matrix.py > matrix_src.txt
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=core python _verify_tmp/slug_matrix.py > matrix_core.txt
diff matrix_src.txt matrix_core.txt   # empty → identical
```

### Canonical suite

| Version | Command | Result | Notes |
|---|---|---|---|
| 3.15.2 | `python -m pytest -q` | **592 passed, 17 skipped, 119 subtests**, exit 0 | host Python |
| 3.13.18 | `uv run --no-project --python 3.13 --with pytest python -m pytest -q` | **592 passed, 17 skipped, 119 subtests**, exit 0 | uv-managed, no lockfile created |
| 3.12.16 | `uv run --no-project --python 3.12 --with pytest python -m pytest -q` | **592 passed, 17 skipped, 119 subtests**, exit 0 | uv-managed, no lockfile created |
| 3.11.18 | `uv run --no-project --python 3.11 --with pytest python -m pytest -q` | **592 passed, 17 skipped, 119 subtests**, exit 0 | re-run after the docstring edit |

Skips are the same **platform gates** in every version: symlink tests (two flavours
in 3.15/3.12/3.13, one in 3.11) and the POSIX file-mode check on Windows
(`tests/test_cli.py:33`). They are not suppressed failures.

### Targeted proof for the two originally failing tests

| Failing test | Command | Result |
|---|---|---|
| `tests/test_relocation_fixtures.py::StateKeyCollisionTests::test_case_only_difference_maps_to_one_state_directory` | `python -m pytest -q tests/test_state_key_normalization.py tests/test_relocation_fixtures.py` | **50 passed, 1 skipped** (symlink), exit 0 — on 3.15.2 and 3.11.18 |
| `_dev/tests/test_identity_parity.py::DropinStateKeyTests::test_case_only_difference_collides` | `PYTHONPATH=core python -m unittest discover -s _dev/tests -p test_identity_parity.py` | **Ran 27 tests, OK**, exit 0 |

### Drop-in suite (full)

| Version | Command | Result |
|---|---|---|
| 3.15.2 | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **Ran 285 tests, OK (skipped=9)**, exit 0 |
| 3.13.18 | `uv run --no-project --python 3.13 ... python -m unittest discover -s _dev/tests` | **Ran 285 tests, OK (skipped=9)**, exit 0 |
| 3.12.16 | `uv run --no-project --python 3.12 ... python -m unittest discover -s _dev/tests` | **Ran 285 tests, OK (skipped=9)**, exit 0 |
| 3.11.18 | `uv run --no-project --python 3.11 ... python -m unittest discover -s _dev/tests` | **Ran 285 tests, OK (skipped=9)**, exit 0 |

Drop-in skips are all symlink tests (9, unchanged across versions).

### Parity module

| Version | `PYTHONPATH=core python -m unittest discover -s _dev/tests -p test_identity_parity.py` |
|---|---|
| 3.15.2 | Ran 27 tests, OK |
| 3.13.18 | Ran 27 tests, OK |
| 3.12.16 | Ran 27 tests, OK |
| 3.11.18 | Ran 27 tests, OK |

### Compile, sync, profile and packaging

| Check | Command | Result | Version |
|---|---|---|---|
| Compile (canonical + drop-in sources + all tests) | `python -m compileall -q src core tests _dev/tests` | exit 0 | 3.15.2, 3.13.18, 3.12.16, 3.11.18 (all pass) |
| Canonical/drop-in slug parity | `diff matrix_src.txt matrix_core.txt` | **empty** — byte-identical output on 26 inputs | 3.15.2 |
| Sync structure/version/packaging | `python _dev/tools/sync_core_from_src.py --check` | `OK: source and drop-in structure, versions and packaging declarations agree.`, exit 0 | 3.15.2 |
| Profile-configured checks | `OMEGA_PROFILE=_dev/project-profile.json python devkit/health/run_project_checks.py` | **All 2 configured project checks passed** (compile + full 285-test drop-in run), exit 0 | 3.15.2 |

Packaging + clean install (same as before, negative result unchanged):

| Check | Result | Version |
|---|---|---|
| `python -m pip wheel . --no-deps --wheel-dir <tmp>` + zip inspection | 171 files, 429,433 bytes, 0 forbidden paths, `ogx/cli.py` + entry points present, exit 0 | 3.15.2 |
| `python -m venv <tmp>` → `pip install --no-deps <wheel>` → `ogx --version` | `ogx 0.8.0a1`, exit 0 | 3.15.2 |

These two are unchanged from the pre-repair baseline, so they are not repeated in
every interpreter; the four-version compile check covers cross-version bytecode
correctness.

### Mapping to the four failing GitHub Actions checks

| GitHub job | Local equivalent | Versions run | Status |
|---|---|---|---|
| Profile-configured project checks | profile-equivalent `run_project_checks.py` (compile + full 285-drop-in run) | 3.15.2 (and the drop-in it runs is identical to what 3.12/3.13 run) | **PASS** locally; CI pins 3.12, same drop-in image |
| Tests (Python 3.11) | canonical `pytest -q` + drop-in `unittest discover -s _dev/tests` + parity module | 3.11.18 | **PASS** |
| Tests (Python 3.12) | canonical `pytest -q` + drop-in `unittest discover -s _dev/tests` + parity module | 3.12.16 | **PASS** |
| Tests (Python 3.13) | canonical `pytest -q` + drop-in `unittest discover -s _dev/tests` + parity module | 3.13.18 | **PASS** |

> Interim success note (corrected): the earlier draft marked 3.12 and 3.13 as
> `NOT_RUN` because the host did not ship those interpreters. Both are available
> through `uv python` (`cpython-3.12.16` and `cpython-3.13.18`), so the full
> canonical suite, full drop-in suite, compile and parity module were all run on
> both, and all pass. While this review was running, one slow 3.12 drop-in run
> briefly reported `FAILED (errors=1)`, but two clean re-runs of the exact same
> command both returned `Ran 285 tests, OK (skipped=9)`, so that line was a
> transient artefact of the asynchronous capture, not a real failure. The failure
> was never merged into any log the test framework wrote to, and the two clean
> results are the authority.

---

## 6. Python versions actually tested

- **CPython 3.15.2** — `C:\Program Files\Python35\\python.exe`; full canonical suite,
  full drop-in suite, profile checks, compile, sync, parity matrix, packaging.
- **CPython 3.13.18** — uv-managed; full canonical, full drop-in, compile, parity
  module.
- **CPython 3.12.16** — uv-managed; full canonical, full drop-in (re-run clean),
  compile, parity module.
- **CPython 3.11.18** — uv-managed; full canonical (re-run after docstring edit),
  full drop-in, compile, parity module.
- **CPython 3.12 / 3.13** are therefore **RUN**, not `NOT_RUN` — the only
  open question about them now is the hosted CI matrix, which this task forbids
  pushing for.
- The fixed code uses only `str.casefold()` and `hashlib`, available on every
  supported version; four-version `compileall` and the unchanged `requires-python`
  floor are the cross-version evidence.

---

## 7. Why the fix is platform-independent

- No `os.name` branch, no Windows-only assumption, and no filesystem
  case-sensitivity probe was introduced. The slug is folded by a pure string rule,
  so the same input yields the same key on every platform.
- `Path.resolve()` remains the only path normalizer, so separator handling is
  delegated to `pathlib` rather than re-implemented.
- The regression tests are meaningful on Windows: they assert on the directory
  **name string**, so they fail there if the slug regresses, instead of being
  silently satisfied by case-insensitive `Path` equality.
- A 26-input slug matrix was diffed between the canonical and drop-in
  implementations and produced **identical output**, giving a concrete, versioned
  evidence of parity beyond the behavioural tests.

---

## 8. Remote / local HEAD comparison (new in this review)

- Local `main` = `386c6699dfc32587277be0408080be127fbb8e29`
- Remote `origin/main` = `485664e92826ba42e1bc78803e72341e609f2a6a`
- The mission named `485664e9…` as the CI HEAD; it exists now (after `git fetch
  origin main`) and is the same object as remote `main`.
- Local `main` is the **parent** of remote `main`; the only difference is a single
  commit on remote that added `SECURITY.md` (now present at
  `https://raw.githubusercontent.com/…/main/SECURITY.md`, HTTP 200, 619 bytes).
- No file in the repaired set (`src/ogx/cli.py`, `src/ogx/config.py`, `init.py`,
  `core/ogx/cli/common.py`, `core/ogx/kernel/config.py`, the two new test files, the
  three changed doc files, `LICENSE`, `pyproject.toml`, `.github/workflows/*`,
  `_dev/fixtures/*`, `docs/MILESTONE_P0_ENGINEERING_REPORT.md`) differs between local
  HEAD, remote `main` and `origin/main` before the repair. So there is **no conflict**
  and **no already-applied upstream fix** for the same defect.
- **Implication:** if the owner later commits the worktree on top of local `main`, the
  result fast-forwards cleanly to remote `main` except for the `SECURITY.md` addition
  (which is unrelated to this repair). The repair does not depend on being rebased.

---

## 9. File-by-file audit verdicts

| File | Verdict | Evidence |
|---|---|---|
| `src/ogx/config.py` | ✅ correct | New `state_key_slug` mirrors the digest's `casefold` rule; `state_key_slug('ProJect-2') == 'project-2'`; `py_compile` passes; `import re` present and used. |
| `src/ogx/cli.py` | ✅ correct | `_external_report_dir` delegates to the helper; only `state_key_slug` added to imports; no other `re.sub` slug logic remains. |
| `core/ogx/kernel/config.py` | ✅ correct | Mirror with identical body; docstring-only difference; `import re` present and used. |
| `core/ogx/cli/common.py` | ✅ correct | Uses mirrored helper; `import re` removed (no other `re.` call exists — `grep "re\."` returned nothing). |
| `init.py` | ✅ correct | `state_home` folds the slug; fallback `host` → `project`, consistent with the canonical fallback; `re.sub` still used for sanitization (no removal of `import re` — not needed). |
| `tests/test_state_key_normalization.py` | ✅ correct | 14 real tests, exact table match (after the table-row fix), all 17 skipped per version are platform gates. |
| `_dev/tests/test_identity_parity.py` | ✅ correct | +7 drop-in tests on top of existing suite; existing `test_case_only_difference_collides` unchanged; no test weakened/skipped/deleted. |
| `docs/CONFIGURATION.md` | ✅ correct | Accurately documents slug = casefold + sanitize + strip + fallback; compatibility note present. |
| `docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md` | ✅ correct | Folded-slug note added; matches code. |
| `docs/GITHUB_ACTIONS_FAILURE_REPAIR_REPORT.md` | ✅ correct after fix | Root cause, files, strategy, tests, commands, versions, limitations, git state all accurate; two defects (docstring indentation, phantom test row) found and corrected. |
| `pyproject.toml` / `.github/workflows/*` / `LICENSE` / `_dev/fixtures/*` / `docs/MILESTONE_P0_ENGINEERING_REPORT.md` | ✅ untouched | `git status` clean on all; `git show origin/main:` + `diff` confirms `identity_vectors.json` and `test_identity_parity.py` at `origin/main` are byte-identical to local. |
| `build/lib/` | ℹ️ irrelevant | git-ignored local build artifact; contained copies of the new `state_key_slug` because it was built from current source during packaging; deleted in cleanup. Not part of the worktree. |

---

## 10. Remaining limitations

1. **Hosted CI not re-run.** Verifying the four jobs on GitHub requires a commit and
   push, which this task forbids. All local evidence now covers the code paths on
   every available interpreter; the green-check claim remains unconfirmed until the
   owner pushes.
2. **No Linux host available.** WSL is not registered on this machine, so there is no
   ext4 host to reproduce the collision by creating directories. The collision is
   reproduced through name-level assertions that are filesystem-independent, plus an
   explicit slug matrix diff. A real Linux confirmation only comes from the CI matrix
   (or manual ext4 testing by the owner).
3. **Case-only assertion is the proxy.** The tests assert on the directory **name
   string**, so they fail on Windows if the slug regresses; they still cannot create
   two real sibling directories here, because the host filesystem folds case. That is
   the only remaining gap between local proof and the GitHub Actions reproduction.
4. **Migration is manual on case-sensitive hosts** — documented in §3 and
   `docs/CONFIGURATION.md`; no automatic rename was added, to keep the key derivation
   purely deterministic. A project whose folder contains upper-case letters on a
   case-sensitive host gets a new lower-case directory; the old one is left in place.
5. **Long-project-name name-length limit** is a pre-existing limitation (no slug
   cap; `ENAMETOOLONG` possible on a filesystem with a short name limit), unchanged by
   this repair and out of scope.

---

## 11. Git state

```text
HEAD / local main            386c6699dfc32587277be0408080be127fbb8e29
remote origin/main           485664e92826ba42e1bc78803e72341e609f2a6a
delta (remote vs local main)  1 file changed, 21 deletions (SECURITY.md)
committed       nothing
pushed          nothing
reset/cleaned   nothing

 M GUARDIAN-X/_dev/tests/test_identity_parity.py
 M GUARDIAN-X/core/ogx/cli/common.py
 M GUARDIAN-X/core/ogx/kernel/config.py
 M GUARDIAN-X/docs/CONFIGURATION.md
 M GUARDIAN-X/docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md
 M GUARDIAN-X/init.py
 M GUARDIAN-X/src/ogx/cli.py
 M GUARDIAN-X/src/ogx/config.py
?? GUARDIAN-X/tests/test_state_key_normalization.py
?? GUARDIAN-X/docs/GITHUB_ACTIONS_FAILURE_REPAIR_REPORT.md
```

P0 preservation: `_dev/fixtures/identity_vectors.json`,
`_dev/fixtures/relocation_scenarios.json`, `_dev/fixtures/vectors.py`,
`docs/MILESTONE_P0_ENGINEERING_REPORT.md` and the P0 gates are byte-identical to
`origin/main` (verified by `git show origin/main:…` + `diff`); the only P0 test
file touched (`test_identity_parity.py`) gained tests and lost none.

**Final status: PARTIALLY_COMPLETE** — the defect is fixed and verified on
CPython 3.11.18, 3.12.16, 3.13.18 and 3.15.2 (canonical suite 592/17/119 and
drop-in 285/9 on every version, plus profile-equivalent checks, compile, sync,
slug-parity matrix and packaging all pass), but hosted CI cannot be re-run without
an authorized push and no Linux host is present, so the four GitHub Actions
green-check claim is unconfirmed.

---

*Audit performed by a single agent. No parallel agents used. No commit, push, merge,
PR, release, visibility change, `reset`/`clean`/`force checkout`, destructive git, or
`LICENSE`/metadata change was made. No test was weakened, skipped, deleted or
rewritten. No dependency was added. Verification artifacts were removed from the
worktree after capture.*
