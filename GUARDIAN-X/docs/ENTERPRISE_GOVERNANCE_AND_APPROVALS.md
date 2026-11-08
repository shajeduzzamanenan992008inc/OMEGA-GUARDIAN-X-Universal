# OMEGA GUARDIAN X — Enterprise Governance & Email Approval

## Scope and maturity

Release `0.7.1a1` adds a local enterprise operating model, governance records, a one-time email approval ledger, and controlled execution for a small allowlist of project mutations. It is a single-machine/local-alpha control plane, **not** a production SaaS identity or workflow service. Do not represent this release as certified, fully autonomous or production-enterprise-ready.

## Six operating pillars

| Pillar | Operating responsibility | Core records |
|---|---|---|
| Leadership | Accountability, delegation, ethical conduct, risk appetite | Initiative, decision, risk |
| Direction | Mission, strategy, OKRs, roadmap and investment priorities | Objective, initiative, decision |
| Management | Portfolio, delivery, capacity, budgets, quality and benefits | Initiative, objective, risk, decision |
| Administration | Identity, access, policies, audit evidence, license/vendor controls | Decision, risk, change |
| Steering | Stage gates, assurance, cross-team blockers and corrective steering | Decision, risk, initiative, change |
| Operations | Change/release, incident/problem, reliability, backup and continuity | Change, incident, service metric, risk |

The operating model is available with `ogx enterprise framework`. Organization-scoped records are created through `ogx enterprise record-add`, updated through `record-update` (with a status transition graph), and reported through `overview` / `records`. Administration also includes member listing, role assignment and removal, with protection against last-owner lockout and admin-to-owner privilege escalation.

## Configure mail delivery

Secrets may be loaded from the private OGX `.env` file outside scanned repositories, the process environment, or a supported OS secret manager. Never commit a real `.env` file or put API keys/SMTP passwords in governance JSON, command-line arguments or email body templates. Use `ogx env init`, edit the private file shown by `ogx env path`, then run `ogx env check`. OS environment values take precedence. `OGX_ENV_FILE` may select an alternate file; OGX never auto-loads an untrusted target-project `.env`.

### Brevo API (default)

Configure these settings in the private `.env` file or process environment:

```text
OGX_MAIL_PROVIDER=brevo
OGX_BREVO_API_KEY=<Brevo API key>
OGX_MAIL_FROM_EMAIL=verified-sender@example.com
OGX_MAIL_FROM_NAME=OMEGA GUARDIAN X
OGX_APPROVER_EMAIL=approver@example.com
OGX_APPROVER_SUBJECT=idp|approver-subject
OGX_ACTOR_SUBJECT=idp|requester-subject
OGX_APPROVAL_TTL_MINUTES=30
```

The Brevo sender must be registered/verified. OGX uses the transactional email API endpoint `POST https://api.brevo.com/v3/smtp/email` and the `api-key` header. It sends a non-marketing approval notification with text and HTML content.

### SMTP-compatible mail service

Choose this for any mail provider that explicitly supplies SMTP relay credentials. **Bravo AI at bravo.ai is not an outbound mail provider**: its published connected Gmail/Outlook mail permissions are read-only and it states it cannot send or edit those messages. If you mean a different product named Bravo Mail, use this adapter only when that provider supplies SMTP host/port/login credentials; otherwise a provider-specific adapter is required.

Choose the following configuration in the private `.env` file or process environment:

```text
OGX_MAIL_PROVIDER=smtp
OGX_SMTP_HOST=<SMTP host>
OGX_SMTP_PORT=587
OGX_SMTP_USERNAME=<SMTP login>
OGX_SMTP_PASSWORD=<SMTP key/password>
OGX_MAIL_FROM_EMAIL=verified-sender@example.com
OGX_MAIL_FROM_NAME=OMEGA GUARDIAN X
OGX_APPROVER_EMAIL=approver@example.com
OGX_APPROVER_SUBJECT=idp|approver-subject
OGX_ACTOR_SUBJECT=idp|requester-subject
```

Port 465 uses implicit TLS; other configured ports use STARTTLS with certificate verification. Use the SMTP credential required by the mail provider, not an unrelated REST API key. Do not assume Bravo AI or a different mail provider implements Brevo's API. A distinct Bravo-branded mail service without SMTP compatibility needs its own documented sending API adapter.

## Approval flow

1. Review the read-only plan first.
2. Run the existing confirmed action. With default settings, OGX creates a pending request and sends a one-time code. It does not modify the project.
3. The designated approver checks the message and records approval or rejection from a trusted OGX terminal.
4. Only the original requester may execute an approved request, once, before its expiry.
5. The action result and lifecycle events remain in the local SQLite approval ledger.

Example safe repair:

```bash
ogx guardian repair-plan ./my-project
ogx guardian repair ./my-project --confirm
# Approver reads the email code and approves:
ogx governance approve <REQUEST_ID> --code <ONE_TIME_CODE>
# Original requester executes:
ogx governance execute <REQUEST_ID>
```

Example integration or remote fetch:

```bash
ogx integrate apply ./my-project --confirm
ogx guardian upgrade-fetch ./my-project --remote origin --confirm
```

View workflow state:

```bash
ogx governance list
```

### Approval security properties

- Code is generated with a cryptographically secure random source, salted and hashed in storage, compared in constant time, single-use and expires after 5–1440 minutes (default 30).
- No clickable auto-approval URL is sent. This avoids email link scanners accidentally approving requests.
- Requester and approver subject strings must differ; the approver must match the configured approver subject.
- Execution is limited to eight allowlisted operations: safe repair, Git fetch, integration apply/remove, Git clone, branch creation, feature-branch push and draft Pull/Merge Request creation. No arbitrary command string is accepted; no auto-merge or deployment is granted.
- Failed mail delivery disables the request. A queued request is not considered sent unless the mail API/SMTP adapter confirms acceptance.
- Request and event history is local to the OGX machine. The configured external subject values are identity claims supplied by the caller; this local-alpha release does not validate them against an OIDC/SAML provider.

## Local break-glass

For isolated development/test sandboxes only, `OGX_ALLOW_UNAPPROVED_WRITES=1` bypasses email approvals for direct confirmed writes. Do not use this on shared, production or enterprise-managed workstations. Remove the variable after testing.

## Enterprise record examples

```bash
ogx enterprise init
ogx enterprise org-create --slug acme --name "Acme" --owner-subject "idp|owner"
ogx enterprise record-add --org <ORG_ID> --type objective --title "Reduce escaped defects" --owner-subject "idp|quality-lead" --priority high --status planned --actor-subject "idp|owner"
ogx enterprise record-add --org <ORG_ID> --type risk --title "Unpatched critical dependency" --owner-subject "idp|security-lead" --priority critical --status open --actor-subject "idp|admin"
ogx enterprise overview --org <ORG_ID> --actor-subject "idp|owner"
ogx enterprise records --org <ORG_ID> --actor-subject "idp|owner"
ogx enterprise record-update --org <ORG_ID> --record-id <RECORD_ID> --status in_progress --actor-subject "idp|owner"
ogx enterprise member-add --org <ORG_ID> --external-subject "idp|developer" --role developer --actor-subject "idp|owner"
ogx enterprise members --org <ORG_ID> --actor-subject "idp|owner"
ogx enterprise audit --org <ORG_ID> --actor-subject "idp|owner"
```

Strategic objective/initiative/decision creation requires owner/admin. Operational records (risk, change, incident and service metric) can be recorded by owner/admin/developer. All list operations are organization-member scoped; overview and audit are owner/admin/auditor scoped.

## Production requirements still outstanding

Before real enterprise deployment: integrate and verify an OIDC/SAML IdP; replace caller-provided subject claims with authenticated sessions; use PostgreSQL with migrations and tenant-isolation tests; apply KMS-backed encryption and secret rotation; add rate limiting, abuse protection, immutable/centralized audit export, backup/restore and disaster-recovery drills; protect local state and filesystem ACLs; add multi-approver policy/quorum and risk-tier policies; run independent security review/penetration testing; provide monitoring and incident response. Local SQLite and CLI subject parameters are not a substitute for these controls.
