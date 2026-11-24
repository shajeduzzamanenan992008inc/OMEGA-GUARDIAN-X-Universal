# OGX Security Model

## Default trust posture

Target repositories and all content originating from them are untrusted. OGX does not run project files during a normal scan. The default mode is read-only and report output is stored outside the repository.

## Protected assets

Source code, credentials, private repository data, customer data, audit events, provider tokens, signing keys, organization membership, remote Git state and production environments.

## Threat actors and risks

- Malicious repository content or prompt-injection text attempting to override agent rules.
- Malicious/compromised plugins, skills or AI agents.
- Secret leakage through scanner evidence, logs, reports or PR descriptions.
- Path traversal, symlink escape, zip-slip or ownership-manifest tampering.
- Malicious build/test scripts attempting host or network access.
- Compromised Git provider token or overly broad OAuth scopes.
- Cross-tenant data access in future SaaS services.
- Supply-chain attacks through dependencies, container images or release artifacts.

## Controls in 0.4.0a1

- Read-only target scanning and external OGX state.
- Bounded file count/size and symlink avoidance.
- Secret value redaction in findings.
- Explicit confirmation for integration, clone, branch creation, push and PR/MR creation.
- Main/master push refusal.
- Git provider tokens read from environment; never saved by OGX.
- Skills are parsed as declarative manifests; no third-party code is executed.
- Sandbox runner requires a pre-pulled image digest; no implicit image pull; snapshot copy excludes common credentials and dependency directories; network disabled; read-only container root; capabilities dropped; no-new-privileges; process/memory/CPU limits; disposable snapshot removed after run.
- Tenant store queries are organization-scoped and membership-gated in the local prototype.

## Sandbox caveat

The restricted Docker container is not a formally verified VM. Docker daemon, host kernel, Docker Desktop/daemon configuration and host user permissions remain trusted. Do not mount the Docker socket, host home directory, credentials or other repositories. For hostile samples, prefer a disposable virtual machine with a patched host and no secrets.

## Not yet implemented

Full OS-level sandboxing for Windows without Docker, seccomp profile management, network allowlisting, live CVE feeds, full data-flow SAST, DAST scope engine, signed plugin verification, secrets vault integration, enterprise SSO, production tenant isolation, encryption key rotation, immutable audit storage and independent penetration testing.

## Reporting rule

A pattern match is a lead, not proof. Findings must record rule, severity, confidence, location and evidence. OGX must redact values before persistence and say when analysis was incomplete. No report may claim that a project is secure or certified based solely on a clean heuristic scan.

## Provider engine controls (Milestone H)

- Credentials are read per provider from its own environment variables; a token
  is never reused across providers, embedded in a URL, written to the registry,
  or loaded from the analyzed project.
- API bases must pass `validate_api_base`: HTTPS by default, no userinfo,
  refusal of loopback/private/link-local/cloud-metadata hosts unless explicitly
  opted in. Request and pagination hosts are pinned to the configured API host.
- HTTP redirects are refused. Responses are size-capped. Read retries are
  bounded and honor `Retry-After`; state-changing requests are never retried.
- Provider error bodies are redacted (token shapes, `Authorization`, `Bearer`)
  before display or persistence.
- Every remote write requires a validated plan, a capability and permission
  check, explicit approval, and an audit event at
  `<state_dir>/platform/audit.jsonl`.
- Destructive operations (delete, merge, protected-branch push, permission
  change, deployment) are not implemented.
- A capability that is not implemented reports `unsupported` or
  `requires_auth`; it can never be reported as a successful operation.

### Still not implemented for providers

Vault/KMS-backed credentials, OAuth device or refresh flows, webhook signature
verification, per-tenant provider quotas, and live production credential
rotation.

## Release-review hardening (2026-11-24)

- **Resolved-address enforcement.** The default transport resolves the provider
  host once, validates every IPv4/IPv6 answer, and dials the validated address.
  Private, loopback, link-local, reserved, multicast and unspecified answers are
  refused unless the operator explicitly opts in (loopback only for a literal
  loopback hostname). Mixed public/private answers fail closed. The original
  hostname is still used for SNI and certificate verification. This closes the
  hostname-only validation / connect-time resolution gap (DNS rebinding).
- **Redirects** are refused at the transport layer, including for custom request
  functions.
- **No proxy support.** `HTTP_PROXY`/`HTTPS_PROXY` are ignored by the pinned
  transport, deliberately avoiding an unvalidated resolution path. Enterprise
  proxies require a dedicated reviewed integration.
- **Tamper-evident audit.** Platform audit records are hash-chained
  (`seq`, `prev_hash`, `sha256`). `ogx platform audit` verifies the chain and
  exits non-zero on corruption.

Limits: the chain is not signing, not WORM storage and not externally anchored;
rewriting the entire file or truncating the newest records is not prevented.
There is still no address allowlist, certificate pinning or credential vault.
