# Milestone I.3 — Packaging Validation Report

Date: 2026-11-26
Commit: `757d6b6c519a30487a3bd46db30011ece30b1bdb`
Environment: Windows, CPython 3.15.2, setuptools bundled with that interpreter

## 1. Configuration reviewed

`GUARDIAN-X/pyproject.toml`:

| Setting | Value |
| --- | --- |
| Build backend | `setuptools.build_meta` |
| Build requirement | `setuptools>=77` |
| Name / version | `omega-guardian-x`, dynamic from `ogx.__version__` = `0.8.0a1` |
| Requires-Python | `>=3.11` |
| Dependencies | none |
| Package directory | `src` with automatic discovery |
| Console script | `ogx = "ogx.cli:main"` |
| License expression | `LicenseRef-OMEGA-GUARDIAN-X-Proprietary-Alpha` |
| License files | `license-files = ["LICENSE"]` |

`GUARDIAN-X/LICENSE` does not exist at this commit. The declaration was left
untouched, as instructed.

## 2. Wheel build

Command:

```bash
cd GUARDIAN-X
python -m pip wheel . --no-deps --no-build-isolation -w .i3tmp/wheel
```

Result: **exit 0**, one wheel produced.

Observed warning (repeated by setuptools):

```text
BuildBackendWarning: Cannot find any files for the given pattern.
Pattern 'LICENSE' did not match any files.
```

| Property | Observed |
| --- | --- |
| Wheel | `omega_guardian_x-0.8.0a1-py3-none-any.whl` |
| Entries | 167 |
| Size | 402,313 bytes |
| SHA-256 | `cc9976b0ea627ff68f4a85195cdd536a620224fc039625c0f6a66dd5cd46757d` |
| `Name` | `omega-guardian-x` |
| `Version` | `0.8.0a1` |
| `Requires-Python` | `>=3.11` |
| `License-Expression` | `LicenseRef-OMEGA-GUARDIAN-X-Proprietary-Alpha` |
| License file in wheel | **absent** |
| Forbidden paths (`tests/`, `_dev/`, `.github/`, `core/`, `src/`, caches) | 0 |
| `ogx/cli.py`, `ogx/__init__.py` | present |
| Console entry points | `...dist-info/entry_points.txt` present |

The wheel build succeeds. The missing LICENSE produces a warning and an absent
license file, not a build failure.

## 3. Clean virtual-environment install

```bash
python -m venv .i3tmp/venv
.i3tmp/venv/Scripts/python.exe -m pip install --no-index --no-deps .i3tmp/wheel/*.whl
```

| Check | Result |
| --- | --- |
| Install | exit 0, `Successfully installed omega-guardian-x-0.8.0a1` |
| `import ogx` | `0.8.0a1` |
| `ogx --version` | `ogx 0.8.0a1` |
| `ogx providers list` | exit 0 |
| `ogx platform status` | exit 0 |
| `ogx host list` | `No hosts registered.`, exit 0 |

## 4. Drop-in archive

Builder: `GUARDIAN-X/_dev/tools/make_dist.py`. It excludes top-level `_dev`,
`.github`, `src`, `tests`, `build`, `release`, `dist`, caches and common secret
and binary suffixes, but it does not exclude nested directories by name.

### 4.1 Working-tree build

| Check | Result |
| --- | --- |
| Build | exit 0, 324 files |
| Required `omega-shield/init.py` | present |
| Required `omega-shield/core/ogx/__init__.py` | present |
| Forbidden-path assertion (the CI rule) | **FAIL** |
| First forbidden hit | `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md` |
| `init.py status` from the extracted tree | exit 0; state created outside the host tree |

The 324-file count includes the 14 uncommitted milestone reports that the user
moved into `GUARDIAN-X/docs/`. Those are working-tree content and would not be
present in a clean CI checkout.

### 4.2 Clean `757d6b6` checkout

A `git archive 757d6b6` export was built with the same tool:

| Check | Result |
| --- | --- |
| Build | exit 0, 305 files |
| Required entries | present |
| Forbidden-path assertion | **FAIL** |
| Forbidden hit | `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md` |

The nested path is tracked at HEAD (`GUARDIAN-X/GUARDIAN-X/.github/RELEASE_NOTES.md`).
`make_dist.py` excludes only a top-level `.github`, so the nested `.github`
directory is copied into the archive. The CI drop-in job rejects any archive
member containing `/.github/`, so that job would fail on its own assertion even
after the billing blocker is removed.

## 5. Missing LICENSE — factual consequence

- `GUARDIAN-X/LICENSE` is absent. Commit `757d6b6` deleted 491 lines from it.
- The wheel build emits `Pattern 'LICENSE' did not match any files.`
- No license file is present in the wheel.
- The `License-Expression` metadata is still emitted.
- The wheel, clean install and CLI smoke tests all succeed.

**Packaging success is separate from license compliance.** A wheel that builds
and installs does not establish that the project's licensing obligations are
met, and nothing in this milestone restores, recreates or substitutes the
LICENSE file or changes license metadata.

## 6. Not performed

- No sdist build.
- No signing, provenance attestation or reproducibility claim.
- No upload, publication or release.
- No LICENSE restoration or modification.

## 7. Packaging conclusion

The wheel is locally buildable and installable, with no development-file
leakage and working console entry points. It is **not** license-complete and is
**not** release-ready on that basis. The drop-in archive is runnable but fails
the repository's own forbidden-path rule because of the tracked nested
`GUARDIAN-X/.github` file. Neither artifact was published.
