# Host Lifecycle CI Validation

Date: 2026-11-24
Static inspection: performed from `D:/FTP/OMEGA-GUARDIAN-X-Universal`

## 1. What was validated

### Automated tests (local execution)

- Canonical suite: **465 passed, 15 skipped, 0 failed** (`python -m pytest -q`).
  This includes the new host classes under `tests/test_host_lifecycle.py` (13),
  `tests/test_host_filesystem_runtime.py` (16 passed, 3 skipped),
  `tests/test_host_git_runtime.py` (21), and `tests/test_host_cli_lifecycle.py`
  (6 — all 6 host lifecycle CLI tests now pass).
- Drop-in distribution tests: **Ran 245 tests, OK (skipped=9)**
  (`PYTHONPATH=core python -m unittest discover -s _dev/tests`).
- Compilation: `python -m compileall -q src/core/tests/_dev/tests` pass.
- Sync consistency: `python _dev/tools/sync_core_from_src.py --check` reports OK.

### GitHub Configuration Inspection
Root workflows:
- `../.github/workflows/ci.yml`: run at push/pull_request/dispatch via native action pins and a Python matrix [3.11,3.12,3.13]. Runs canonical pytest, drop-in unittest, and sync check. Compiles src/core/tests/_dev/tests.
- `../.github/workflows/config-validation.yml`: validates the `GUARDIAN-X/.github` configuration, project profile, Copilot frontmatter, and active workflow safety via Ruby `validate_config.rb` (and local YAML/JSON/frontmatter checks). Action refs pinned with full 40-character SHAs; `permissions: write-all` and `pull_request_target` forbidden; top-level `permissions:` declared.
- `../.github/workflows/dependency-review.yml`, `security.yml`: present.

Concluded:
- Root workflow files (`../.github`) are the active set; GitHub does not execute workflows from a nested `.github` directory.
- The project carries an inert duplicate of the workflow files under
  `GUARDIAN-X/.github/workflows/`. They are identical in name but **not** the
  active set; the root files are authoritative. Raising the root-level
  config-validation path gate was initiated: `.github/workflows/**` is now on
  the push/pull_request path list so root workflow edits trigger this check.

## 2. What could not be verified

- Cross-version run: only CPython 3.15.2 is installed locally. The CI matrix
  runs 3.11-3.13, so cross-version behavior is model-inspected only, not run.
- Linux/macOS-specific symlink and POSIX file-mode paths are skipped on this
  Windows host; symlink tests are skipped here.
- No hosted CI was executed; no push, merge, publish or PR was created.

## 3. Static assertion pass/fail seen here

- `scripts` in `src/ogx`, `core/ogx`, `tests`, `_dev/tests` compile cleanly.
- No `UnboundLocalError`-class docstrings or undefined symbols in the lifecycle
  CLI path: added the missing `uuid4` import that caused test failures earlier in
  this milestone.
- Host CLI now returns 0 for healthy session flows; disconnection leaves the
  session closed and removed from the live list; status reflects the true
  process-local live state plus the coarse persisted status.

## 4. Limitations stated up front

- Host runtime is synchronous and process-local; CI cannot be used to assert
  multi-process or daemon behaviour because those do not exist.
- The local-Git and local-filesystem adapters are read-only by construction; CI
  exercises their refusal paths but cannot exercise a missing write-path defect.
- Audit tamper-evidence is hashed into the platform log; it is not an external
  tamper-proof store.

## 5. Intent of this doc

This document is the **Host Lifecycle CI Validation** record for the milestone.
It makes explicit which lifecycle, adapter and CLI behaviours were executed and
passed, which were only statically inspected, and which were out of scope for
this environment.
