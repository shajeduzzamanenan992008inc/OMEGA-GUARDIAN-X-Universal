# Installation

## Requirements

| Requirement | Value |
| --- | --- |
| Python | 3.11 or newer (classifiers declare 3.11, 3.12, 3.13) |
| Runtime dependencies | none (standard library only) |
| Build dependency | `setuptools>=77` |
| Optional | Git, Docker (sandbox only), Ruby (config validator only) |

The package was verified locally on Windows with CPython 3.15.2. Runs on the
declared 3.11–3.13 matrix and on Linux/macOS are configured in CI but were not
executed on the verification host; see `docs/PLATFORM_SUPPORT_MATRIX.md`.

## Install from source

```bash
cd GUARDIAN-X
python -m venv .venv
# Windows
.venv/Scripts/python.exe -m pip install .
# POSIX
.venv/bin/python -m pip install .
```

Editable install for development:

```bash
python -m pip install -e .
python -m pytest -q
```

## Install from a built wheel

```bash
cd GUARDIAN-X
python -m pip wheel . --no-deps --no-build-isolation -w dist
python -m pip install --no-index --no-deps dist/omega_guardian_x-0.8.0a1-py3-none-any.whl
```

The wheel has no dependencies, so `--no-index` works offline.

## Verify the installation

```bash
ogx --version                          # ogx 0.8.0a1
ogx --help                             # command surface
ogx providers list                     # 28 descriptors
ogx platform status                    # 7 verified / 21 descriptor-only
```

If the `ogx` script is not on `PATH`, invoke the module:

```bash
python -m ogx --version
```

## First run

```bash
ogx init          # create state outside the target repository
ogx detect .      # read-only technology detection
ogx scan .        # read-only scan; reports in the OGX state directory
```

`ogx init` must not be run inside a repository you do not intend to attach;
state is global, reports are external, and scanning never writes into the
project.

## Project identity

```bash
ogx identity create . --name "My Project"
ogx identity show .
ogx identity validate .
```

This writes `.guardian-x/identity.json` in the project control folder. The
project keeps its UUID when moved between directories, drives or hosts. See
`docs/PROJECT_IDENTITY.md`.

## Drop-in distribution (no installation)

Build the lean archive:

```bash
cd GUARDIAN-X
python _dev/tools/make_dist.py            # writes dist/omega-shield-0.8.0a1-dropin.zip
```

Extract it next to the project you want to guard, then:

```bash
cd omega-shield
python init.py status
```

The archive contains `init.py`, `core/`, `guard/`, `devkit/`, `link/`, `docs/`
and shield metadata. It excludes `src/`, `tests/`, `_dev/`, `.github/`,
`release/`, `dist/`, databases, wheels, `.env` files and bytecode.

The drop-in requires no third-party packages; it uses only the standard library
and the host Python (3.11+).

## Uninstall

```bash
python -m pip uninstall omega-guardian-x
```

State under `OGX_HOME` is not removed by uninstall. Delete it explicitly only
if you intend to discard registry, audit and report history.

## Troubleshooting

See `docs/TROUBLESHOOTING.md`.
