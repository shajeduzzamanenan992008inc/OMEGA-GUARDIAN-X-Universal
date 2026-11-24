# Cross-Platform Verification (Phase 7)

Date: 2026-11-24

## 1. Declared support

`pyproject.toml`: `requires-python = ">=3.11"`; classifiers list Python 3.11,
3.12 and 3.13; `Operating System :: OS Independent`.

## 2. Executed matrix

| OS | Python | Canonical suite | Drop-in suite | Status |
| --- | --- | --- | --- | --- |
| Windows (`win32`) | CPython 3.15.2 | 409 passed, 12 skipped | 245 tests, OK (9 skipped) | EXECUTED |
| Linux | 3.11 / 3.12 / 3.13 | — | — | UNVERIFIED |
| macOS | 3.11 / 3.12 / 3.13 | — | — | UNVERIFIED |
| Windows | 3.11 / 3.12 / 3.13 | — | — | UNVERIFIED |

Python 3.15.2 is outside the declared classifier range. Passing on it is
evidence that the code runs on a newer interpreter, not proof for 3.11–3.13.

## 3. Skips (12 canonical, 9 drop-in)

All skips are platform gates, not failures:

- symlink creation unavailable on Windows without privileges;
- POSIX file-mode assertions unavailable on Windows;
- directory symlink tests unavailable.

No test was weakened or removed to produce a pass. On Linux/macOS these are
expected to execute; that expectation is unverified here.

## 4. Platform-specific behaviour observed

- **Paths:** Git Bash `/tmp` is not visible to native Windows Python; temporary
  artifacts must be project-local or converted with `cygpath -w`.
- **Build:** a local `build/` directory shadows the `build` module; `pip wheel`
  is the working build path.
- **File modes/links:** security checks that depend on POSIX modes or symlinks
  are skipped on this host.
- **Encoding:** reports and logs are UTF-8; no encoding failure was observed.
- **Subprocess:** Git-dependent tests pass only where Git is installed.

## 5. Conclusion

Cross-platform support is **PARTIALLY VERIFIED**: Windows on one interpreter
version only. Linux and macOS remain UNVERIFIED until the hosted matrix runs.
