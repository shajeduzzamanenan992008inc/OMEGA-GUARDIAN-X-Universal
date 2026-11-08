# OGX 0.7.1a1 Validation Results

Local validation performed in the build workspace on 2026-11-08:

- `python -m compileall -q src tests` — passed.
- `PYTHONPATH=src pytest -q` — 94 tests passed, including `.env` CLI, parser/configuration, schema-coverage and permissions tests.
- Package wheel built with `python -m pip wheel --no-deps --no-build-isolation .` — passed.
- Built wheel installed into an isolated target directory and CLI version/framework/database initialization smoke checks — passed.
- Wheel archive inspection — verified inclusion of enterprise schema, email adapters, approval workflow and governance operating model.
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
- Full SAST/DAST, transitive dependency resolution, exhaustive CVE intelligence, runtime debugger, production SaaS, independent security audit or certification.

The 94-test result is local unit/fixture evidence, not a claim of universal compatibility, exploitability proof, email delivery, production readiness or enterprise certification. The CLI/SQLite identity subjects remain caller-supplied in this alpha; production requires authenticated SSO/OIDC/SAML sessions and centralized service controls.
