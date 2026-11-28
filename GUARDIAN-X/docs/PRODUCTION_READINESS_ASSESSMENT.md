# Production readiness architecture assessment (PRD-01)

**Date:** 2026-11-28
**Basis:** source code at the current feature branch, not roadmap intent. Each
gap names the component, severity, accountable owner role and exit criteria.

**Status:** the assessment itself is complete (PRD-01); production acceptance
(PRD-06) is **not** granted.

## 1. Components and trust zones

```text
 Zone 0  Operator workstation (trusted-ish)
   CLI (ogx.*)  ──► state dir (OGX_HOME: registry, identity, audit, queues)
   Dashboard / API (loopback) ──► SQLite enterprise store + local KMS file

 Zone 1  Local project (untrusted input, contained writes)
   scanner, patch engine (exact-match, hash preconditions, containment)
   sandbox (Docker, digest-pinned, no network)  ──► test execution

 Zone 2  Remote providers (untrusted responses)
   platform.transport (HTTPS, pinned host, bounded) ──► 7 Git providers,
   GitHub Codespaces host

 Zone 3  Remote execution hosts (semi-trusted, read-only)
   SSH (pinned host key), Docker CLI, Kubernetes (read-only, namespace scoped)

 Zone 4  Enterprise data plane (single node, no HA)
   SQLite store: orgs, memberships, projects, audit, governance, approvals,
   service accounts/tokens, tenant secrets, idempotency
```

Data flows: project files → scanner → findings → patch plan → approval →
write → tests (sandbox) → review/evolution → audit. Provider/host responses →
bounded transport → adapter → normalized metadata → registry/audit. API →
service token → policy decision → store query → JSON. Secrets → AES-class AEAD
(ChaCha20-Poly1305) → ciphertext in SQLite; key material in a 0600 file.

Single points of failure: the workstation (all local state), the SQLite file
(single writer), the local key file, the Docker daemon (test execution),
`OGX_GITHUB_TOKEN` (GitHub host and Codespaces).

## 2. Gap matrix

| Area | Current state | Gap | Severity | Owner | Exit criteria |
| --- | --- | --- | --- | --- | --- |
| Authentication (ENT-03) | service tokens only; no human login | no OIDC/SAML/MFA, sessions, logout, CSRF for cookie flows | **Critical** for multi-user production | Platform owner + IdP admin | validated OIDC flow with issuer/audience/nonce/PKCE, JWKS rotation, MFA policy, session revocation, replay tests |
| Persistence (ENT-02) | SQLite, WAL, single writer | no PostgreSQL, migrations framework, pool, multi-node concurrency | **High** | Backend owner | storage abstraction + PG schema/migrations, concurrent claim tests, backup/restore on PG |
| Authorization (ENT-04/UI-06) | implemented matrix + service scopes + tenant binding | no custom-role authoring; no UI beyond the dashboard's role-aware reads | Medium | Security owner | policy extension API, deny-parity tests across every surface, break-glass runbook |
| Tenant isolation (ENT-05) | tenant column on every query + policy tenant check | service identity lifecycle lacks rotation automation; no deletion/retention flow | High | Backend owner | scheduled rotation, tenant deletion/retention with audit proof |
| Encryption/KMS (ENT-06) | ChaCha20-Poly1305 envelope + local key file, rotation/revocation | no managed KMS, no HSM, pure-Python AEAD not constant-time, no outage test | **High** | Security owner | managed KMS provider, key outage/recovery drill, key-policy review |
| API (ENT-08/UI-04) | v1 REST, bearer auth, idempotency, rate limit, bounds | no OpenAPI, no deprecation policy, no TLS/HA, no pagination cursors beyond offset | Medium | API owner | OpenAPI document, versioning policy, reverse-proxy TLS deployment, load test |
| Dashboard (UI-05) | six views, strict CSP, token in memory | not browser-verified; no E2E; no accessibility audit | Medium | Frontend owner | Playwright E2E + axe scan + screenshot evidence |
| Observability (ENT-09/PRD-03) | structured audit + local logs, queue stats | no metrics endpoint, traces, alert rules, SLOs, runbooks | **High** | SRE owner | metrics/traces, alert rules validated by failure injection, SLOs, incident runbooks |
| Backup/DR (ENT-10/PRD-04) | registry backup/recover only | no consistent encrypted snapshot, retention, restore drill, RPO/RTO | **High** | SRE owner | owner-approved RPO/RTO, encrypted off-host backups, documented restore drill evidence |
| Sandbox execution (SEC-12/AI-05/ORC-13) | hardened Docker invocation, mocked tests | no live daemon run, no ORC-13 E2E with pinned image | **High** | Platform owner | real Docker host evidence with daemon version, image digest, cleanup log |
| Plugin isolation (PLG-05) | Docker + restricted process backend, default off | no live container proof; third-party execution disabled | Medium | Security owner | live disposable-container run; owner decision to enable |
| Relocation (ID-10..12/TST-11) | journaled engine, simulated media failures | no physical second volume/USB/machine | Medium | Owner + hardware | real relocation evidence with before/after hashes |
| Providers (PROV-05/TST-07) | 7 adapters, mocked contract tests | no live least-privilege credential per provider | Medium | Provider owner | per-provider live read test with recorded scope and rate-limit behaviour |
| Hosts (HOST-08/09/10/11/14) | SSH/Docker/K8s/Codespaces adapters, mocked | no live targets or credentials | Medium | Platform owner | live read-only probes with versions and redaction evidence |
| Independent review (SEC-14/PRD-02) | none | no external review of patch engine, sandbox, plugins, identity, transport, API | **Critical** | Owner | commissioned review, findings tracked by severity, retest |
| Release (REL-04/REL-07) | deterministic archives, unsigned provenance | no production signing key, no published release | **High** | Owner | authorized signing, checksums, SBOM, provenance, rollback policy |
| Upgrade/migration (PRD-05) | additive registry migrations + tests | no cross-version matrix, no interrupted-upgrade drill | Medium | Backend owner | upgrade matrix 0.7→0.8, rollback drill, large-state migration test |
| Capacity (PRF-05/06) | 1,021-file benchmark, queue stats | no monorepo/large-queue benchmark, no published capacity | Low | Performance owner | measured workload at target size, documented limits |

## 3. Security surface summary

* **Highest-risk components:** patch engine (writes source), sandbox (runs
  code), plugin runtime (third-party code), credential/transport layer
  (tokens), API (authenticated surface), KMS/key file (secret custody).
* **Controls that exist and are tested:** path containment and hash
  preconditions, digest-pinned sandbox image and resource caps, approval-gated
  writes, Ed25519 publisher trust, version-bound permissions, token hashing,
  tenant checks, AEAD with AAD binding, request bounds and rate limiting,
  hash-chained audit.
* **Controls that are documented but not enforced:** independent review, WORM
  audit, seccomp/network allowlisting, OS-level isolation without Docker,
  managed KMS, SSO/MFA.

## 4. Deployment topologies

| Topology | Ready? | Blocking gaps |
| --- | --- | --- |
| Single developer workstation (local only) | usable today | none for local read/scan/patch with review; CLI only |
| Single team server (API + dashboard + SQLite) | **no** | SSO, TLS/HA deployment, observability, backup/DR, independent review |
| Multi-tenant SaaS | **no** | PostgreSQL, SSO/MFA, managed KMS, tenant lifecycle, capacity evidence, HA |

## 5. Verdict

Development work is materially advanced (canonical suite 1182 passed / 23
skipped; drop-in 289 OK), but production acceptance remains blocked by the
**Critical** rows above: no human authentication, no independent security
review, and no live evidence for the sandbox/hosts/providers that the product
advertises. The honest classification remains **DEVELOPMENT INCOMPLETE —
IMPLEMENTATION REMAINS** until those rows are closed or explicitly descoped by
the owner.
