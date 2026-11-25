# Milestone I.2 — CI Evidence Report

**Report date:** 2026-11-25
**Authoritative workflow location:** repository-root `.github/workflows/`
**Scope:** static inspection and local parsing only. **No workflow was
triggered, no branch was pushed, no pull request was opened, and no remote
state was changed.** Hosted execution was not authorized and was not attempted.

---

## 1. Which workflows can actually run

GitHub Actions only executes workflow files from `.github/workflows/` at the
**repository root**. This repository has two same-named workflow sets:

| Location | Files | Executed by GitHub? |
| --- | --- | --- |
| `.github/workflows/` (root) | 4 | **Yes** — authoritative |
| `GUARDIAN-X/.github/workflows/` | 4 | **No** — inert |

Both trees are tracked, and all eight files parse. The nested set is a
duplicate of the same names with different content:

```
$ diff -q .github/workflows/ci.yml GUARDIAN-X/.github/workflows/ci.yml
Files .github/workflows/ci.yml and GUARDIAN-X/.github/workflows/ci.yml differ
```
(identical result for `config-validation.yml`, `dependency-review.yml`,
`security.yml`)

All four root workflows use `defaults.run.working-directory: GUARDIAN-X`, which
is what makes them correct for this monorepo layout. The nested copies do not,
because they were written when `GUARDIAN-X/` was the repository root. The nested
copies are therefore both inert **and** wrong for this layout, and they must not
be treated as an equal second candidate. No nested file was modified or deleted.

Related finding: the Milestone I report asserted that the nested
`GUARDIAN-X/.github/` `workflows/` directory "is empty". It is not — it holds
four files. That correction is recorded in the addendum to
`docs/MILESTONE_I_FINAL_REPORT.md`.

---

## 2. YAML syntax verification

Real parser, not a heuristic: PyYAML 6.0.4 installed into an isolated temporary
virtual environment (`/tmp/yamlcheck`), run against each file. The system
interpreter has no `yaml` module, so this was the only way to obtain a real
parse rather than a structural guess.

```
PASS .github/workflows/ci.yml | jobs: ['tests', 'profile-checks', 'packaging', 'drop-in'] | triggers: ['push', 'pull_request', 'workflow_dispatch']
PASS .github/workflows/config-validation.yml | jobs: ['validate'] | triggers: ['push', 'pull_request', 'workflow_dispatch']
PASS .github/workflows/dependency-review.yml | jobs: ['dependency-review'] | triggers: ['pull_request']
PASS .github/workflows/security.yml | jobs: ['codeql'] | triggers: ['push', 'pull_request', 'schedule', 'workflow_dispatch']
PASS GUARDIAN-X/.github/workflows/ci.yml
PASS GUARDIAN-X/.github/workflows/config-validation.yml
PASS GUARDIAN-X/.github/workflows/dependency-review.yml
PASS GUARDIAN-X/.github/workflows/security.yml
```

8 of 8 files parse under `yaml.safe_load`. This establishes syntax only; it does
not establish that GitHub will accept the files semantically.

---

## 3. Triggers and path filters

| Workflow | Triggers | Path filters | Assessment |
| --- | --- | --- | --- |
| `ci.yml` | `push`, `pull_request`, `workflow_dispatch` | none | PASS — every push and PR runs the full matrix |
| `config-validation.yml` | `push`, `pull_request`, `workflow_dispatch` | `.github/workflows/**`, `GUARDIAN-X/.github/**`, `GUARDIAN-X/devkit/agents/**`, `GUARDIAN-X/devkit/health/**`, `GUARDIAN-X/link/templates/**`, `GUARDIAN-X/_dev/tools/**`, `GUARDIAN-X/SECURITY.md` | PASS — root workflow edits are in scope, which is what the tracker entry P11-1 records |
| `dependency-review.yml` | `pull_request` only | none | PASS for its purpose — dependency review is a PR-time control |
| `security.yml` | `push`, `pull_request`, `schedule: "23 4 * * 1"`, `workflow_dispatch` | none | PASS — weekly CodeQL plus per-PR analysis |

One coverage gap, recorded as an observation and not fixed (no workflow
modification was in scope): `config-validation.yml` does not watch
`GUARDIAN-X/docs/**` or the repository-root `docs/**`, so documentation-only
changes do not run the config validator. `ci.yml` has no path filter, so the test
matrix does run on documentation-only pushes — which is why `d55cea3` would
trigger `ci.yml` and `security.yml` but not `config-validation.yml` or
`dependency-review.yml`.

---

## 4. Python matrix

| Job | Python | Scope |
| --- | --- | --- |
| `tests` | `["3.11", "3.12", "3.13"]`, `fail-fast: false` | canonical pytest + drop-in unittest + sync check + compile |
| `profile-checks` | `3.12` | `devkit/health/run_project_checks.py` with `OMEGA_PROFILE` |
| `packaging` | `3.12` | wheel build, wheel content inspection, clean-venv install, CLI smoke |
| `drop-in` | `3.12` | drop-in import, entry point, archive build and content inspection |

PASS, with a note that is already documented honestly elsewhere: the matrix is
3.11–3.13 while local verification is CPython **3.15.2**, which is outside the
declared classifier range. The matrix has never executed, so nothing here
establishes 3.11–3.13 behaviour.

`guaranteed` audit: the matrix is not `["3.11", "3.12", "3.13", "3.15"]`, so
3.15.2 is not tested in CI at all — it is a local-only interpreter.

---

## 5. Job-level permissions

| Workflow | Top-level | Job-level | Assessment |
| --- | --- | --- | --- |
| `ci.yml` | `contents: read` | `contents: read` on all 4 jobs | PASS — least privilege |
| `config-validation.yml` | `contents: read` | `contents: read` | PASS |
| `dependency-review.yml` | `contents: read`, `pull-requests: read` | `contents: read`, `pull-requests: read` | PASS — exactly the two scopes the action needs |
| `security.yml` | `contents: read` | `contents: read`, `security-events: write` | PASS — the write scope is required to upload SARIF, and it is granted only to the CodeQL job |

No `write-all`, no `contents: write`, no `id-token: write`, no `actions: write`
anywhere in the authoritative set.

---

## 6. Third-party action pinning

Every `uses:` in the authoritative set is pinned to a full 40-hex commit SHA with
a version comment:

```
actions/checkout@11d5960a326750d5838078e36cf38b85af677262                      # v4
actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065                  # v5
actions/dependency-review-action@2031cfc080254a8a887f58cffee85186f0e49e48      # v4.9.0
github/codeql-action/init@d2aacf54c2fbc910db8d7ec338fdb54ea48c692d             # v3
github/codeql-action/autobuild@d2aacf54c2fbc910db8d7ec338fdb54ea48c692d        # v3
github/codeql-action/analyze@d2aacf54c2fbc910db8d7ec338fdb54ea48c692d          # v3
```

**PASS** — zero floating tags, zero branch refs, zero unpinned actions.

A claim in an earlier report that pinning was "mixed (tag-based elsewhere)" is
**not accurate for the authoritative set**: all six distinct pins are SHAs.
That observation most likely came from the nested copies; it is recorded here so
it is not carried forward.

---

## 7. Test, packaging and drop-in jobs

Each referenced path was checked for existence:

| Referenced by workflow | Path | Present |
| --- | --- | --- |
| `ci.yml` tests job | `GUARDIAN-X/src`, `core`, `tests`, `_dev/tests` | PRESENT |
| `ci.yml` tests job | `GUARDIAN-X/_dev/tools/sync_core_from_src.py` | PRESENT |
| `ci.yml` profile-checks | `GUARDIAN-X/devkit/health/run_project_checks.py` | PRESENT |
| `ci.yml` profile-checks | `GUARDIAN-X/_dev/project-profile.json` | PRESENT |
| `ci.yml` packaging | `GUARDIAN-X/src/ogx/cli.py`, `src/ogx/__init__.py` | PRESENT |
| `ci.yml` drop-in | `GUARDIAN-X/init.py`, `core/ogx/__init__.py` | PRESENT |
| `ci.yml` drop-in | `GUARDIAN-X/_dev/tools/make_dist.py` | PRESENT |
| `config-validation.yml` | `GUARDIAN-X/_dev/tools/validate_config.rb` | PRESENT |

**PASS.** The three scripted jobs also contain real fail-closed assertions
rather than trusting exit codes alone:

- the tests job re-parses `unittest` stderr for `Ran (\d+) test` and exits 1 on
  zero collection — this is the guard that a silently empty discovery cannot
  pass;
- the packaging job opens the wheel, rejects any of
  `tests/ _dev/ .github/ core/ src/ __pycache__ .pytest_cache/ .env`, requires
  `ogx/cli.py`, `ogx/__init__.py` and a dist-info `entry_points.txt`;
- the drop-in job opens the archive, rejects
  `/tests/ /_dev/ /.github/ /release/ /src/ __pycache__ /results.txt`, and
  requires `omega-shield/init.py` and `omega-shield/core/ogx/__init__.py`.

The packaging job's venv invocation uses `$RUNNER_TEMP/ogx-venv/bin/python`,
which is the correct layout for `ubuntu-latest`.

---

## 8. Working-directory correctness

`defaults.run.working-directory: GUARDIAN-X` is set on every job that has `run:`
steps in all four root workflows. `dependency-review.yml` has no `run:` steps, so
it correctly has no `defaults` block.

**PASS.** Without this, every `python` invocation would resolve relative to the
repository root, where there is no `tests/`, `src/ogx/` or `pyproject.toml`. This
is the defect the completion report records fixing at `e7e7686`.

---

## 9. Secret exposure risk in untrusted pull requests

Checked for the standard untrusted-PR hazards:

```
$ grep -rn "pull_request_target\|\${{ secrets" .github/workflows/
(none)
```

| Hazard | Present | Assessment |
| --- | --- | --- |
| `pull_request_target` + checkout of PR head | No | PASS |
| `secrets.*` interpolated into any workflow | No | PASS |
| `persist-credentials: true` on checkout | No — `persist-credentials: false` on every checkout step | PASS |
| Write-scoped `GITHUB_TOKEN` on a PR-triggered job | No — all PR-reachable jobs are `contents: read` | PASS |
| `workflow_dispatch`-only secrets | No secrets at all | PASS |
| Artifact-poisoning surface (`actions/download-artifact` on PR input) | No download-artifact step | PASS |

The only elevated scope in the entire set is `security-events: write` on the
CodeQL job, and `security.yml` runs on `push`, `schedule` and `workflow_dispatch`
as well as `pull_request`. On a fork PR the token is read-only by GitHub's own
default for that event regardless of the block, so this does not create a
write surface for untrusted contributors.

**PASS** on this specific class of risk. This is a static reading of the
workflow files; it is not a secret scan of the repository and it is not a
substitute for a real security review.

---

## 10. Do hosted runs exist?

**No hosted run was observed, and the answer cannot be established from this
environment.**

Attempts and results:

```
$ git ls-remote origin
e7e7686f834cdcfda35ae26c47ccd3420e93b698        HEAD
e7e7686f834cdcfda35ae26c47ccd3420e93b698        refs/heads/main

$ curl -sS .../repos/.../actions/runs?head_sha=e7e7686...
{"message": "Not Found", "status": "404"}

$ curl -sS .../repos/...
<no public metadata>
```

The repository metadata endpoint returned no public payload and the runs
endpoint returned `404`. For a repository that is private or internal, an
unauthenticated request is answered `404` rather than `403` — so this result is
consistent with either "no such runs" or "not visible to this caller". It is
**not** evidence that no runs exist. The honest classification is `UNKNOWN`, not
`NOT_RUN`, for the two workflows that trigger on every push.

What is established:

- `e7e7686` is the remote tip. Its tree contains all four root workflow files,
  so a push of that commit would have offered `ci.yml` and `security.yml` a
  trigger.
- `d55cea3` (which would also trigger them) is **not** on the remote at all.
- No credential for `gh` or for the GitHub API exists in this environment, so no
  run list, job log, artifact or check-run status could be read.

Therefore: the project has a coherent, correctly-placed, correctly-scoped CI
configuration that has **never been observed executing**. Any statement that CI
is green, red or previously green for this tree is unsupported.

---

## 11. Result classification

| # | Check | Result | Basis |
| --- | --- | --- | --- |
| 1 | Workflow YAML syntax (root, 4 files) | `PASS` | PyYAML 6.0.4 `safe_load`, 4/4 |
| 2 | Workflow YAML syntax (nested, 4 files) | `PASS` | PyYAML 6.0.4 `safe_load`, 4/4 (inert anyway) |
| 3 | Root is the only executed workflow location | `PASS` | GitHub layout rule; root set carries `working-directory: GUARDIAN-X` |
| 4 | Triggers and path filters | `PASS` | inspection, §3 |
| 5 | Python matrix 3.11/3.12/3.13 present, `fail-fast: false` | `PASS` | inspection, §4 |
| 6 | Job-level least-privilege permissions | `PASS` | inspection, §5 |
| 7 | Full-SHA pinning of all third-party actions | `PASS` | inspection, §6 |
| 8 | Test job present and non-vacuous | `PASS` | zero-collection guard, §7 |
| 9 | Packaging job present and non-vacuous | `PASS` | wheel content assertions, §7 |
| 10 | Drop-in job present and non-vacuous | `PASS` | archive content assertions, §7 |
| 11 | `working-directory: GUARDIAN-X` correctness | `PASS` | inspection, §8 |
| 12 | Secret exposure in untrusted PRs | `PASS` (static) | no `pull_request_target`, no `secrets.*`, no `persist-credentials: true` |
| 13 | `_dev/tools/validate_config.rb` executable locally | `NOT_RUN` | no Ruby in this environment |
| 14 | Hosted `ci.yml` run for `e7e7686` | `UNKNOWN` | API 404 unauthenticated; no run observed |
| 15 | Hosted `security.yml` / CodeQL results | `UNKNOWN` | same |
| 16 | Hosted `dependency-review.yml` run | `NOT_RUN` | no pull request exists; PR-only trigger |
| 17 | Hosted `config-validation.yml` run | `UNKNOWN` | same visibility limit as 14 |
| 18 | 3.11–3.13 behaviour actually exercised | `NOT_RUN` | matrix never executed |
| 19 | Linux runner behaviour | `NOT_RUN` | never executed |
| 20 | Scheduled CodeQL from the weekly cron | `UNKNOWN` | no run observed |

Sixteen checks are `PASS` on static evidence. Nothing is classified `FAIL`: no
defect was found in the authoritative workflow set. Nothing is claimed as a
hosted success.

---

## 12. What would move this from UNKNOWN to observed

In order of value, none of which was performed or authorized here:

1. One push of `d55cea3` to `main` (a fast-forward) and a read of the resulting
   `ci.yml` and `security.yml` run for that SHA.
2. Or a pull request from a temporary branch, which additionally exercises
   `dependency-review.yml`.
3. Or a `workflow_dispatch` run, which requires write access to the repository.
4. `gh` or an authenticated API token in the verification environment, which
   would at least let an existing run history be read without any push.

Item 4 is the cheapest and does not change remote state.

---

## 13. Actions deliberately not taken

- No workflow was triggered, dispatched or re-run.
- No branch was pushed.
- No pull request was opened.
- No workflow file, action pin, permission or trigger was modified.
- No nested workflow copy was modified or deleted.
- No secret was read, printed or tested.

---

## 14. Verdict

The authoritative CI configuration at `.github/workflows/` is **static-pass**:
valid YAML, correct monorepo working directory, least-privilege permissions,
fully SHA-pinned actions, a real 3.11/3.12/3.13 test matrix, and three
non-vacuous packaging/distribution jobs with fail-closed content assertions.

**No hosted execution is observed or claimed.** The single nested duplicate set
is inert and must not be counted as a second working configuration. Hosted
evidence remains the one outstanding item for this project's CI story.

*Point-in-time CI evidence. Not a push or workflow-dispatch authorization.*
