# OMEGA GUARDIAN X — Universal Enterprise Guardian

**Release:** `0.8.0a1` · **Maturity:** Alpha · **Python:** 3.11–3.13 (declared)

A two-part project governance and repository intelligence kit:

- **`.github/`** — reusable repository engineering kit: specialist AI-agent instructions, secure CI templates, runbooks, issue/PR templates, Dependabot examples and cross-stack development guidance.
- **`GUARDIAN-X/`** — runnable local CLI engine: repository audit, stack detection, syntax/security heuristics, architecture inventory, safe repair preview, Git workflow, enterprise governance records and email-gated approvals.

This is a local-first alpha, not a certified compliance product, production SaaS service or autonomous system that can safely repair every language and deploy changes without review.

## Repository architecture

- **Canonical source:** `GUARDIAN-X/src/ogx/` — packaged into the `omega-guardian-x` wheel and the `ogx` console script. Implementation changes belong here.
- **Drop-in distribution:** `GUARDIAN-X/core/ogx/` — a hand-maintained, standard-library-only tree loaded by `GUARDIAN-X/init.py`. It uses a different internal layout and does not ship the provider engine.
- **Consistency check:** `python GUARDIAN-X/_dev/tools/sync_core_from_src.py --check` verifies version agreement, required packages/modules, absence of nested duplicate trees, name collisions and packaging declarations. It never generates or deletes feature code.
- **Tests:** `GUARDIAN-X/tests/` (canonical source, pytest) and `GUARDIAN-X/_dev/tests/` (drop-in plus architecture/distribution checks, unittest).

## Quick install

From this directory:

```powershell
cd GUARDIAN-X
python -m pip install .
ogx --version
ogx guardian audit "D:\Projects\MyProject"
```

Build a wheel without runtime dependencies, or install the generated wheel from `GUARDIAN-X/release/`:

```powershell
python -m pip wheel . --no-deps -w release
python -m pip install .\release\omega_guardian_x-0.8.0a1-py3-none-any.whl
```

## Private configuration (`.env`)

The Guardian CLI centralizes mail, email approval, and optional Git-hosting credentials in a private `.env` file stored outside scanned projects by default. After installation, run `ogx env init`, `ogx env path`, edit the displayed file, then run `ogx env check`. Real secrets are never bundled; `GUARDIAN-X/.env.example` contains placeholders only and real `.env` files are Git-ignored.

## Enterprise operating model

```powershell
ogx enterprise framework
ogx enterprise init
ogx enterprise org-create --slug acme --name "Acme" --owner-subject "idp|owner"
```

The control model covers six pillars:

1. Leadership — accountability, delegation, risk appetite and escalation.
2. Direction — mission, strategy, objectives, OKRs and roadmap priorities.
3. Management — portfolio, capacity, delivery, budget/quality tracking and benefits.
4. Administration — membership, roles, policy, audit evidence and control records.
5. Steering — stage gates, assurance, decisions, risk acceptance and corrective action.
6. Operations — change/incident handling, service metrics, reliability and continuity.

Records currently supported by the local data model: objectives, initiatives, risks, decisions, changes, incidents and service metrics. Membership roles are owner, admin, developer, auditor and viewer.

## Email approval before supported write actions

Confirmed integration changes, supported safe repairs, Git fetch/clone/branch/push and draft PR/MR actions queue for email approval by default. The command sends a one-time code to the designated approver; the action does not execute at request time. After the approver records the decision, the original requester executes that same validated request once.

### Brevo transactional mail

The code supports the Brevo transactional API. Set secrets in the current shell or a secret manager; never commit them:

```powershell
$env:OGX_MAIL_PROVIDER = "brevo"
$env:OGX_BREVO_API_KEY = "<Brevo API key>"
$env:OGX_MAIL_FROM_EMAIL = "verified-sender@example.com"
$env:OGX_APPROVER_EMAIL = "approver@example.com"
$env:OGX_APPROVER_SUBJECT = "idp|approver-subject"
$env:OGX_ACTOR_SUBJECT = "idp|requester-subject"
```

### SMTP-compatible mail provider

If your mail provider exposes standard SMTP relay credentials, use the SMTP adapter instead. Note: the Bravo AI product at `bravo.ai` documents connected Gmail/Outlook access as read-only and says it cannot send or edit email. If this is the product you mean, it cannot be the outbound approval sender; use Brevo or a mail provider with SMTP/API sending capability. If you mean a different “Bravo Mail” service, use this adapter only if it provides SMTP relay credentials:

```powershell
$env:OGX_MAIL_PROVIDER = "smtp"
$env:OGX_SMTP_HOST = "<SMTP host>"
$env:OGX_SMTP_PORT = "587"
$env:OGX_SMTP_USERNAME = "<SMTP username>"
$env:OGX_SMTP_PASSWORD = "<SMTP password or SMTP key>"
$env:OGX_MAIL_FROM_EMAIL = "verified-sender@example.com"
$env:OGX_APPROVER_EMAIL = "approver@example.com"
$env:OGX_APPROVER_SUBJECT = "idp|approver-subject"
$env:OGX_ACTOR_SUBJECT = "idp|requester-subject"
```

Do not assume a Brevo API key can be used against another provider. A distinct Bravo-branded mail service without SMTP compatibility needs its own documented API adapter.

Example workflow:

```powershell
ogx integrate plan "D:\Projects\MyProject"
ogx integrate apply "D:\Projects\MyProject" --confirm
# Approver, from a trusted terminal:
ogx governance approve <REQUEST_ID> --code <ONE_TIME_CODE>
# Original requester:
ogx governance execute <REQUEST_ID>
```

Read-only audits do not require email approval. Never enable `OGX_ALLOW_UNAPPROVED_WRITES=1` on a shared or production machine.

## Verification

Locally executed for this working tree (Windows, CPython 3.15.2):

- Canonical source: `python -m pytest -q` — **465 passed, 15 skipped** (reproduced 2026-11-26 at HEAD `36e56c0` on CPython 3.15.2, 90.58 s, exit 0). The earlier `340 passed, 11 skipped` completion-pass figure is kept as history only.
- Drop-in distribution: `PYTHONPATH=core python -m unittest discover -s _dev/tests` — **258 tests, OK (9 skipped)** (reproduced 2026-11-26 at HEAD `36e56c0`, 71.25 s, exit 0; 13 of those tests are the archive-generator regression suite added in Milestone I.5).
- Architecture and distribution checks (included in the suites) — 17 passed.
- `python -m compileall -q src core tests _dev/tests` — passed.
- Wheel `omega_guardian_x-0.8.0a1-py3-none-any.whl` — rebuilt 2026-11-26 from the clean `36e56c0` tree (SHA-256 `04243ea0…`), 167 files, 402,363 bytes, no test/CI/source-tree leakage; installed into a clean venv; `ogx --version`, `--help`, `providers list`, `platform status` and `host list` passed. The build warns that the declared `LICENSE` file is absent, so no license file is bundled.
- Drop-in zip — **326 files** from a clean `36e56c0` export on 2026-11-26 (SHA-256 `a65a2c80…`; the I.5 execution baseline `a004348` also produced 326 entries). The nested `omega-shield/GUARDIAN-X/.github/RELEASE_NOTES.md` entry found in Milestone I.3 is gone: the generator fix committed in `06ffef4` excludes repository directory names at any depth and ignores `*.egg-info` build metadata. The unmodified CI forbidden-path assertion reports **0 hits**, `omega-shield/init.py` and `omega-shield/core/ogx/__init__.py` are present, and `init.py status` passed. `_dev/tests/test_make_dist.py` now regression-tests these exclusions, the required runtime entries, the `omega-shield/` prefix and deterministic entry ordering from synthetic fixtures.

The skips are platform-gated tests on Windows (15 in the canonical suite — 12 symlink, 2 POSIX file-mode, 1 where Windows drive-relative rejection is covered by a separate test — and 9 in the drop-in suite); they execute on the Linux CI matrix. Every figure in this section was re-executed locally on Windows/CPython 3.15.2 at HEAD `36e56c0` on 2026-11-26. Hosted GitHub Actions has **not** produced a verified run for `36e56c0`: the last observed push-triggered runs (for `757d6b6`) were refused before any job started because of account billing/spending limits, and neither `a004348` nor `36e56c0` was queried for a hosted run (`gh` is not installed and the unauthenticated API was rate-limited). Python 3.11/3.12/3.13 were not available locally. Brevo and SMTP transports are tested with mocked HTTP/SMTP clients; no real inbox delivery was tested.

## Read before production use

See `GUARDIAN-X/docs/ENTERPRISE_GOVERNANCE_AND_APPROVALS.md`, `GUARDIAN-X/docs/SECURITY_MODEL.md` and `GUARDIAN-X/docs/ENTERPRISE_READINESS.md`. This CLI alpha does not authenticate its `idp|...` subject strings against a real OIDC/SAML identity provider. Production enterprise use requires authenticated SSO, managed database/migrations, KMS/secret rotation, tenant-isolation controls, immutable centralized audit, operational monitoring, backups/DR, abuse protection and an independent security review.
