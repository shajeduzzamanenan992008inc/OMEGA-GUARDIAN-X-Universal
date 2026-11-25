# OGX 0.8.0a1 validation results

These are the results actually observed for the current working tree. They are
not a claim about hosted CI or about platforms that were not exercised.

## Environment

- OS: Windows (Git Bash)
- Python: CPython 3.15.2
- Package under test: `omega-guardian-x` 0.8.0a1
- Canonical source: `src/ogx`; drop-in: `core/ogx`

Re-verified: 2026-11-25 at HEAD `d55cea3` (its parent `e7e7686` is the remote tip and `d55cea3` changes documentation only, so the code under test is identical to `e7e7686`).

## Commands and results

| Check | Command (from `GUARDIAN-X`) | Result |
|---|---|---|
| Compile | `python -m compileall -q src core tests _dev/tests` | passed |
| Canonical tests | `python -m pytest -q` | **465 passed, 15 skipped** at HEAD `e7e7686` (earlier baseline recorded 340 passed, 11 skipped) |
| Drop-in + architecture tests | `PYTHONPATH=core python -m unittest discover -s _dev/tests` | **245 tests, OK (9 skipped)** |
| Version/structure | `python _dev/tools/sync_core_from_src.py --check` | passed |
| Wheel build | `python -m pip wheel . --no-deps -w release` | passed |
| Clean install | wheel installed into a fresh venv | passed |
| CLI | `ogx --version`, `--help`, `providers list`, `platform status` | passed |
| Drop-in | `PYTHONPATH=core python -m ogx --version`; `python init.py status` | passed |
| Drop-in zip | `python _dev/tools/make_dist.py` | 279 files; no `src/`, tests, release, CI, env or database files |
| Secrets | tracked files scanned for token/private-key shapes | only `.env.example` (placeholders); no real token hits outside tests/docs |

## Skips

All 24 skips (15 canonical, 9 drop-in) are platform gates, not suppressed
failures:

- POSIX file-mode checks (`chmod 600/644`) on Windows — 2 in the canonical suite.
- Symlink creation (directory/file) unavailable on Windows — 12 in the
  canonical suite and 9 in the drop-in suite.
- Windows drive-relative path rejection is covered by a separate test —
  1 canonical skip.

They execute on the Linux CI matrix.

## Provider registry

28 descriptors:

- **Verified (7):** GitHub, GitLab, Bitbucket, Gitea, Forgejo, Azure DevOps
  Repos, AWS CodeCommit. Supported capability counts range from 12 to 19
  (identify, auth discovery, health, rate limit where implemented, pagination,
  repository/branch/commit/file/tree reads, PR list/create, and issue
  list/create/update where implemented).
- **Descriptor-only (21):** AWS, Azure, Azure Pipelines, crates.io, Docker
  workspace, GCP, GitHub Actions, GitHub Codespaces, GitLab CI, Gitpod,
  Jenkins, Jira, Kubernetes workspace, Linear, Maven Central, npm, NuGet, PyPI,
  remote SSH workspace, SourceHut, Trello. These advertise only identify,
  auth-discovery and API-version metadata.

Writes are limited to approval-gated pull/merge request and (where implemented)
issue creation; delete, merge, push to protected branches, permission changes
and deployment are not implemented. Host adapters exist (`local_filesystem`,
`local_git`, GitHub compatibility shim); the host registry starts empty on a
fresh state directory, and the independent host contract suite
`tests/test_host_contract.py` passes 12 tests.

The local hosts are **read-only**. The session/health/read/disconnect/recovery
lifecycle is implemented for them (Milestone H: `src/ogx/hosts/lifecycle.py`
plus the filesystem and Git runtime adapters), but no host supports writes,
execution, remote operations or branch operations, and none is declared
operational in that sense.

## Not verified

- Hosted GitHub Actions execution. Runs exist for `757d6b6`, but every job was
  refused before starting because of account billing/spending limits; no test,
  packaging or drop-in job executed. See
  `../../docs/MILESTONE_I3_CI_EVIDENCE_REPORT.md`.
- Python 3.11, 3.12 and 3.13 locally; Linux locally.
- Ruby-based `_dev/tools/validate_config.rb` (Ruby is not installed here).
- Real Brevo/SMTP inbox delivery (mocked transports only).
- Any real provider API call or write (all provider tests use fake transports).
