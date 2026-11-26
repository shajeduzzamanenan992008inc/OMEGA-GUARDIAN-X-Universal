# CI/CD Verification (Phase 6)

Date: 2026-11-24
Repository-level workflow directory: `.github/workflows/` (correct location).

## 1. Workflows present

| File | Purpose (inspected content) |
| --- | --- |
| `ci.yml` | test matrix, profile checks, packaging, drop-in validation |
| `config-validation.yml` | configuration validation |
| `dependency-review.yml` | dependency review on pull requests |
| `security.yml` | CodeQL analysis |

## 2. `ci.yml` facts verified by inspection

- `runs-on: ubuntu-latest` for all jobs (Linux only).
- Python matrix `["3.11", "3.12", "3.13"]` for the test job.
- `working-directory: GUARDIAN-X` on the run steps.
- Actions pinned by full commit SHA:
  `actions/checkout@11d5960…` (v4), `actions/setup-python@a26af69…` (v5).
- Top-level `permissions:` block with job-level permissions.
- Separate jobs for tests, profile checks (Python 3.12), packaging (3.12) and
  drop-in validation (3.12).

`security.yml` uses pinned CodeQL action SHAs (`d2aacf54…`, v3).

## 3. What was executed

- Files exist at the repository root and were inspected with `grep`.
- **No workflow was executed in this pass.** Nothing was pushed in this pass.
- **Milestone I.3 update (2026-11-26):** hosted runs do exist. For `757d6b6`
  the three push workflows (`OGX CI`, `GitHub Configuration Validation`,
  `OGX Code Security`) each concluded `failure` with zero executed steps.
  GitHub's annotation states the jobs were not started because recent account
  payments failed or the spending limit must be increased. No test, packaging
  or drop-in job ran. See `MILESTONE_I3_CI_EVIDENCE_REPORT.md`.
- YAML parsing was **not** performed in this pass: PyYAML is unavailable in the
  current interpreter (`import yaml` fails). A previous pass parsed these files
  successfully, but that is historical, not current evidence.

## 4. Gaps

| Gap | Severity | Note |
| --- | --- | --- |
| Hosted CI blocked by account billing | High | Runs exist for `757d6b6`, but every job was refused before starting; no hosted result can be claimed |
| Linux-only runners | Medium | Windows/macOS untested in CI |
| Python 3.15 not in matrix | Low | Local verification used 3.15.2 only |
| `dependency-review` requires GitHub Advanced Security | Medium | Availability unverified |
| No artifact signing/provenance job | Medium | Not implemented |
| No explicit audit/host/provider contract job | Low | They run as part of pytest |

## 5. Required next action

Push a branch (with authorization) and record the actual job results and URLs.
Until then, the CI status is **UNVERIFIED**, not passing.

## 6. Local equivalents executed instead

```bash
cd GUARDIAN-X
python -m pytest -q
PYTHONPATH=core python -m unittest discover -s _dev/tests
python -m compileall -q src/ogx tests
python _dev/tools/sync_core_from_src.py --check
python -m pip wheel . --no-deps --no-build-isolation -w <dir>
```

Results: 465 passed / 15 skipped at HEAD `e7e7686`; 245 drop-in tests OK (9 skipped); compile and
sync checks passed; wheel built and installed in a fresh venv.

## 7. Phase 0 baseline re-verification (2026-11-26)

At HEAD `36e56c0` (clean tree, `origin/main` 0/0) the local equivalents were
re-executed: canonical **465 passed, 15 skipped** (90.58 s, exit 0), drop-in
**258 tests, OK (9 skipped)** (71.25 s, exit 0), archive regression suite
**13 tests, OK**, compile and sync checks exit 0. No hosted workflow was
executed or queried for `36e56c0`; the billing blocker recorded in §3 is
unchanged. See `../../docs/MASTER_BASELINE_AUDIT.md`.
