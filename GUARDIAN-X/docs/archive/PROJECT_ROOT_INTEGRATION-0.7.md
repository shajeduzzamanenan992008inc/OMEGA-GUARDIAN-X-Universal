> **Superseded in 0.8.0a1** (0.7 project-root integration (scattering installer)). Kept for history; see `docs/ARCHITECTURE.md` and `docs/INTEGRATION.md`.

# Project-root integration

OGX has two modes:

1. **External scan (default):** reads the target repository and stores reports in external user state. No target files are created.
2. **Explicit project-control integration:** preview the exact file plan, then approve adding a separate `omega-shield/` folder and missing AI/Dependabot files in the project root. The OGX runtime remains installed outside the target.

## Commands

```powershell
ogx integrate plan PATH
ogx integrate apply PATH --confirm
ogx integrate status PATH
ogx integrate remove PATH --confirm
```

## Non-destructive contract

- Existing files are never overwritten, appended to, or deleted during integration.
- New files are created exclusively. Conflicts are preserved and reported.
- An existing unmanaged `omega-shield/` folder causes a refusal.
- Symlinked destination paths are rejected.
- SHA-256 hashes are recorded for files created by OGX.
- Removal deletes only listed files whose current hash still matches. User-modified files are preserved. Directories are removed only if empty.
- Integration does not execute source code, package scripts, tests, plugins, or generated code.

## Scope and limits

The folder and `.github` files provide repository-wide guidance for supported AI agents, project policy metadata, custom agent profiles, Dependabot configuration, and a helper script. They do not grant OS permissions, enforce branch protection, guarantee that AI agents obey instructions, or silently authorize edits. Retain normal repository permissions, code review, CI, and branch protection.

## Sequential integration of multiple repositories

Use `scripts/integrate-many.ps1 -RepositoryPaths @(...) -Apply`. It processes one repository at a time, displays each plan, and requires the operator to type `APPLY` for each repository individually. Without `-Apply`, it only prints plans.
