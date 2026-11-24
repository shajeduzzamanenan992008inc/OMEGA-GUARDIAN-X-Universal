# Release Guide

This guide describes how to produce and verify `0.8.0a1` artifacts locally. It
does **not** authorize publishing. Publishing a release, tag or package index
upload requires explicit user authorization.

## 1. Version sources

Three files must agree:

| File | Source of truth |
| --- | --- |
| `VERSION` | `0.8.0a1` |
| `src/ogx/__init__.py` | `__version__ = "0.8.0a1"` |
| `core/ogx/__init__.py` | reads `VERSION` via `_read_version()` |

```bash
python _dev/tools/sync_core_from_src.py --check
```

Expected: `OK: source and drop-in structure, versions and packaging
declarations agree.`

## 2. Pre-release gates

Run from `GUARDIAN-X/`:

```bash
python -m pytest -q
PYTHONPATH=core python -m unittest discover -s _dev/tests
python _dev/tools/sync_core_from_src.py --check
```

Release gate: zero failures. Skips are acceptable only with a recorded platform
reason (for example symlink creation on Windows). Do not weaken assertions to
make a gate pass.

## 3. Build the wheel

`python -m build` is unreliable in this repository because a local `build/`
directory shadows the module. Use pip:

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

Expected metadata:

```text
Name: omega-guardian-x
Version: 0.8.0a1
Requires-Python: >=3.11
License-Expression: LicenseRef-OMEGA-GUARDIAN-X-Proprietary-Alpha
License-File: LICENSE
```

## 4. Inspect the wheel

```bash
python - <<'PY'
import glob, zipfile
p = glob.glob("dist/*.whl")[0]
z = zipfile.ZipFile(p)
names = z.namelist()
print("entries", len(names))
print("forbidden", [n for n in names if n.startswith(("tests/", "src/", "_dev/", ".github/", "release/"))])
print("license", [n for n in names if n.endswith("LICENSE")])
PY
```

The 0.8.0a1 wheel has 163 entries, no forbidden paths, and bundles `LICENSE`.

## 5. Clean-environment installation test

```bash
python -m venv .venv-release
.venv-release/Scripts/python.exe -m pip install --no-index --no-deps dist/*.whl   # Windows
# .venv-release/bin/python -m pip install --no-index --no-deps dist/*.whl        # POSIX
.venv-release/Scripts/python.exe -c "import ogx; print(ogx.__version__)"
.venv-release/Scripts/ogx.exe --version
.venv-release/Scripts/ogx.exe providers list > /dev/null
```

All four commands must exit 0. `--no-index` proves the package needs no
dependencies.

## 6. Drop-in archive

```bash
python _dev/tools/make_dist.py
python - <<'PY'
import zipfile
z = zipfile.ZipFile("dist/omega-shield-0.8.0a1-dropin.zip")
print(len(z.namelist()))
print("omega-shield/init.py" in z.namelist())
PY
```

Extract into a temporary location and run:

```bash
cd /path/to/extracted/omega-shield && python init.py status
```

The archive must exclude `src/`, `tests/`, `_dev/`, `.github/`, `release/`,
`dist/`, `.env`, databases, wheels and bytecode. Do not build it while a
temporary directory exists inside `GUARDIAN-X/`, because the builder walks the
tree and would include it.

## 7. Checksums

```bash
sha256sum dist/*.whl dist/*-dropin.zip
```

Record the digests in the release notes. Checksums are not signatures.

## 8. Signing, provenance and reproducibility — not implemented

- No artifact signing key is available or used.
- No SLSA/provenance attestation is produced.
- Builds are not guaranteed bit-for-bit reproducible; wheel bytes depend on
  timestamps and toolchain versions.

Do not claim signed or reproducible artifacts.

## 9. Publishing (requires explicit authorization)

A maintainer who has been authorized may:

1. Commit the release state and tag it.
2. Create a GitHub release and attach the wheel, drop-in zip and `SHA256SUMS`.
3. Upload to a package index only if the license and visibility are approved.

None of these steps were performed by the agent. Do not run `git push`, tag,
merge or publish without authorization.

## 10. Rollback

- Keep the previous wheel/drop-in zip and its checksums.
- Uninstall the package and reinstall the previous wheel:
  `python -m pip install --force-reinstall <previous.whl>`.
- State under `OGX_HOME` (registry, audit, reports) is forward-only; back it up
  with `ogx registry backup` before upgrading.

## 11. Release checklist

- [ ] Versions agree (`--check`)
- [ ] Full pytest suite passes
- [ ] Drop-in suite passes
- [ ] Wheel built from a clean tree
- [ ] Wheel metadata and LICENSE verified
- [ ] Fresh-venv install and CLI smoke pass
- [ ] Drop-in archive inventory and `init.py status` pass
- [ ] Checksums recorded
- [ ] Docs match behavior
- [ ] Residual risks reviewed
- [ ] Hosted CI run exists (currently blocked)
- [ ] Independent review completed (currently blocked)
- [ ] Explicit authorization to publish
