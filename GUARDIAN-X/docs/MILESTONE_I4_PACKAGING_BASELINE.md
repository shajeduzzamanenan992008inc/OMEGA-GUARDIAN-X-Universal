# Milestone I.4 — Packaging Baseline

> **Milestone I.5 correction (2026-11-26).** This report was written against
> `757d6b6` while the generator fix was still uncommitted. The fix landed in
> `06ffef4` (`06ffef44f179068d0b9b016d77b51c34e6bdb121`); the current HEAD and
> `origin/main` are `a004348` (`a004348cd371fc1a42aefcaacf7e31b577026476`).
> Commit `a004348` then relocated the Milestone I.3/I.4 reports from the
> repository-root `docs/` into this directory with no content change. The
> pre-fix measurements below are preserved as the dated record of the I.4 pass;
> current measurements are in `../../docs/MILESTONE_I5_BASELINE_REPORT.md`.

Date: 2026-11-26
Repository root: `D:/FTP/OMEGA-GUARDIAN-X-Universal`
Branch: `main`
HEAD: `757d6b6c519a30487a3bd46db30011ece30b1bdb`
Remote `origin/main`: `757d6b6c519a30487a3bd46db30011ece30b1bdb` (re-read with `git ls-remote`, no fetch)

## 1. Baseline reconfirmation

| Item | Observed |
|---|---|
| Branch | `main` |
| Local HEAD / `main` / `origin/main` | all `757d6b6c519a30487a3bd46db30011ece30b1bdb` |
| Ahead / behind | 0 / 0 |
| Python | CPython 3.15.2 (Windows) |
| Preserved user changes | 14 deleted root `docs/MILESTONE_I*` files, 14 untracked copies in `GUARDIAN-X/docs/`, 8 modified documentation/README files |
| Milestone I.3 reports | 5 untracked files under root `docs/` |

No reset, checkout, fetch, or cleanup was performed. The working tree was used exactly as found.

## 2. Drop-in generator under inspection

`GUARDIAN-X/_dev/tools/make_dist.py` walks `ROOT.rglob("*")` where `ROOT` is `GUARDIAN-X/`, and writes every non-skipped file under the archive prefix `omega-shield/`.

Before this milestone the exclusion logic was:

```python
EXCLUDE_TOP = {
    "_dev", ".github", "pyproject.toml", ".git", "dist", "build", "release",
    "tests", "src", ".pytest_cache",
}
...
if path.is_dir() or rel.parts[0] in EXCLUDE_TOP:
    continue
```

`rel.parts[0]` is the **first path component only**. A directory named `.github` was therefore excluded at `GUARDIAN-X/.github/`, but not at any deeper level.

## 3. Root cause of the nested `.github/` entry

The repository tracks one stray nested mirror:

```
GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md
```

`git ls-files GUARDIAN-X/GUARDIAN-X` returns exactly that one file. It is a committed duplicate of release notes, not a runtime or documentation input for the drop-in.

For that file, `rel.parts` is `("GUARDIAN-X", ".github", "RELEASE_NOTES.md")`:

- `rel.parts[0]` is `"GUARDIAN-X"`, which is **not** in `EXCLUDE_TOP`;
- the `.github` component is never inspected;
- the file is written to `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md`.

The inclusion is therefore caused by a broad recursive copy combined with a top-level-only exclusion rule. It is **not** an explicit inclusion, and it is not caused by the root `.github/` directory (which is correctly excluded).

## 4. Pre-fix archive measurements

Both archives were built with the unmodified generator.

| Source | Files | SHA-256 (observed run) |
|---|---|---|
| Current working tree (`GUARDIAN-X/`) | 324 | `2808da3b0240a328d11866bee39ce127ef5c2130142ef8cb5081d3b12141d2bb` |
| Clean `git archive HEAD` export | 305 | `96fef0ac1e061f5a97e11d4ebf79d445b17a1f27b9b8d44e1c2f828e6742324b` |

Zip hashes vary between runs because the standard library records source file mtimes; the entry lists are stable and sorted.

Forbidden-token scan of both archives (the CI tokens `"/tests/"`, `"/_dev/"`, `"/.github/"`, `"/release/"`, `"/src/"`, `"__pycache__"`, `"/results.txt"`):

| Token | Working tree | Clean HEAD |
|---|---|---|
| `/.github/` | **1** — `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md` | **1** — same |
| `/tests/` | 0 | 0 |
| `/_dev/` | 0 | 0 |
| `/src/` | 0 | 0 |
| `/release/` | 0 | 0 |
| `__pycache__` | 0 | 0 |
| `/results.txt` | 0 | 0 |

The clean export contains 305 files, matching the Milestone I.3 baseline exactly.

## 5. Working-tree vs clean-export difference

`only in HEAD`: none.
`only in WORK`: 19 files —

- 14 untracked relocated Milestone I/I.1/I.2 reports under `GUARDIAN-X/docs/` (the user's preserved changes);
- 5 files under `GUARDIAN-X/core/omega_guardian_x.egg-info/` (`PKG-INFO`, `SOURCES.txt`, `dependency_links.txt`, `entry_points.txt`, `top_level.txt`).

The `egg-info` directory is ignored build metadata left by an in-tree wheel build. It is not part of the clean HEAD export, but the current generator would ship it from a working tree. That is a second, smaller hygiene defect.

## 6. Required drop-in content

The clean-export archive contains the complete distribution contract:

| Required item | Present |
|---|---|
| `omega-shield/init.py` | yes |
| `omega-shield/core/ogx/__init__.py` | yes |
| `omega-shield/VERSION` | yes |
| `omega-shield/shield.toml` | yes |
| `omega-shield/core/` | 117 entries |
| `omega-shield/docs/` | 76 entries |
| `guard/`, `devkit/`, `link/`, `examples/`, `schemas/`, `scripts/` | yes |

Top-level archive roots in the clean export: `.cursor`, `.env.example`, `.gitignore`, `AGENTS.md`, `CHANGELOG.md`, `CLAUDE.md`, `CODEX.md`, `CONTRIBUTING.md`, `CURSOR.md`, `GEMINI.md`, `GUARDIAN-X` (the stray nested mirror), `OGX-*.ps1`, `README*`, `SECURITY.md`, `VERSION`, `WINDSURF.md`, `core`, `devkit`, `docs`, `examples`, `guard`, `init.py`, `link`, `requirements-dev.txt`, `schemas`, `scripts`, `shield.toml`.

Observations outside the known defect:

- `.cursor/rules/ogx.mdc` ships. It is tracked editor guidance, not a cache or temporary file, and is left unchanged in this milestone.
- `core/omega_guardian_x.egg-info/` ships only from a dirty working tree. It should be excluded by the generator.
- No `__pycache__`, `.pytest_cache`, `.git`, `.env`, database, wheel, or zip artifacts are present in the clean export.

## 7. CI assertion assessment

The drop-in job in `.github/workflows/ci.yml` asserts:

```python
forbidden = ("/tests/", "/_dev/", "/.github/", "/release/", "/src/", "__pycache__", "/results.txt")
bad = [name for name in names if any(token in name for token in forbidden)]
```

This is the correct expression of the intended policy: those path components must not appear **anywhere** in the archive. The assertion is not overly strict, and the nested path genuinely violates it. The generator, not the assertion, is wrong.

The wheel job uses a separate assertion over wheel names; it is unaffected by this defect.

## 8. Existing packaging tests

- `_dev/tests/test_architecture.py` verifies that the drop-in tree exists, is non-trivial, and does not duplicate the provider engine. It does not exercise `make_dist.py`.
- No test in `_dev/tests` or `tests/` builds or inspects the drop-in archive.
- The effective archive assertion is the inline Python check in `ci.yml`, which has not executed on hosted runners for this HEAD because of the account billing restriction.

## 9. Fix decision

The smallest maintainable correction is to apply the existing exclusion policy to **every path component**, not only `rel.parts[0]`, and to treat `*.egg-info` directories as build metadata. The CI assertion, required-file list, archive prefix, and all runtime content stay unchanged.
