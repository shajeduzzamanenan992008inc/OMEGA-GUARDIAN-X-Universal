# Universal Engine Audit — 0.7.1a1

Date: 2026-11-14  
Scope: additive universal analysis layer, spec-driven language engines, dedicated Android engine, unified findings, build/test discovery, and new CLI/reporting commands.  
Mode: local, static, read-only. No target code, build, test, package manager, device, emulator, or network operation was executed.

## 1. Executive result

The repository already contained a working OGX CLI, scanner, reports, Guardian workflow, enterprise prototype and 94 passing tests. The universal layer was added without replacing those paths.

| Question | Result |
|---|---|
| Existing CLI and scanner behavior preserved? | Yes. Existing commands remain registered; scanner tests and the full legacy suite pass. |
| New engine contract available? | Yes. 51 registered engines: 1 Android + 50 declarative technology engines. |
| Unified findings available? | Yes, with engine provenance, stable IDs, verification state, deduplication and secret redaction. |
| Android analysis available? | Yes, static only. Manifest, Gradle, R8, resources, sources, tests and APK/AAB ZIP metadata are inspected. |
| Android build/test/device execution available? | No. SDK, Gradle, adb, emulator and device operations are `not_configured` or `not_attempted` in this environment. |
| Build/test commands executed? | Never. Discovery only; every adapter reports `executed: false`. |
| Target repository modified by the new commands? | No. Reports are written under external `OGX_HOME` state and symlinks are refused. |
| Network used by the new commands? | No. |
| New third-party runtime dependencies? | None. Standard library only. |

## 2. Audit of the pre-existing baseline

The pre-change repository was inspected before edits.

- Branch: `main`; baseline commit `749acc4`.
- Python: 3.15.2; Git: 2.56.0; Node: 26.13.0; OpenJDK: 17.0.20.1.
- Android SDK: absent. `gradle`, `adb`, `sdkmanager` and `emulator`: absent.
- Baseline suite: 94 tests passed, 7 skipped, using `PYTHONPATH=core python -m unittest discover -s _dev/tests`.
- Existing user changes were preserved:
  - deleted `GUARDIAN-X/README.md`, root `.github/README.md` and other `.github` files as present in the working tree;
  - untracked `GUARDIAN-X/README-Guardian-X.md` and root `.github/README-Github.md`;
  - all other uncommitted moves/deletions were not reverted.
- `pyproject.toml` referenced the deleted `README.md`. It was updated to the existing `README-Guardian-X.md` so packaging remains valid without recreating the user's deleted file.

## 3. What was added

| Area | Files | Purpose |
|---|---|---|
| Shared rules | `core/ogx/rules.py` | Secret, code, shell, PowerShell, Docker, Kubernetes, Terraform and CI patterns; placeholder detection; evidence redaction. |
| Safe workspace | `core/ogx/workspace.py` | Bounded indexing, read-only access, traversal/symlink refusal, DTD rejection, JSONC helper, SHA-256 and project fingerprint. |
| Unified findings | `core/ogx/findings.py` | `UnifiedFinding`, stable IDs, verification states, severity summary and deduplication. |
| Engine contract | `core/ogx/engines/contract.py` | `BaseEngine`, capability states, failure isolation, execution/network assertions. |
| Build/test discovery | `core/ogx/engines/build.py` | Adapter and test pattern discovery; tool presence only. No commands are run. |
| Language specs | `core/ogx/engines/language_specs.py` | 50 declarative language/technology specifications. |
| Language engine | `core/ogx/engines/language.py` | Detection, structural parsing, declared dependency inventory, rules and discovery. |
| Android engine | `core/ogx/engines/android.py` | Android-specific static analysis and environment capability checks. |
| Registry | `core/ogx/engines/registry.py` | Registration, selection, health and execution planning. |
| Pipeline | `core/ogx/pipeline.py` | Universal/Android reports, engine inspection, unified findings and Markdown renderers. |
| Tests | `tests/test_engines.py`, `tests/test_android_engine.py`, `tests/test_pipeline.py`, `tests/test_cli_universal.py` | Contract, language, Android, pipeline and CLI regression coverage. |

Existing files modified for the engine work are limited to: `core/ogx/scanner.py` (now imports the extracted shared rule catalog while preserving public behavior), `core/ogx/cli.py` (additive imports and new command handlers/parsers), and `pyproject.toml` (readme reference changed to the existing `README-Guardian-X.md`). All other pre-existing behavior was preserved; the user's README and `.github` moves were not reverted.

## 4. Capability status vocabulary

Every engine operation reports one of:

- `supported` — implemented and covered by local tests for the stated scope.
- `partial` — implemented with documented heuristics or missing external resolution.
- `not_configured` — implementation exists but a local tool/SDK or project manifest is absent.
- `unsupported` — no implementation for this operation.
- `failed` — the operation raised; the failure is isolated and reported.

These states describe local capability, not certification of a language, framework, or security posture.

## 5. Verified behavior

Commands run on 2026-11-14:

```powershell
python -m compileall -q core _dev/tests
PYTHONPATH=core python -m unittest discover -s _dev/tests
PYTHONPATH=core python -m ogx engines list --json
PYTHONPATH=core python -m ogx engines inspect <fixture> --engines android,lang-python --json
PYTHONPATH=core python -m ogx analyze <fixture> --format both
PYTHONPATH=core python -m ogx android <fixture> --format both
```

Observed results:

- `compileall`: passed.
- Full suite: 134 tests, 9 skipped, 0 failures.
- Registry: 51 engines; unique IDs; health reports `static-read-only`, `execution_performed: false`, `network_access: false`.
- Universal fixture: 14 engines detected (Android, Python, JavaScript, TypeScript, Go, Rust, Kotlin, Java, TOML, JSON, XML, YAML, Dockerfile, Kubernetes), 18 findings, and all 12 fixture files unchanged by SHA-256 after the run.
- Android fixture: Android detected; 5 findings across 5 Android rules in the final fixture run (27 distinct Android rules are exercised across the test suite); build/test status `not_configured`; device operations `not_attempted`; no secret values in JSON or Markdown.
- `analyze` and `android` wrote reports only under external `OGX_HOME`; the target directory listing was unchanged.
- CLI compatibility: existing commands `detect`, `scan`, `start`, `inventory`, `architecture`, `sbom`, `diagnose`, `validate`, `guardian`, `enterprise` and `governance` remain registered.

## 6. Android environment finding

This build environment does not provide a usable Android build/device toolchain:

| Requirement | Status |
|---|---|
| Android SDK | Not detected |
| `gradle` | Not installed |
| Gradle wrapper scripts | Not present in the test fixture; presence would still not authorize execution |
| `adb` | Not installed |
| `sdkmanager` | Not installed |
| `emulator` | Not installed |
| JDK | Installed, but insufficient without Android SDK/Gradle |
| Build execution | `not_configured` |
| Device/emulator operations | `not_attempted` |

Therefore Android build, test execution, APK installation, instrumentation tests and on-device validation are **not verified**. APK/AAB inspection is limited to ZIP metadata and v1 signature-entry presence; signatures are not cryptographically verified and binary manifests are not decoded.

## 7. Security and safety properties

- Scanning is local and read-only.
- The workspace root is resolved once; traversal and absolute paths are rejected.
- Symlinked files/directories are skipped, and report destinations reject symlinks.
- File count, file size, per-engine byte budgets and finding counts are bounded.
- XML DTD/entity declarations are rejected before parsing.
- Secret values are redacted from findings, facts and reports; Android signing and Gradle credential values are redacted.
- No target script, build tool, package manager, wrapper, test runner, adb command, emulator or network client is invoked.
- Build/test proposals are labeled `not_executed`/`not_configured` and require a separately approved isolated worker.
- Findings are labeled `observed` or `inferred`; heuristic findings are not presented as exploitability proof.

## 8. Known limitations

- Language engines are not compiler-grade. C-like delimiter balance is heuristic; text parsers do not build full syntax trees.
- YAML analysis is structural only because no YAML parser dependency is bundled.
- Dependency inventory is declaration-level only: no resolver, lockfile verification, transitive graph, installed-version evidence or vulnerability lookup.
- Framework detection remains indicator-based.
- Android binary manifests, APK signing blocks and runtime behavior are not analyzed.
- No real Android build, unit test, instrumentation test, emulator or device was run.
- No real-world large-repository performance, fuzzing, penetration test or independent review was performed.
- Container, Git-hosting and email workflows retain their pre-existing external dependencies and were not re-certified here.

## 9. Rollback instructions

No commit or push was made. To roll back only this universal layer while preserving user changes:

1. Review the working tree:
   ```powershell
   git status --short
   ```
2. Revert the shared scanner import if a complete rollback is required:
   ```powershell
   git restore -- core/ogx/scanner.py
   ```
3. Remove the new untracked engine files and tests:
   ```powershell
   Remove-Item -Recurse -Force core/ogx/engines
   Remove-Item -Force core/ogx/rules.py, core/ogx/workspace.py, core/ogx/findings.py, core/ogx/pipeline.py
   Remove-Item -Force tests/test_engines.py, tests/test_android_engine.py, tests/test_pipeline.py, tests/test_cli_universal.py
   ```
4. Revert the additive CLI imports/functions/parsers and the `pyproject.toml` readme reference using a reviewed diff or `git restore -- core/ogx/cli.py pyproject.toml`.
5. Restore documentation changes selectively. Do not run `git reset --hard` or `git clean -fd`: those would destroy unrelated user changes, including README and `.github` moves.

## 10. Release recommendation

**Conditional alpha recommendation: GO for source-level review and controlled pilot; NO-GO for production or Android certification.**

Reasons to proceed:

- The existing suite remains green.
- New behavior is additive and defaults to read-only.
- Capability states are explicit, and unavailable Android execution is reported instead of simulated.
- The new CLI commands are tested and write reports externally.

Blocking conditions for a production release:

- Android SDK/Gradle integration must be tested in an approved isolated environment, including `./gradlew test` and at least one debug build.
- APK signature verification and binary manifest decoding require approved tooling and fixtures.
- Dependency resolution, lockfile verification and transitive vulnerability data remain unimplemented.
- Independent security review, performance testing on large repositories, and packaging/install validation across supported OSes are still required.
- No claim of full language, framework, Android, or security coverage should be made.
