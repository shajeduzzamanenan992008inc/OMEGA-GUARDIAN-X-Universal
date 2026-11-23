# OGX 0.7.1a1 Validation Results

Local validation performed in the build workspace on 2026-11-08 and re-run on 2026-11-14 after the universal engine work:

- `python -m compileall -q core _dev/tests` — passed.
- `PYTHONPATH=core python -m unittest discover -s _dev/tests` — 134 tests passed, 9 skipped, 0 failures.
- The 40 new universal-layer tests (`test_engines.py`, `test_android_engine.py`, `test_pipeline.py`, `test_cli_universal.py`) pass: 17 + 7 + 8 + 8 tests; 2 symlink-oriented tests are skipped on Windows.
- Package wheel built with `python -m pip wheel --no-deps --no-build-isolation . -w dist` — passed (`omega_guardian_x-0.7.1a1-py3-none-any.whl`, 87 archive entries, 176,338 bytes).
- Built wheel installed into an isolated target directory; `ogx --version`, `ogx engines list` and engine registry import smoke checks — passed.
- Wheel archive inspection — verified inclusion of enterprise schema, email adapters, approval workflow, governance operating model, `ogx.engines` package and the new pipeline/workspace/findings/rules modules.
- Universal engine registry — 51 unique engine IDs (1 Android + 50 technology engines), all reporting `static-read-only` and `execution_performed: false`.
- Universal fixture run (`ogx analyze`) — 14 engines detected (Android, Python, JavaScript, TypeScript, Go, Rust, Kotlin, TOML, JSON, XML, YAML, Dockerfile, Kubernetes, Java), 18 findings, external JSON+Markdown reports, and all 12 fixture files unchanged by SHA-256 before/after.
- Android fixture run (`ogx android`) — manifest/Gradle/source checks produced 5 findings across 5 Android rules; build and test capabilities were `not_configured`, device operations `not_attempted`, `executed: false`, `build_ready: false`; no SDK, Gradle, adb, emulator or device command was run.
- Report-path protection — reports are written only under external `OGX_HOME` state; symlinked report destinations are refused; source-tree files are not created or modified.
- `.env` CLI, parser/configuration, schema-coverage and permissions tests remain in the full suite and pass.
- Enterprise governance: six operating pillars, organization memberships, owner/admin privilege boundaries, last-owner protection, governance record permissions and status transitions.
- Email approval ledger: code hashing, one-time use, expiry, invalid-attempt lockout, four-eyes separation, mail-delivery failure handling, exact action revalidation, request/event lifecycle, and requester-bound execution.
- Brevo API request-shape test — passed with a mocked HTTPS transport; no real email was sent.
- `.env` configuration: known-key allowlist, duplicate/malformed key rejection, no shell execution, no target-project auto-loading, environment precedence, symlink refusal, POSIX mode warning, private file creation and no-overwrite behavior — passed.
- CLI `.env` initialization and validation through explicit `OGX_ENV_FILE` — passed.
- `.env.example` is checked against every supported OGX runtime setting; no non-empty secret values are bundled — passed.
- SMTP adapter tests — STARTTLS on 587 and implicit TLS on 465 verified using mocked transports; no real SMTP server was contacted.
- Guardian audit: combined profile, inventory, architecture, compatibility, quality gates and maintenance recommendations.
- Syntax checks: Python AST, JSON (excluding common JSONC files), TOML and XML project manifests.
- Safe repair: dry-run, external before-image, preservation of existing `.gitignore` content, nested environment-file detection, tracked `.env` blocking, symlink blocking and invalid UTF-8 blocking.
- Git upgrade workflow: offline non-repository handling, local bare remote branch comparison, confirmed ref-only fetch and unchanged working-tree status.
- Cross-platform integration: Dependabot generation, Renovate configuration validation and preservation of existing user files.
- Existing integration, scanner, SBOM, OSV request-shaping, enterprise prototype, skill validation, secret redaction, sandbox snapshot policy, debug triage and impact tests — passed.

Not run / not established:

- Real Brevo API key, verified sender or SMTP relay credentials were not supplied; end-to-end email delivery/inbox arrival was not tested. Bravo AI at bravo.ai is not treated as an outbound sender because its published email connections are read-only.
- Ruff lint — the Ruff executable is not installed in this environment.
- Real hosted GitHub/GitLab/Bitbucket/Gitea/Forgejo credentials or PR/MR lifecycle.
- Renovate/Dependabot bot installation, schedule execution or package update PRs in a customer repository.
- Real Docker image execution, container escape testing or Windows PowerShell installer/uninstaller validation.
- Full application test/build execution across all supported languages; Guardian deliberately did not execute target project scripts.
- Android build, unit test, instrumentation test, emulator and device execution — no Android SDK, Gradle, adb, sdkmanager or emulator is installed; capability states remain `not_configured`/`not_attempted`.
- APK/AAB signature verification, binary-manifest decoding and on-device runtime behavior.
- Full SAST/DAST, transitive dependency resolution, exhaustive CVE intelligence, runtime debugger, production SaaS, independent security audit or certification.

The 134-test result is local unit/fixture evidence, not a claim of universal compatibility, exploitability proof, Android build verification, email delivery, production readiness or enterprise certification. The CLI/SQLite identity subjects remain caller-supplied in this alpha; production requires authenticated SSO/OIDC/SAML sessions and centralized service controls.
