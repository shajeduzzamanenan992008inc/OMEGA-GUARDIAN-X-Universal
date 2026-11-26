# Milestone I.5 — Baseline Report

Date: 2026-11-26
Repository root: `D:/FTP/OMEGA-GUARDIAN-X-Universal`
Branch: `main`
Status: baseline captured before any edit in this milestone

## 1. Environment

| Item | Observed |
|---|---|
| OS | Windows 10.0.19045 (AMD64) |
| Python | CPython 3.15.2 |
| pip | 26.3 |
| Git | 2.56.0.windows.4 |
| Shell | bash (Git Bash) |

Only CPython 3.15.2 was available. `pyproject.toml` declares
`requires-python = ">=3.11"` and classifiers for 3.11–3.13, so the declared
versions were **not** executed in this milestone.

## 2. Git baseline

All commands were run with cwd = repository root and exited 0.

| Command | Observed |
|---|---|
| `git rev-parse --show-toplevel` | `D:/FTP/OMEGA-GUARDIAN-X-Universal` |
| `git rev-parse --abbrev-ref HEAD` | `main` |
| `git rev-parse HEAD` | `a004348cd371fc1a42aefcaacf7e31b577026476` |
| `git rev-parse origin/main` | `a004348cd371fc1a42aefcaacf7e31b577026476` |
| `git rev-list --left-right --count HEAD...origin/main` | `0  0` |
| `git status --porcelain=v1` | empty |
| `git diff --stat` | empty |
| `git diff --cached --stat` | empty |
| `git ls-files --others --exclude-standard` | empty |

Tracked top-level tree: `.github/`, `.gitignore`, `GUARDIAN-X/`, `README.md`.
There is no repository-root `docs/` at this commit.

## 3. Commit topology and what each commit contains

```
a004348 chore: update repository files   <- HEAD = origin/main
06ffef4 chore: update repository files   <- packaging fix
757d6b6 chore: update repository files   <- Milestone I.4 baseline
d55cea3 docs(milestone-i1): ...
```

- **`06ffef4`** (`06ffef44f179068d0b9b016d77b51c34e6bdb121`), parent
  `757d6b6`: 31 files, +1172/−29. This is the commit that contains the I.4
  packaging fix. `git show 06ffef4 -- GUARDIAN-X/_dev/tools/make_dist.py`
  shows the exact change: `EXCLUDE_TOP` (first path component only) was
  replaced by `EXCLUDE_TOP_FILES = {"pyproject.toml"}` plus `EXCLUDE_DIRS`
  applied to every path component, with `*.egg-info` treated as build metadata.
  The same commit created the I.3 and I.4 reports at root `docs/` and moved the
  14 I.1/I.2 reports into `GUARDIAN-X/docs/`.
- **`a004348`** (`a004348cd371fc1a42aefcaacf7e31b577026476`), parent
  `06ffef4`: 8 files, 0 content changes — pure renames of the I.3/I.4 reports
  from root `docs/` to `GUARDIAN-X/docs/`.

Net result: 22 milestone reports now live in `GUARDIAN-X/docs/` (Milestone I,
I.1, I.2, I.3, I.4), and root `docs/` does not exist at HEAD.

## 4. Preservation of earlier user work

The I.4 session recorded uncommitted user changes: 14 deleted root reports, 14
untracked relocated copies, and 8 edited documentation/README files. Those
changes are no longer uncommitted — `06ffef4` and `a004348` committed them.

Because the baseline tree is clean, this milestone cannot lose an uncommitted
user change. No `reset`, `clean`, `checkout`, `restore`, `stash` or history
rewrite was performed.

## 5. Pre-edit archive baseline

Method:

```bash
git archive --format=zip -o <scratch>/head.zip HEAD
# extract, then run the committed generator from the export
python <export>/GUARDIAN-X/_dev/tools/make_dist.py <scratch>/clean-dropin-a.zip
python <export>/GUARDIAN-X/_dev/tools/make_dist.py <scratch>/clean-dropin-b.zip
# and from the working tree
python GUARDIAN-X/_dev/tools/make_dist.py <scratch>/baseline-dropin.zip
```

| Build source | Entries | SHA-256 | Forbidden hits | Required entries |
|---|---|---|---|---|
| Clean `git archive HEAD` (`a004348`) | 326 | `21ebccb43d330264892bfdf9bc1f0abecb566057fa2c91c70850a8125e9ba7ee` | 0 | present |
| Working tree | 326 | `6b8635c01a5609d1bd28731b556528d84b5566d2a3701228fbccfdea84fca29a` | 0 | present |

- The entry lists are identical (`namelist()` equality): the clean export and
  the working tree ship the same content at baseline.
- Two consecutive builds of the same clean export were byte-identical.
- The SHA-256 difference between the two sources comes from extraction mtimes
  recorded in ZIP headers, not from content.
- `omega-shield/docs/` contains 98 entries, including the 22 milestone reports.
- The nested `.github` entry found in Milestone I.3 is absent.
- ZIP bytes embed extraction mtimes, so the SHA-256 is run-specific. A second
  clean export of the same commit produced
  `403f683dfff25022fd1ab5c79bde3a3dbb04d4b9038da703484c737a362f22c9` with the
  identical 326-entry list.

## 6. Existing packaging coverage before this milestone

| Check | Location | Exercises `make_dist.py`? |
|---|---|---|
| Architecture/distribution tests (17) | `GUARDIAN-X/_dev/tests/test_architecture.py` | No |
| Archive forbidden/required assertion | `.github/workflows/ci.yml`, `drop-in` job | Only in hosted CI |
| Drop-in suite execution | `.github/workflows/ci.yml`, `tests` job | No archive test collected |
| Local drop-in suite (245 tests at baseline) | `GUARDIAN-X/_dev/tests` | No |

The inline CI assertion was the only archive regression check, and it runs only
in hosted CI. No local test built or inspected the archive.

## 7. Scope decisions

1. The four I.5 reports are created under the repository-root `docs/`, as the
   directive specifies. The three I.4 reports are updated in place under
   `GUARDIAN-X/docs/`, where `a004348` put them; no duplicate copies are made.
2. `make_dist.py` is refactored only enough to expose
   `build_archive(root, out)`. The CLI, exclusion policy, prefix and entry order
   are unchanged; byte-identical output is proven in the regression report.
3. No workflow, CI assertion, required-file list, `src/`, `core/`,
   `pyproject.toml` or LICENSE change is made.
4. The nested mirror is audited but not deleted: removing a tracked file is
   outside this pass's authorization.

## 8. Authorization boundary

No commit, push, PR, merge, release, reset, clean, destructive checkout, stash,
or GitHub account/repository setting change was performed. `LICENSE` was not
restored or modified.
