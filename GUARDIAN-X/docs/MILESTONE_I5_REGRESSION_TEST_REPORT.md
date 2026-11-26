# Milestone I.5 — Regression Test Report

Date: 2026-11-26
Baseline HEAD: `a004348cd371fc1a42aefcaacf7e31b577026476`
Generator: `GUARDIAN-X/_dev/tools/make_dist.py`
Test module: `GUARDIAN-X/_dev/tests/test_make_dist.py`

## 1. Deliverables

1. **`GUARDIAN-X/_dev/tests/test_make_dist.py`** — a dedicated, 13-test
   regression suite for the drop-in archive generator.
2. **`GUARDIAN-X/_dev/tools/make_dist.py`** — a behavior-preserving extraction
   of `build_archive(root, out)` so the real generator can be exercised against
   synthetic fixture trees.

No existing test, CI assertion, workflow or required-file list was changed.

## 2. Generator change

Before, all logic lived inside `main()` and was bound to the module-level
`ROOT`; `VERSION` was read at import time. After:

```python
def build_archive(root: Path, out: Path) -> int:
    """Write the drop-in zip for ``root`` to ``out`` and return the entry count."""
    ...

def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    ...
    count = build_archive(ROOT, out)
```

The policy is untouched: `EXCLUDE_TOP_FILES`, depth-independent `EXCLUDE_DIRS`,
`*.egg-info`, `EXCLUDE_NAMES`, `EXCLUDE_SUFFIXES`, the `.env.*` rule with the
`.env.example` exception, sorted traversal, and the `omega-shield/` prefix.

The extraction is required because the test must exercise the real generator
against a controlled tree. Copying the script into a fixture would test a copy,
not the shipped tool.

## 3. Fixture design

Each test builds one synthetic tree in a `tempfile.TemporaryDirectory`. Nothing
reads the repository working tree.

Included (must ship):

```
VERSION
shield.toml
init.py
core/ogx/__init__.py
core/ogx/cli.py
guard/gate.py
docs/ARCHITECTURE.md
.env.example
```

Excluded (must not ship):

```
.github/workflows/ci.yml
GUARDIAN-X/.github/RELEASE_NOTES.md     # the exact I.3/I.4 defect shape
GUARDIAN-X/.git/config
pkg/_dev/tools/tool.py
pkg/tests/test_x.py
pkg/src/app.py
pkg/release/notes.txt
pkg/build/out.bin
pkg/dist/pkg.zip
pkg/.pytest_cache/CACHEDIR.TAG
pkg/__pycache__/mod.cpython-311.pyc
core/omega_guardian_x.egg-info/{PKG-INFO,SOURCES.txt}
pyproject.toml
.env
.env.local
results.txt
core/cache.pyc
```

## 4. Requirement mapping

| Directive requirement | Test method |
|---|---|
| 1. `.github` excluded at any directory depth | `test_github_is_excluded_at_any_depth` |
| 2. Nested `.git` excluded | `test_nested_git_is_excluded` |
| 3. Nested `_dev`, `tests`, `src` and cache directories excluded | `test_development_directories_are_excluded_at_any_depth`, `test_cache_directories_are_excluded` |
| 4. `*.egg-info` build metadata excluded | `test_egg_info_build_metadata_is_excluded` |
| 5. Required runtime files remain included | `test_required_runtime_files_are_included` |
| 6. `init.py`, `core/ogx/__init__.py`, VERSION, `shield.toml` present | `test_required_runtime_files_are_included` (explicit assertions) |
| 7. Expected `omega-shield/` prefix | `test_archive_uses_the_omega_shield_prefix_for_every_entry` |
| 8. Legitimate documentation and runtime directories retained | `test_documentation_and_runtime_directories_are_retained` |
| 9. No accidental omission of required runtime content | `test_archive_contains_exactly_the_expected_runtime_content` (exact set equality) |
| 10. Deterministic archive entry ordering | `test_entry_order_is_deterministic_and_path_independent` |

Additional coverage:

- `test_top_level_packaging_file_and_secret_rules` — `pyproject.toml`, `.env`,
  `.env.local`, `results.txt`, `*.pyc` excluded; `.env.example` retained.
- `test_no_absolute_or_traversing_entries` — no leading `/`, no `..` component,
  no backslash separators in archive names.
- `test_ci_forbidden_token_policy_passes_on_the_fixture` — runs the exact CI
  token tuple over the generated names.

## 5. Results

cwd = `GUARDIAN-X/`; exit codes are the commands' own.

| Check | Command | Result |
|---|---|---|
| Focused new suite | `PYTHONPATH=core python -m unittest discover -s _dev/tests -p "test_make_dist.py"` | **Ran 13 tests — OK**, 1.795 s, exit 0 |
| Drop-in suite | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **Ran 258 tests — OK (skipped=9)**, 59.092 s, exit 0 |
| Canonical suite | `python -m pytest -q` | **465 passed, 15 skipped**, 74.76 s, exit 0 |
| Compile | `python -m compileall -q src core tests _dev/tests _dev/tools` | exit 0 |
| Source/drop-in sync | `python _dev/tools/sync_core_from_src.py --check` | OK, versions `0.8.0a1`, exit 0 |

The drop-in suite grew from 245 to 258 tests (+13), exactly matching the new
module. No existing test was changed, removed, skipped or weakened. The 9
skips are the pre-existing platform gates on Windows.

## 6. Behavior-preservation proof

The same clean `a004348` export was built twice: once with the committed
generator from `06ffef4`, once with the I.5 refactor overlaid.

| Generator | Entries | SHA-256 | Forbidden | Required |
|---|---|---|---|---|
| Committed (`06ffef4`) | 326 | `21ebccb43d330264892bfdf9bc1f0abecb566057fa2c91c70850a8125e9ba7ee` | 0 | present |
| I.5 refactor | 326 | `21ebccb43d330264892bfdf9bc1f0abecb566057fa2c91c70850a8125e9ba7ee` | 0 | present |

Entry lists identical; archive bytes identical. The refactor changed no shipped
content. Both builds above come from the same export; a second, independently
extracted export of `a004348` produced SHA-256
`403f683dfff25022fd1ab5c79bde3a3dbb04d4b9038da703484c737a362f22c9` with the same
326 entries, because ZIP headers record extraction mtimes.

## 7. CI integration

No workflow file was modified. The existing `tests` job in
`.github/workflows/ci.yml` runs the drop-in suite with
`unittest discover -s _dev/tests` and fails when zero tests are collected, so
the new module is collected by the same job on Python 3.11, 3.12 and 3.13. The
local count moving 245 → 258 through that exact command is the evidence that
collection happens on the same path. The `drop-in` job's inline forbidden-path
and required-file assertions are unchanged and remain the end-to-end check on
the real tree.

This is integration through the existing job, not a hosted run. Hosted CI did
not execute for this tree (see the final report).

## 8. Determinism and portability

- Two builds of the same fixture produce identical `namelist()`.
- Two independent fixture roots with identical content produce identical
  `namelist()`, so entry order does not depend on the absolute path.
- Entry order follows `sorted(root.rglob("*"))`. Windows path comparison is
  case-insensitive; POSIX comparison is case-sensitive. For mixed-case names
  the sequence can therefore differ between platforms even though the entry
  **set** is identical. The test asserts content equality plus repeat and
  path-independent determinism, not a fixed cross-platform sequence.
- ZIP bytes still record source mtimes, so byte-level reproducibility across
  exports or filesystems is not guaranteed. This is unchanged from the prior
  generator.

## 9. Limitations

- The suite is synthetic by design and does not assert the repository's own
  archive contents. The unmodified CI assertion and the clean-export run in this
  milestone cover the real tree.
- Hosted CI did not run, so the new test has not executed on Linux or on Python
  3.11/3.12/3.13.
- Wheel packaging is out of scope for I.5 (Milestone I.6, Task 8).
