# OMEGA GUARDIAN X — Universal Enterprise Guardian

**Release:** `0.7.1a1` · **Maturity:** Alpha · **Python:** 3.11+

A two-part project governance and repository intelligence kit:

- **`.github/`** — reusable repository engineering kit: specialist AI-agent instructions, secure CI templates, runbooks, issue/PR templates, Dependabot examples and cross-stack development guidance.
- **`GUARDIAN-X/`** — runnable local CLI engine: repository audit, stack detection, syntax/security heuristics, architecture inventory, safe repair preview, Git workflow, enterprise governance records and email-gated approvals.

This is a local-first alpha, not a certified compliance product, production SaaS service or autonomous system that can safely repair every language and deploy changes without review.

## Quick install

From this directory:

```powershell
cd GUARDIAN-X
python -m pip install .
ogx --version
ogx guardian audit "D:\Projects\MyProject"
```

The standalone wheel is included in `GUARDIAN-X/release/` and can be installed with:

```powershell
python -m pip install .\release\omega_guardian_x-0.7.1a1-py3-none-any.whl
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

Local validation for this release: **80 unit tests passed**, Python compile check passed, wheel build passed, isolated wheel install and CLI/framework/database smoke checks passed. Brevo and SMTP transports were tested with mocked HTTP/SMTP clients; no real mail account or credentials were supplied, so real inbox delivery was not tested.

## Read before production use

See `GUARDIAN-X/docs/ENTERPRISE_GOVERNANCE_AND_APPROVALS.md`, `GUARDIAN-X/docs/SECURITY_MODEL.md` and `GUARDIAN-X/docs/ENTERPRISE_READINESS.md`. This CLI alpha does not authenticate its `idp|...` subject strings against a real OIDC/SAML identity provider. Production enterprise use requires authenticated SSO, managed database/migrations, KMS/secret rotation, tenant-isolation controls, immutable centralized audit, operational monitoring, backups/DR, abuse protection and an independent security review.
