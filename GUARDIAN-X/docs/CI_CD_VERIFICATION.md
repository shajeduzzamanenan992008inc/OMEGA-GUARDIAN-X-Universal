# CI/CD Verification

Date: 2026-11-26
Repository: `shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal`
Verified at HEAD: `7135b6ec9099facf844d4247058ec0846c80eaab` (`main`, clean tree,
`origin/main` = same SHA)

## 1. Where the authoritative workflows live

The Git root of this project is `OMEGA-GUARDIAN-X-Universal/`; the product tree is
`GUARDIAN-X/`. GitHub Actions only reads `.github/workflows/` at the **repository
root**, so the authoritative definitions are:

| File | Workflow name | Jobs / steps |
| --- | --- | --- |
| `.github/workflows/ci.yml` | `OGX CI` | 4 jobs / 18 steps |
| `.github/workflows/security.yml` | `OGX Code Security` | 1 job / 4 steps |
| `.github/workflows/config-validation.yml` | `GitHub Configuration Validation` | 1 job / 2 steps |
| `.github/workflows/dependency-review.yml` | `Dependency Review` | 1 job / 2 steps |

`GUARDIAN-X/.github/workflows/` holds a **standalone-distribution mirror** of the
same four workflows (identical job/step shape, product-relative paths). It is
ignored by GitHub in this monorepo and only runs if `GUARDIAN-X/` is exported as
its own repository root. `GUARDIAN-X/.github/workflows/README.md` records that
rule, so the two sets are no longer an undocumented, misleading duplicate.

## 2. What `OGX CI` executes

All jobs run on `ubuntu-latest`, `permissions: contents: read`,
`persist-credentials: false`, `timeout-minutes` set, and a concurrency group with
`cancel-in-progress: true`.

| Job | Steps |
| --- | --- |
| `Tests (Python 3.11 / 3.12 / 3.13)` | checkout → setup-python (matrix, `fail-fast: false`) → install pytest → `compileall -q src core tests _dev/tests` → `pytest -q` → drop-in `unittest discover` with an explicit zero-collection guard → `sync_core_from_src.py --check` |
| `Profile-configured project checks` | checkout → Python 3.12 → `devkit/health/run_project_checks.py` with `OMEGA_PROFILE=_dev/project-profile.json` |
| `Wheel build and clean install` | build wheel → inspect archive for development-file leakage and required `ogx/` modules/entry points → install into a fresh venv → `ogx --version`, `--help`, `providers list`, `platform status` |
| `Drop-in distribution` | `PYTHONPATH=core python -m ogx --version`, `python init.py status` → `make_dist.py` → inspect zip for forbidden paths and required `omega-shield/` entries |

Failure hiding was audited: there is no `|| true`, no `continue-on-error` and no
skipped step anywhere in either workflow set. `pytest` exits 5 on an empty suite,
and the drop-in step parses `Ran N tests` and exits 1 when `N == 0`.

Actions are pinned by full commit SHA: `actions/checkout@11d5960a…` (v4),
`actions/setup-python@a26af69…` (v5), `github/codeql-action/*@d2aacf54…` (v3),
`actions/dependency-review-action@2031cfc0…` (v4.9.0).

## 3. Hosted run status: BLOCKED (account billing, not code)

The last observed push-triggered runs (`OGX CI` run `45742613573`, `OGX Code
Security` run `45742613443`, commit `e695569`) concluded `failure` with **zero
executed steps**. Every job shows `runner_id = 0`, an empty `steps` array, a
2-second lifecycle and zero billable milliseconds, with GitHub's annotation:

> The job was not started because recent account payments have failed or your
> spending limit needs to be increased.

No test, packaging, drop-in or CodeQL job has ever run for the current HEAD. This
is an account-level restriction and cannot be fixed from the repository. Full
evidence is in `CI_FAILURE_ROOT_CAUSE_REPORT.md`. Hosted CI is therefore
**NOT VERIFIED**, not passing.

## 4. Local equivalents executed for this HEAD

Run from `GUARDIAN-X/` on Windows / CPython 3.15.2 on 2026-11-26:

| Command | Result |
| --- | --- |
| `python -m pytest tests -q` | **578 passed, 17 skipped, 102 subtests passed**, 150.23 s, exit 0 |
| `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **Ran 278 tests — OK (skipped=9)**, 88.5 s, exit 0 |
| `OMEGA_PROFILE=_dev/project-profile.json python devkit/health/run_project_checks.py` | **2/2 checks PASS**, exit 0 (includes the 278-test drop-in run) |
| `python -m compileall -q src core tests _dev/tests` | exit 0 |
| `python _dev/tools/sync_core_from_src.py --check` | exit 0 |
| `python -m pip wheel . --no-deps` + clean-venv install + `ogx` smoke | see `MEGA_ULTRA_FINAL_COMPLETION_REPORT.md` §D |
| `python _dev/tools/make_dist.py` + archive inspection | see §D of the same report |

Workflow YAML was parsed with PyYAML 6.0.4: all eight files load, and each pair
(root vs product) has an identical job/step shape (ci 4/18, security 1/4,
config-validation 1/2, dependency-review 1/2).

`ruby _dev/tools/validate_config.rb` (the config-validation job body) was **NOT
RUN** locally: no Ruby interpreter exists on this machine. It is exercised only by
hosted CI, which is billing-blocked.

## 5. Remaining CI gaps

| Gap | Severity | Status |
| --- | --- | --- |
| Hosted runs refused by account billing | High | **BLOCKED** — owner action in GitHub Billing & plans; no repository change can fix it |
| Runners are Linux-only (`ubuntu-latest`) | Medium | Windows/macOS behaviour is exercised only locally on Windows; no Windows/macOS runner job |
| `dependency-review` needs GitHub Advanced Security on private repos | Medium | Availability unverified |
| No artifact signing / provenance (SLSA, attestations) | Medium | NOT_IMPLEMENTED |
| Python 3.11/3.12/3.13 not installable locally | Medium | Matrix is CI-only; local interpreter is 3.15.2 (outside the declared range) |
| No CI job for the Rust/Go/JS kit templates | Low | Templates are documentation examples, not built products |

## 6. Required next action

The repository owner must clear the GitHub Actions billing/spending-limit block,
then push an authorized commit. Until a hosted run executes the 6 jobs and 7
checks green, CI status stays **UNVERIFIED** in every report.
