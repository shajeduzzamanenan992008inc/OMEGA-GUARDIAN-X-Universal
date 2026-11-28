# Enterprise authorization and REST API (ENT-04, ENT-05, ENT-08, UI-04, UI-05, UI-06)

**Status:** implemented and tested offline. Human SSO is **not** implemented
(ENT-03 remains unsupported); the API authenticates machine identities only.

**Review date:** 2026-11-28

## 1. What exists

| Capability | Module | State |
| --- | --- | --- |
| One deny-by-default policy engine | `src/ogx/enterprise/authz.py` | implemented, tested |
| Role/permission matrix with inheritance | `authz.ROLE_PERMISSIONS` | implemented, tested |
| Tenant-bound service identities | `src/ogx/enterprise/service_identity.py` | implemented, tested |
| Scoped, expiring, revocable bearer tokens | `service_identity.py` + `store.py` | implemented, tested |
| Authenticated REST API `v1` | `src/ogx/api/server.py` | implemented, tested |
| Dashboard (same-origin assets) | `src/ogx/api/dashboard.py` | implemented, served and tested; no browser E2E |
| Cross-interface decision parity | `authz.PolicyEngine` used by CLI, API and store helpers | implemented, tested |
| Audit of every decision | `PolicyEngine(audit_sink=...)`, `audit_events` table | implemented, tested |

Not implemented and not claimed: OIDC/SAML/MFA (ENT-03), interactive sessions,
cookies, RBAC custom-role authoring, OpenAPI document generation, HA or TLS
termination, PostgreSQL persistence (ENT-02).

## 2. Roles and actions

Roles: `owner`, `admin`, `developer`, `auditor`, `viewer`. Actions are the
29 values in `authz.Action` (for example `project.read`, `project.write`,
`workflow.run`, `workflow.approve`, `job.requeue`, `plugin.grant`,
`audit.export`, `secret.read`, `kms.rotate`, `service_account.manage`,
`break_glass`).

`owner` holds every action. `admin` holds everything except
`organization.delete`. `developer` may read and write projects, run workflows
and propose AI changes but cannot approve them, manage members or read audit
exports. `auditor` holds the read surface plus `audit.export`. `viewer` holds
read-only project/host/workflow/job/plugin/governance actions.

Three properties are enforced and tested:

1. **Deny by default.** An unknown action, unknown role, absent principal, or a
   principal with no tenant binding for a tenant-scoped resource is denied with
   a machine-readable rule (`deny.unknown_action`, `deny.unknown_role`,
   `deny.no_principal`, `deny.tenant_missing`).
2. **Explicit deny precedence.** A principal carrying `denied={action}` is
   refused even when its role grants the action (`deny.explicit`).
3. **Scope intersection for services.** A service token's effective permission
   set is `role_permissions ∩ token_scopes`. A token can never exceed its
   account's role (`deny.role`) and cannot escape its own scope list
   (`deny.scope`).

Break-glass is an action, not a bypass: it requires `owner`/`admin`, a reason of
at least 16 characters, and it is written to the audit sink like every other
decision.

## 3. Service identities and tokens

`ServiceIdentityManager` creates a service account inside exactly one
organization with one base role (`admin`, `developer`, `auditor`, `viewer`) and
an explicit scope list that must be a subset of that role. Tokens:

* format `ogxst.<32 hex token id>.<>=32 char secret>`; the secret is generated
  with `secrets.token_urlsafe(32)` and stored **only** as SHA-256;
* returned exactly once, with a warning that it is not recoverable;
* lifetime bounded to 60 s – 30 days, tenant-bound, revocable, and invalidated
  when the account is disabled;
* listed only by fingerprint (`sha256:<16 hex>`), never by value;
* authentication failures are uniform (`invalid_token`) and never echo the
  presented secret or reveal whether an account exists.

Managed through the CLI:

```bash
ogx enterprise service-account-create --org <id> --name ci --role developer \
  --scopes project.read,project.write --actor-subject owner@example.com
ogx enterprise token-issue --org <id> --account <account-id> \
  --actor-subject owner@example.com --ttl 3600
ogx enterprise token-revoke --org <id> --token-id <token-id> \
  --actor-subject owner@example.com
ogx enterprise tokens --org <id> --actor-subject owner@example.com
```

## 4. REST API v1

Start: `ogx serve --host 127.0.0.1 --port 8765` (the server refuses any
non-loopback bind; terminate TLS in a reverse proxy for remote use).

| Method | Path | Action |
| --- | --- | --- |
| GET | `/health`, `/ready` | none (liveness/readiness; no internals) |
| GET | `/api/v1/meta` | none (version, auth scheme, limits) |
| GET | `/api/v1/organizations` | `organization.read` |
| GET | `/api/v1/organizations/{org}` | `organization.read` |
| GET | `/api/v1/organizations/{org}/projects` | `project.read` |
| POST | `/api/v1/organizations/{org}/projects` | `project.write` |
| GET | `/api/v1/organizations/{org}/members` | `member.read` |
| GET | `/api/v1/organizations/{org}/audit` | `audit.read` |
| GET | `/api/v1/organizations/{org}/audit/export` | `audit.export` |
| GET | `/api/v1/organizations/{org}/governance/records` | `governance.read` |
| POST | `/api/v1/organizations/{org}/governance/records` | `governance.write` |
| GET | `/api/v1/hosts`, `/api/v1/hosts/{id}` | `host.read` |
| GET | `/api/v1/jobs/stats` | `job.read` |
| GET | `/api/v1/plugins` | `plugin.read` |
| GET | `/api/v1/policy/matrix` | `policy.read` |
| POST | `/api/v1/authorization/check` | `policy.read` (decision probe) |
| GET | `/`, `/dashboard.css`, `/dashboard.js` | none (assets; data needs a token) |

Behavioural guarantees:

* Tenant scoping is a policy input: the path organization id becomes the
  resource tenant, so a token from tenant A receives `deny.tenant_mismatch`
  (HTTP 403) for tenant B.
* `Authorization: Bearer` is required; a missing token returns 401 with
  `WWW-Authenticate`, an invalid/expired/revoked one returns 401 `invalid_token`.
* Writes require `application/json`, are capped at 64 KiB (`413` otherwise), and
  are capped again by pagination bounds (`limit` 1–200, `offset` ≥ 0).
* `Idempotency-Key` (8–128 chars) replays the stored response; reuse with a
  different body is `409 idempotency_conflict`.
* Responses are `Cache-Control: no-store`; every response carries
  `X-Request-Id`; errors use `{"error": {"code", "message", "request_id"}}`.
* A request carrying a foreign `Origin` is refused (`403 origin_not_allowed`);
  no CORS headers are emitted, so browsers cannot read cross-origin data.
* Rate limiting is per bearer token (hashed key, 120 requests/minute default,
  `429` + `Retry-After`).
* Unexpected exceptions return a generic `500 internal_error` with a request id;
  no traceback, path or credential is echoed.

## 5. Dashboard (UI-05)

`/` serves a dependency-free control plane with six views: overview (projects,
members, hosts, queue), projects, hosts and capabilities, governance records,
audit (with NDJSON export) and the role/action matrix. Data comes exclusively
from the authenticated API, so the UI cannot bypass server-side authorization.
The bearer token is kept in a JavaScript variable only — never
`localStorage`/`sessionStorage`/cookies. Assets are separate files with a strict
CSP (`default-src 'none'; script-src 'self'; style-src 'self'`), `X-Frame-Options:
DENY`, `nosniff` and `Referrer-Policy: no-referrer`.

## 6. Evidence

Commands run 2026-11-28 in `GUARDIAN-X/`:

```bash
python -m pytest tests/test_authz.py tests/test_enterprise_identity.py \
  tests/test_api.py tests/test_crypto_kms.py tests/test_cloud_host.py -q
# 81 passed

python -m pytest -q
# 1182 passed, 23 skipped, 119 subtests passed

ogx authz matrix                       # exit 0
ogx authz check --action project.delete --roles viewer   # DENY, exit 1
ogx authz check --action project.read --roles viewer     # ALLOW, exit 0
```

`tests/test_api.py` exercises real HTTP over a loopback socket: 401 without a
token, tenant mismatch 403, scope denial, idempotent replay and conflict,
governance round-trip, audit export scope, policy parity with the local engine,
foreign Origin, wrong content type, pagination bounds, 404/405 and dashboard
assets/CSP.

## 7. Remaining gaps (honest)

* ENT-03 (OIDC/SAML/MFA) is still unimplemented: no human login exists, so the
  dashboard and API serve machine identities only.
* ENT-02 (PostgreSQL) is unimplemented; the API is single-node SQLite with the
  documented WAL/single-writer limits.
* No OpenAPI document, versioning/deprecation policy document, HA deployment or
  TLS termination is included.
* The dashboard has automated asset/API tests but has not been driven in a
  browser (no screenshot/E2E evidence).
