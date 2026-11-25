# Milestone I.3 — Hosted CI Evidence Report

Date: 2026-11-26
Commit under audit: `757d6b6c519a30487a3bd46db30011ece30b1bdb`
Repository: `shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal`

## 1. Access method

The repository is not readable anonymously: unauthenticated `github.com`
returned HTTP 404 and the unauthenticated REST API returned a rate-limit
response. An authenticated read-only query was made with the credential already
stored in the local Git credential helper, using only:

```text
GET /repos/{owner}/{repo}/actions/runs?branch=main&per_page=100
GET /repos/{owner}/{repo}/actions/runs/{run_id}
GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs
GET /repos/{owner}/{repo}/actions/jobs/{job_id}
GET /repos/{owner}/{repo}/check-runs/{check_run_id}/annotations
GET /repos/{owner}/{repo}/actions/permissions
```

No credential value was printed or written to any report. No workflow was
triggered, no push, PR, merge or settings change was made.

This is an authoritative Actions run listing, not a pull-request-only view.

## 2. Repository run inventory

`total_count` for `main`: **25** runs.

Runs for `757d6b6`: **3**, all `push`, all `completed`, all `failure`:

| Run ID | Workflow | Event | Conclusion | URL |
| --- | --- | --- | --- | --- |
| 45670893677 | OGX CI | push | failure | https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal/actions/runs/45670893677 |
| 45670893573 | GitHub Configuration Validation | push | failure | https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal/actions/runs/45670893573 |
| 45670893599 | OGX Code Security | push | failure | https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal/actions/runs/45670893599 |

All three were created at `2026-11-25T18:01:46Z` and updated by
`2026-11-25T18:01:50Z`.

## 3. Job results

Every job below has **0 steps** and **no runner**. All started at
`2026-11-25T18:01:47Z` and completed at `2026-11-25T18:01:49Z`.

| Workflow | Job | Job ID | Status | Conclusion | Steps |
| --- | --- | --- | --- | --- | --- |
| OGX CI | Tests (Python 3.11) | 139405532420 | completed | failure | 0 |
| OGX CI | Tests (Python 3.12) | 139405533026 | completed | failure | 0 |
| OGX CI | Tests (Python 3.13) | 139405532993 | completed | failure | 0 |
| OGX CI | Profile-configured project checks | 139405533243 | completed | failure | 0 |
| OGX CI | Wheel build and clean install | 139405533023 | completed | failure | 0 |
| OGX CI | Drop-in distribution | 139405532933 | completed | failure | 0 |
| GitHub Configuration Validation | Validate YAML, JSON, Copilot frontmatter, and workflow safety | 139405533286 | completed | failure | 0 |
| OGX Code Security | CodeQL Python analysis | 139405531274 | completed | failure | 0 |

No matrix leg, packaging job, drop-in job, profile check, configuration
validation or CodeQL analysis executed.

## 4. Root cause

The check-run annotation for every inspected job is the same:

> The job was not started because recent account payments have failed or your
> spending limit needs to be increased. Please check the 'Billing & plans'
> section in your settings

This is an account billing/spending-limit refusal, not a test failure, workflow
syntax error, action-resolution failure or code defect.

Supporting observations:

- All four pinned action SHAs resolve to real upstream commits:
  `actions/checkout@11d5960…` (2026-07-16), `actions/setup-python@a26af69…`
  (2025-04-24), `github/codeql-action@d2aacf54…` (2026-11-20),
  `actions/dependency-review-action@2031cfc…` (2026-03-03).
- Repository Actions permissions: `enabled: true`, `allowed_actions: "all"`,
  `sha_pinning_required: false`; default workflow token permission `read`,
  `can_approve_pull_request_reviews: false`.
- The last successful run was `42460429496` on 2026-11-08 at `749acc4`:
  job "Profile-driven project checks" succeeded with **5 steps** and a real
  runner. Actions has worked in this repository before the billing refusal.
- The same zero-step pattern appears on the earlier `e7e7686` run
  `45428212796` (six jobs, 0 steps each), so the refusal predates `757d6b6`.

## 5. Classification

| Target | Classification | Basis |
| --- | --- | --- |
| OGX CI (run 45670893677) | **FAIL (not executed)** | Run concluded failure; all 6 jobs refused at startup |
| GitHub Configuration Validation (45670893573) | **FAIL (not executed)** | Run concluded failure; job refused at startup |
| OGX Code Security (45670893599) | **FAIL (not executed)** | Run concluded failure; job refused at startup |
| Python 3.11 / 3.12 / 3.13 matrix | **NOT_RUN** | 0 steps, no runner |
| Packaging job | **NOT_RUN** | 0 steps, no runner |
| Drop-in job | **NOT_RUN** | 0 steps, no runner |
| Profile checks | **NOT_RUN** | 0 steps, no runner |
| Hosted CI overall | **BLOCKED** | Account billing/spending limit |

No workflow may be reported as passing. The observed conclusion is failure, but
it carries no information about test or packaging behavior because no step ran.

## 6. Workflow integrity review (repository-root `.github/workflows/`)

The repository-root workflow directory is authoritative. The nested
`GUARDIAN-X/.github/workflows/` copies are inert kit content and were not used
for this review.

| Check | Observation |
| --- | --- |
| Top-level permissions | `contents: read` in `ci.yml`, `config-validation.yml`, `security.yml`; `contents: read` + `pull-requests: read` in `dependency-review.yml` |
| Elevated permission | Only CodeQL `security-events: write`, scoped to the CodeQL job |
| Checkout credential persistence | `persist-credentials: false` on every checkout step in all four workflows |
| Action pinning | Full 40-hex commit SHAs for checkout, setup-python, CodeQL init/autobuild/analyze and dependency-review; all four SHAs resolve upstream |
| PR security boundary | No `pull_request_target`; dependency review runs only on `pull_request`; no secret use in PR-triggered jobs |
| Python matrix | `["3.11", "3.12", "3.13"]`, `fail-fast: false` |
| Test collection assertion | Drop-in step parses `Ran (\d+) test` and exits non-zero on zero or missing count |
| Packaging assertions | Exactly one wheel; forbidden paths; `ogx` package and console entry points; clean-venv install and CLI smoke |
| Drop-in assertions | Forbidden paths, required entries, zip build |
| Working directory | `working-directory: GUARDIAN-X` on every run block |
| Path filters | `config-validation.yml` triggers on root workflows, nested `.github`, devkit agents/health, link templates, `_dev/tools`, `SECURITY.md` |
| Concurrency | Per-workflow/ref groups with `cancel-in-progress: true` |

### 6.1 Drop-in assertion would fail even without the billing blocker

`make_dist.py` on a clean `757d6b6` export produces 305 files including:

```text
omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md
```

The CI forbidden list contains `/.github/`, so the assertion in
`.github/workflows/ci.yml` would exit non-zero. This is a repository defect
independent of the hosted billing refusal and was not fixed in this milestone
(workflow and builder changes are out of scope).

## 7. Recommendation to obtain the first executed hosted run

1. Resolve the GitHub account billing/spending limit (repository owner action;
   not performed here).
2. Fix or exclude the nested `GUARDIAN-X/GUARDIAN-X/.github/` path so the
   drop-in forbidden-path assertion can pass.
3. With explicit authorization, re-run via `workflow_dispatch` or a new push,
   then record the run IDs and per-job conclusions.
4. Do not mark CI passed until at least one job executes its steps.

## 8. Conclusion

Hosted CI evidence now exists, but it is evidence of a **billing block**, not of
test or packaging behavior. The three runs for `757d6b6` are recorded with
their URLs and job outcomes. Hosted CI is `BLOCKED`, every job is `NOT_RUN`, and
no hosted success is claimed.
