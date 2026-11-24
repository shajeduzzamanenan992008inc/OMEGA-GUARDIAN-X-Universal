# Release Baseline Verification (Phase 1)

Date: 2026-11-24
Starting commit: `c441f01403c2ebcd877970d880d1e66c625274eb`
Environment: Windows (`win32`), CPython **3.15.2**, no provider credentials,
no Docker run, Ruby unavailable, PyYAML unavailable.

Historical results were not trusted; each command below was executed in this
session before the hardening changes (except where marked "after").

## 1. Reproduced baseline

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `git status --porcelain` | 0 | clean |
| 2 | `python -m pytest -q` | 0 | **340 passed, 11 skipped** (46.97 s) |
| 3 | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | 0 | **Ran 245 tests, OK (9 skipped)** (64.16 s) |
| 4 | `python _dev/tools/sync_core_from_src.py --check` | 0 | versions `{VERSION: 0.8.0a1, src: 0.8.0a1, core: 0.8.0a1}`; structure agrees |
| 5 | `python -m pip wheel . --no-deps --no-build-isolation -w <tmp>` | 0 | 163 entries, 390,734 bytes, SHA-256 `c9fdde03…ae0a` |
| 6 | fresh venv install of that wheel | 0 | `import ogx` → `0.8.0a1`; `ogx --version` → `ogx 0.8.0a1`; `providers list` exit 0 |
| 7 | `python _dev/tools/make_dist.py <tmp>` | 0 | 280 files; `init.py` + `core/ogx/__init__.py` present; 0 forbidden |
| 8 | extracted drop-in `python init.py status` | 0 | state created outside host tree |
| 9 | `python -m ogx providers list` | 0 | 28 providers |
| 10 | `python -m ogx platform status` | 0 | verified 7, descriptor-only 21, credentials 0 |
| 11 | `python -m ogx host list` | 0 | `No hosts registered.` |

The 11 canonical skips are Windows symlink/POSIX-mode gates. No failure was
hidden or converted into a skip.

## 2. Baseline test count discrepancy check

`tests/` contained 303 test functions but 340 passing tests because several are
parametrized or class-based. `_dev/tests` contained 245 functions and ran 245
tests. Counts are consistent; there is no unexplained delta.

## 3. Baseline security observations

- The transport validated hostnames only; DNS was resolved later by `urllib`.
- Audit entries had no integrity envelope and no verification command.
- No host could be read successfully (defect I-03), which the baseline suite did
  not cover because there was no host test.
- Identity normalization dropped non-default ports.

These became the Phase 2–4 work items.

## 4. Tooling limitations observed

- `python -m build` fails because a local `build/` directory shadows the module;
  `pip wheel` is the working path.
- Native Windows Python does not resolve Git Bash's `/tmp` mapping; temporary
  artifacts must live inside the project (or use `cygpath -w`).
- A drop-in zip built while a temporary directory exists under `GUARDIAN-X/`
  includes that directory. Build from a clean tree.
- PyYAML is absent, so `.github/workflows/*.yml` could not be parsed this pass;
  workflow structure was verified by inspection only.

## 5. Conclusion

The baseline reproduced exactly. No baseline failure was carried into the
release decision as a pass.
