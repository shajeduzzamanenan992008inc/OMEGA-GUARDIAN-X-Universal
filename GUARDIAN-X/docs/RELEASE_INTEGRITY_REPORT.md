# Release Integrity Report (Phase 8)

Date: 2026-11-24
Artifacts built from commit `c441f01` + uncommitted hardening changes.

## 1. Version consistency

`VERSION`, `src/ogx/__init__.py` and `core/ogx/__init__.py` all report
`0.8.0a1`; `sync_core_from_src.py --check` passes.

## 2. Wheel

Command:

```bash
python -m pip wheel . --no-deps --no-build-isolation -w <dir>
```

| Property | Value |
| --- | --- |
| File | `omega_guardian_x-0.8.0a1-py3-none-any.whl` |
| Size | 394,833 bytes |
| Entries | 164 |
| SHA-256 | `4b046535e7ec8057d0a06086258579dc622947c29521a278ce6bd06c9172d3bf` |
| Forbidden paths (`tests/`, `src/`, `_dev/`, `.github/`, `release/`) | 0 |
| `ogx/platform/audit.py` included | yes |
| Metadata | `omega-guardian-x`, `0.8.0a1`, `Requires-Python: >=3.11`, `LicenseRef-OMEGA-GUARDIAN-X-Proprietary-Alpha` |
| `LICENSE` bundled | yes |
| Runtime dependencies | none |

Fresh venv (no index, no dependencies):
`import ogx` → `0.8.0a1`; `ogx --version` → `ogx 0.8.0a1`;
`ogx platform audit` exit 0; `ogx providers list` exit 0.

## 3. Drop-in archive

`python _dev/tools/make_dist.py` → **290 files**.

- `omega-shield/init.py` present.
- `omega-shield/core/ogx/__init__.py` present.
- Forbidden paths (`src/`, `tests/`, `_dev/`, `.github/`, `release/`, temp
  directories): 0.
- Extracted `python init.py status` exit 0; state created outside the host tree.

The count rose from 280 to 290 because this pass added documentation files.

## 4. Source distribution

Not produced in this pass. `python -m build` is unusable here (local `build/`
shadowing); an sdist would need `python -m build --sdist` from a clean
environment or `pip`-equivalent tooling. This remains a release task.

## 5. Checksums and signing

- SHA-256 was computed locally and is recorded above.
- No signature, no provenance attestation, no transparency log.
- Builds are not guaranteed bit-for-bit reproducible (timestamps/toolchain).
- No SBOM artifact was generated in this pass; `ogx sbom` exists but was not
  run against the built wheel.

## 6. Secret and artifact hygiene

- No credentials are in the wheel or drop-in archive.
- `.env`, databases, wheels, bytecode and results logs are excluded from the
  drop-in builder.
- `dist/` and `release/` are gitignored; this pass removed its temporary build
  and venv directories.

## 7. Verdict

Artifact contents are **clean and installable**. Release integrity is
**PARTIAL**: no sdist, no signing, no provenance, no reproducibility guarantee,
no SBOM.
