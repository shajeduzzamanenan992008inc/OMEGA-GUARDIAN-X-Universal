# OMEGA GUARDIAN X — Universal Guardian Engine (0.5.0a1)

## Purpose

The Guardian engine is an external, local-first maintenance controller for arbitrary repository roots. It composes the existing project detector, bounded scanner, architecture mapper, inventory, compatibility report, Git connector and external report store. Unknown stacks still receive generic inventory and static checks; language-specific semantic coverage varies.

## Commands

```powershell
# One consolidated project health, security, architecture and maintenance report
ogx guardian audit "D:\Repos\MyProject"

# Prioritized maintenance report (same analysis, no project writes)
ogx guardian maintain "D:\Repos\MyProject"

# Preview the deterministic low-risk repairs that OGX knows how to apply
ogx guardian repair-plan "D:\Repos\MyProject"

# Apply allowlisted repairs only after reviewing the preview
ogx guardian repair "D:\Repos\MyProject" --confirm

# Check configured Git remotes without network access
ogx guardian upgrade-check "D:\Repos\MyProject"

# Opt in to a read-only remote branch-head comparison
ogx guardian upgrade-check "D:\Repos\MyProject" --online

# Fetch remote-tracking refs only; no merge/rebase/checkout/pull
ogx guardian upgrade-fetch "D:\Repos\MyProject" --remote origin --confirm

# Audit OGX itself and produce a self-evolution proposal
ogx guardian evolve .
```

Reports are written under OGX's external state directory, not inside the scanned project. The registered project shortcut works too, for example `ogx guardian audit --project store-api`.

## Maintenance scope

- Combines technology detection, static findings, bounded inventory, partial import graph, toolchain compatibility, Git state, test/CI presence and dependency automation coverage.
- Produces prioritized actions for confirmed high-severity findings, missing tests/CI, dependency automation, reproducible dependency declarations, licensing and version-control readiness.
- Detects current upstream branch head via Git's `ls-remote` only when `--online` is supplied.
- A confirmed fetch updates Git remote-tracking metadata only. It does not merge, rebase, checkout, reset, install dependencies, or alter the working tree intentionally.
- Provides a single safe autofix today: add `.env` / `.env.*` ignore patterns when local environment files are present, not already tracked by Git, and `.gitignore` is not a symlink. It preserves existing content and stores a before-image outside the project. Tracked environment files are blocked because `.gitignore` does not remove a secret from Git history.
- The engine audits its own source repository with `ogx guardian evolve`, but it does not rewrite its own source or replace itself in-place.

## Cross-platform dependency updates

`ogx integrate plan PATH` previews root-level governance and dependency automation files. `ogx integrate apply PATH --confirm` creates only missing files and preserves all existing user files. The integration adds:

- `.github/dependabot.yml` — technology-aware update declarations for GitHub Dependabot.
- `renovate.json` — platform-neutral Renovate configuration with a dependency dashboard, lockfile maintenance, vulnerability alert handling where supported, bounded PR concurrency, and major-version updates kept non-automerge.

The configuration is not the hosted bot itself. For Renovate, install the hosted app or run a regularly scheduled self-hosted Renovate instance with least-privilege repository access. Renovate documents platform support for GitHub, GitLab, Bitbucket, Azure DevOps, Gitea and Forgejo (some platforms/features are experimental or differ); Dependabot is not a universal Git-hosting solution. See the [Renovate platform matrix](https://docs.renovatebot.com/modules/platform/) and [installation guide](https://docs.renovatebot.com/getting-started/installing-onboarding/).

OGX's Git operations work with standard Git remotes, including generic/self-hosted Git hosts. Its native repository metadata / draft PR adapter is narrower and currently targets GitHub, GitLab, Bitbucket, Gitea and Forgejo. A remote being fetchable does not imply OGX has a provider API to install bots, set branch protection or open every possible PR/MR type.

## Safety and non-goals

- Normal Guardian audits are read-only with respect to the target project; report and backup data is external.
- The repair engine is allowlisted and intentionally narrow. It does not invent arbitrary source-code patches.
- Project test/build scripts are not automatically executed. Use `ogx sandbox plan` and `ogx sandbox run PATH --image REPOSITORY@sha256:DIGEST --confirm -- COMMAND ...` after reviewing the command. OGX requires a locally available immutable image digest and has no unsandboxed fallback.
- Dependency upgrade bots propose reviewable PRs/MRs. Do not enable automatic merge until the host enforces passing required checks and an authorized reviewer approves the policy.
- Fetching an upstream branch is not equivalent to upgrading a project. A human or separately approved workflow must inspect ancestry, resolve conflicts, update lockfiles, run regression tests and approve the final diff.
- Static findings are evidence to investigate, not proof of exploitability or root cause. The engine does not claim universal SAST, DAST, runtime debugging, compliance certification or enterprise SaaS readiness.

## Verification checklist

1. `python -m compileall -q core _dev/tests`
2. `PYTHONPATH=core python -m unittest discover -s _dev/tests -v`
3. `ogx guardian audit <fixture-project>` and review both external reports.
4. `ogx guardian repair-plan <fixture-project>` then verify preview makes no changes.
5. Run one confirmed repair on a disposable fixture; verify the external before-image and rescan.
6. Test remote checks/fetch against a local bare Git fixture before using hosted repositories.
7. Review all diffs and ensure existing project files were preserved.
