# Project Completion Baseline (Phase 0)

Recorded: 2026-11-24. This document is the Phase 0 forensic baseline for the
single-agent completion pass. It records what was inspected and what was true
at that moment; it is not a claim that the project is complete.

## 1. Repository identity

| Field | Value |
| --- | --- |
| Repository | `https://github.com/shajeduzzamanenan992008inc/OMEGA-GUARDIAN-X-Universal.git` |
| Branch | `main` |
| Baseline commit inspected | `dbf88740f59be351b4701e91495860113f736558` |
| Completion-pass commit | `8647892dd9d16f648303533596df6df74dac9dad` |
| Package version | `0.8.0a1` |
| Local branch present | `milestone-i-runtime-unification` (not merged, not deleted) |

At the start of the pass the worktree was **not clean**: 30 modified paths, 129
deleted paths and 3 untracked paths existed from earlier work. Those changes
were treated as user work: inspected, preserved, and committed rather than
discarded or reset.

## 2. Actual structure

```text
OMEGA-GUARDIAN-X-Universal/
├── README.md                     # root entry point
├── .github/workflows/            # authoritative CI (working-directory: GUARDIAN-X)
└── GUARDIAN-X/
    ├── VERSION                   # 0.8.0a1
    ├── pyproject.toml            # installable distribution
    ├── init.py                   # drop-in entry point
    ├── src/ogx/                  # canonical installable package
    ├── core/ogx/                 # hand-maintained stdlib-only drop-in package
    ├── tests/                    # package test suite (src layout)
    ├── _dev/tests/               # drop-in / architecture test suite
    ├── _dev/tools/               # sync check, drop-in builder, config validator
    ├── docs/                     # project documentation
    ├── guard/, devkit/, link/    # shield runtime components
    └── dist/, release/           # built artifacts (gitignored)
```

Two distributions exist on purpose:

- `src/ogx` is the canonical installable package (wheel, `ogx` console script).
- `core/ogx` is a hand-maintained, standard-library-only drop-in loaded by
  `init.py`; it is **not** generated from `src/ogx` because the trees have
  intentionally different layouts.

`_dev/tools/sync_core_from_src.py` was previously a destructive generator. It
is now a read-only consistency checker (`--check`) that verifies structure,
versions and packaging declarations.

## 3. Functionality present

CLI `ogx` exposes 46 top-level commands: `init`, `env`, `detect`, `scan`,
`start`, `engines`, `analyze`, `intelligence`, `android`, `integrate`,
`projects`, `registry`, `pap`, `identity`, `inventory`, `architecture`, `sbom`,
`agents`, `skills`, `diagnose`, `validate`, `debug`, `test-plan`, `impact`,
`fix-plan`, `policy`, `repo`, `platform`, `osv-audit`, `licenses`, `doctor`,
`compatibility`, `sandbox`, `guardian`, `discover`, `discover-root`, `activate`,
`host`, `provider`, `providers`, `remote`, `repository`, `workspace`,
`enterprise`, `governance`.

Provider engine: 28 descriptors, **7 verified** (GitHub, GitLab, Bitbucket,
Gitea, Forgejo, Azure DevOps Repos, AWS CodeCommit) and **21 descriptor-only**
(AWS, Azure, Azure Pipelines, crates.io, Docker workspace, GCP, GitHub Actions,
GitHub Codespaces, GitLab CI, Gitpod, Jenkins, Jira, Kubernetes workspace,
Linear, Maven Central, npm, NuGet, PyPI, remote SSH workspace, SourceHut,
Trello). No credentials were present in the environment
(`ogx platform status`: `Credentials present: 0`).

Host engine: `ogx hosts` is a compatibility facade; `ogx host list` reports
`No hosts registered.` Local adapters (`local_filesystem`, `local_git`) and a
GitHub shim exist, but no host has passed a host contract suite and no host
claim in this repository should be read as operational.

Project identity: `.guardian-x/identity.json` plus the SQLite Registry V2
(`OGX_HOME/registry/registry.db`). Repository links use a deterministic
namespace UUID derived from `provider_id` + normalized URL, so a URL is never
the project identity.

## 4. Baseline defects verified during the pass

| ID | Defect | Evidence |
| --- | --- | --- |
| B-01 | PAP schema modules were documentation-only after `dbf8874`, so `CURRENT_SCHEMA_VERSION`/validators were unimportable and 9 test modules failed to collect | `ImportError: cannot import name 'CURRENT_SCHEMA_VERSION'`, 9 collection errors |
| B-02 | A nested duplicate tree `core/ogx/core/ogx/` tracked 125 files | `git ls-files core/ogx/core` |
| B-03 | `ogx.discovery` imported undeclared third-party `tomli_w` | import error in a clean environment |
| B-04 | Provider transport read response bodies without a hard cap | unbounded `read()` |
| B-05 | Wheel license metadata was not SPDX and `LICENSE` was not declared | wheel `METADATA` |
| B-06 | Root workflows pointed at the repository root instead of `GUARDIAN-X` | workflow `run` steps |
| B-07 | Stale `0.7.1a1` wheel and results logs were tracked | `git ls-files` |
| B-08 | `sync_core_from_src.py` destroyed hand-maintained drop-in changes | tool source |

These were repaired before the completion-pass commit. Test evidence after
repair is in `docs/TEST_RESULTS.md` and `docs/FINAL_PROJECT_COMPLETION_REPORT.md`.

## 5. Known gaps at baseline (still open unless marked resolved)

- No hosted CI run exists for this repository, so no workflow result can be
  claimed. Workflows are configured and were YAML-parsed locally only.
- 21 providers are descriptor-only by design; none implements a domain
  operation.
- No live provider API test was possible (no credentials, no network fixtures
  approved for live accounts). Adapter behavior is covered by mocked/static
  tests only.
- No host contract suite exists; consequently no host adapter is declared
  operational.
- Only one automated relocation test exists (directory rename); drive-letter,
  USB, clone-to-other-directory and host-to-host migrations are UNVERIFIED.
- Enterprise SSO (OIDC/SAML/MFA), PostgreSQL, REST API, dashboard and
  distributed job execution are NOT IMPLEMENTED.
- Plugin signature verification, publisher trust and revocation are NOT
  IMPLEMENTED.
- The unified lifecycle orchestrator (resume/cancel/rollback across
  subsystems) is PARTIALLY IMPLEMENTED: the state enum and registry column
  exist, but no orchestrator engine drives them.
- SSRF endpoint validation exists, but DNS-rebinding/TOCTOU between validation
  and connection is a residual risk; connections are not pinned to a resolved
  address.
- Audit records are append-only JSONL with no tamper-evident chain and no
  immutable storage.
- Ruby is unavailable on the verification host, so `_dev/tools/validate_config.rb`
  could not be executed.
- Locally only Windows + CPython 3.15.2 was available; the declared support
  matrix is Python 3.11–3.13, and Linux/macOS were not executed locally.

## 6. Environment used for the baseline

| Item | Value |
| --- | --- |
| OS | Windows (`win32`) |
| Python | CPython 3.15.2 |
| Ruby | unavailable |
| Docker | not exercised |
| Network/provider credentials | none |

Python 3.15.2 is outside the declared classifier range (3.11–3.13). Passing
tests on 3.15.2 do not substitute for a run on a declared version.

## 7. Execution plan adopted

1. Repair the PAP import regression and any collection errors.
2. Remove the nested duplicate tree; rewrite the sync tool as a read-only check.
3. Fix undeclared dependencies and unbounded reads; add regression tests.
4. Correct packaging metadata and artifact hygiene.
5. Rewrite the root CI workflows for the real layout and matrix.
6. Re-verify architecture, packaging, CLI, drop-in and tests.
7. Document the real capability matrix, boundaries and residual risk.
8. Produce the completion tracker and final report with one release decision.

Steps 1–8 are recorded in `docs/PROJECT_COMPLETION_TRACKER.md`; executed
evidence is in `docs/FINAL_PROJECT_COMPLETION_REPORT.md`.
