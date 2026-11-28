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

## Controlled change and workflow controls (0.8.0a1)

- **Patch engine.** Every change requires an immutable plan with an exact,
  single-match replacement per hunk, a SHA-256 precondition for each file and
  project containment checked on the unresolved path before resolution. A
  symlinked file or parent is refused. The pre-image is backed up outside the
  project; writes are atomic; every transition is appended to the hash-chained
  audit log. Rollback refuses to overwrite a file edited after the patch.
- **Workflow engine.** State lives in SQLite under the OGX state directory
  (never the project), in WAL mode, with validated state transitions, per-stage
  checkpoints, recorded approvals for `edit` and `test`, bounded attempts and
  idempotency keys. A run holds a time-based lease; concurrent runners are
  refused while it is valid, and an expired lease resets only the interrupted
  `running` stage so a crash can be resumed. A bounded `--max-stages` call
  releases its lease and returns the workflow to `created`.
- **GitHub host.** Reads use the provider transport (pinned host, refused
  redirects, size caps, credential from `OGX_GITHUB_TOKEN` only). Draft-PR
  creation requires a validated plan and explicit approval; branch creation,
  file update, multi-file commit and fast-forward ref update (push recovery)
  also require a validated plan and explicit approval. Merge and deployment
  remain unsupported. No live credential has been verified.
- **State data.** Workflow rows contain project paths, stage outputs and
  actor identities, not provider tokens. Stage output is capped before storage.

Known limits: the lease is time-based and has no heartbeat, so a stage running
longer than its lease could be reclaimed; choose a lease above the longest
stage. The audit chain is tamper-evident but not signed, WORM or externally
anchored. The patch engine prevents path escape but is not an OS sandbox.

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

## Docker host adapter (HOST-09) security notes

The Docker host adapter (src/ogx/hosts/adapters/docker_runtime.py) drives the
local Docker CLI with `shell=False`, bounded timeouts/output, identifier
validation, path-traversal rejection and redacted errors. It does not mount the
Docker socket or host root, does not enable privileged containers, and does not
implement mutating operations (start/stop/rm/build/pull/exec/mounts). Docker
daemon access itself can grant substantial host privileges; treat any reachable
daemon as a high-trust boundary and do not expose it to untrusted analyzed
code. Live verification of the adapter is blocked in this environment.

## Threat model (SEC-15)

This section is the maintained threat model for 0.8.0a1. It is an internal
engineering artefact: it maps every material threat to a control and, where a
control exists, to the test that exercises it. It is **not** an independent
security review (SEC-14 remains blocked on an external reviewer).

### Trust boundaries

| # | Boundary | Trusted side | Untrusted side |
| --- | --- | --- | --- |
| TB-1 | Repository content | OGX process | files, names, README text, prompts, configs inside a target repository |
| TB-2 | Local host / filesystem | user-owned OS | project files, symlinks, mounts, other users' processes |
| TB-3 | Provider APIs (GitHub and six others) | OGX transport with validated DNS and TLS | provider responses, repository metadata, webhook payloads |
| TB-4 | Host adapters (SSH / Docker / Kubernetes) | local client binaries and kubeconfig/docker config | remote hosts, clusters, container images, daemon responses |
| TB-5 | Container sandbox | Docker daemon + host kernel | project test code executing inside the container |
| TB-6 | Plugin / skill runtime | signed and permissioned manifests | third-party plugin code and manifests |
| TB-7 | Identity and relocation | owner-approved identity record | copied state directories, USB media, second machines, Git remotes |
| TB-8 | Enterprise data plane | authenticated tenant context | API clients, dashboard sessions, queue payloads, worker input |
| TB-9 | Release pipeline | owner-approved signing keys | build environment, dependencies, artefact consumers |
| TB-10 | Cross-process host session | lease holder with current fencing token | other local processes, crashed or partitioned holders |
| TB-11 | Enterprise API and service identity | holder of a scoped bearer token; HTTP client | remote service accounts, the API socket, request bodies and query parameters | Once authenticated, the token is the actor. There is no human session and no cookie, so no ambient credential exists. |
| TB-12 | Key store (local KMS provider) | process user, then filesystem permissions | `OGX_HOME/enterprise/kms-keys.json`, envelope ciphertext in SQLite | Same-user attackers and backups are inside this boundary; only the AEAD protects ciphertext. |

### Attacker capabilities assumed

- Can write arbitrary files, names, branches and prompts inside a target
  repository (TB-1), including `.env`, `AGENTS.md`, test scripts and symlinks.
- Can reach the network from the host (not from the sandbox), and can serve
  malicious provider or registry responses.
- Can obtain a provider token with limited scopes (not the owner's full rights).
- Can run a local process as the same user and race the CLI/worker.
- Cannot read kernel memory, cannot forge TLS certificates, and does not control
  the Docker daemon unless explicitly stated.

### Threats, controls and verification

| ID | Threat | Boundary | Control (enforced in code) | Verification | Residual risk |
| --- | --- | --- | --- | --- | --- |
| T-01 | Path traversal / symlink escape when reading project files | TB-1, TB-2 | resolved-root containment, `allowed_roots`, symlink refusal, bounded sizes | `tests/test_engines.py`, `tests/test_scanner.py`, `tests/test_patching.py`, `tests/test_host_contract.py` | TOCTOU between check and read remains possible on a hostile host |
| T-02 | Secret values leak into reports, logs or audit events | TB-1, TB-3 | value redaction before persistence, redacted host/provider errors, no secret in sandbox env | `tests/test_patching.py`, `tests/test_host_kubernetes_runtime.py`, `tests/test_sandbox_isolation.py` | Heuristic redaction can miss novel formats |
| T-03 | Prompt injection inside scanned content overrides agent rules | TB-1 | content is data: reports and prompts never execute repository instructions | `tests/test_intelligence_integration.py` | Model-level prompt injection cannot be fully eliminated |
| T-04 | DNS rebinding / SSRF to internal services | TB-3 | resolve-once, address validation, redirect refusal, no proxy support | `tests/test_network_policy.py`, `tests/test_transport_bounds.py` | A compromised resolver can still lie once |
| T-05 | Provider token compromise or cross-provider reuse | TB-3 | per-provider env vars, token never logged or persisted, minimum-scope guidance | `tests/test_provider_contract.py`, `tests/test_transport_bounds.py` | A stolen token is usable until revoked; no revocation feed |
| T-06 | Webhook replay or forged delivery | TB-3, TB-8 | HMAC verification over the raw body, delivery-id dedupe, timestamp window | `tests/test_webhooks.py` | Local dedupe store is not a distributed nonce service |
| T-07 | Unsafe Docker host operations (privileged containers, socket mounts, exec) | TB-4 | read-only inspection only; no exec/start/stop/rm/build/pull; argument arrays; bounded output | `tests/test_host_docker_runtime.py` | Daemon access itself is high privilege |
| T-08 | Kubernetes Secret disclosure or cluster-wide reach | TB-4 | Secret metadata only, namespace allowlist, cluster-scope refusal, TLS always verified, pinned context | `tests/test_host_kubernetes_runtime.py` | A cluster admin kubeconfig still grants admin |
| T-09 | SSH host-key substitution / MITM | TB-4 | `StrictHostKeyChecking=yes`, `BatchMode=yes`, known_hosts required, bounded commands | `tests/test_host_ssh_runtime.py` | A poisoned known_hosts entry is trusted |
| T-10 | Malicious project code escapes the sandbox | TB-5 | network none, read-only root, all capabilities dropped, `no-new-privileges`, pids/memory/cpu caps, swap disabled, `ipc none`, non-root user, disposable snapshot, no socket mount | `tests/test_sandbox_isolation.py` | Kernel/Docker daemon exploits are out of scope; not a VM |
| T-11 | Credentials or home directories copied into the sandbox snapshot | TB-5 | excluded dirs/names/suffixes, no symlink following, per-file and total caps | `tests/test_sandbox_isolation.py` | A credential in an unexpected filename inside a copied file is not detected |
| T-12 | Container left running after timeout, cancel or client crash | TB-5 | named container, force-remove on timeout/cancel/finally, `--rm` for the normal path | `tests/test_sandbox_isolation.py` | Daemon unreachable at cleanup time leaves the container until daemon recovery |
| T-13 | Unsigned or over-privileged plugin execution | TB-6 | plugin execution stays disabled; manifests validated, permissions declared, revocation recorded | none yet: no plugin test suite exists; plugin execution remains disabled (see PLG-02..05 rows) | PLG-06 restriction preserved; no third-party execution |
| T-14 | Replayed or stale approval authorises a different change | TB-1, TB-9 | approval bound to plan hash + base revision + expiry; stale approval rejected | `tests/test_patching.py`, `tests/test_github_write_lifecycle.py` | A human can still approve a malicious but correctly hashed plan |
| T-15 | Identity forgery during relocation | TB-7 | signed identity record, hash-chained journal, verification before adoption | `tests/test_identity_vectors.py`, `tests/test_identity_lifecycle_cli.py`, `tests/test_relocation_engine.py` | Owner ratification (ID-15) is a human decision, still open |
| T-16 | Cross-tenant data access | TB-8 | organization-scoped queries, membership checks, tenant-scoped caches/queues in the local prototype | `tests/test_enterprise_governance.py` | Not verified against a real PostgreSQL/IdP deployment |
| T-17 | Concurrent or stale host-session mutation | TB-10 | SQLite lease + monotonic fencing token, validate before connect/health, fail closed on theft, dead-PID recovery | `tests/test_host_leases.py` | Wall-clock expiry, PID reuse and non-locking network filesystems |
| T-18 | Malicious repository content drives an unbounded scan | TB-1 | file/byte caps, excluded directories, bounded output, timeouts | `tests/test_scanner.py`, `tests/test_pipeline.py` | Extremely large single files can still cost I/O |
| T-19 | Supply-chain compromise of a dependency or image | TB-9 | digest-pinned images, no implicit pull, dependency review CI, CodeQL | `.github/workflows/`, `tests/test_sandbox_isolation.py` | No reproducible-build or signing evidence yet (REL-02/04/05) |
| T-20 | Audit log tampering | TB-1, TB-8 | hash-chained audit records, `ogx platform audit` verification | `tests/test_platform_engine.py` | Not signed, not WORM, truncation of newest records undetected |
| T-21 | Stolen service token is replayed | TB-11 | SHA-256 token hashing, 60 s–30 day expiry, immediate revocation, tenant binding, uniform `invalid_token` errors | `tests/test_enterprise_identity.py` | Bearer replay is possible until expiry; HTTPS termination is the operator's reverse proxy |
| T-22 | Token is issued with more power than its role | TB-11 | scope must be a subset of the base role, effective permissions are `role ∩ scopes`, escalation rejected at creation | `tests/test_enterprise_identity.py`, `tests/test_authz.py` | A compromised owner can still mint an owner-scoped token |
| T-23 | API path addresses another tenant's object | TB-11 | path organization becomes the resource tenant; mismatch is denied before any query | `tests/test_api.py` | None known; every scoped query carries `organization_id` |
| T-24 | Oversized, non-JSON or cross-origin API request | TB-11 | 64 KiB body cap, `application/json` required for writes, foreign `Origin` refused, no CORS headers, per-token rate limit | `tests/test_api.py` | No TLS or proxy hardening is provided by OGX |
| T-25 | Idempotency key replay causes a duplicate write | TB-11 | request digest stored with the response; same key + different body is `409` | `tests/test_api.py` | Keys are unique per tenant, not globally |
| T-26 | Local key file is exfiltrated | TB-12 | 0600 file, atomic replace, key material never exposed by `describe()`/API/CLI | `tests/test_crypto_kms.py` | A same-user attacker or a filesystem backup can read the key |
| T-27 | Ciphertext is moved to another tenant, name or purpose | TB-12 | AEAD associated data binds `organization:name`; purpose is checked; wrong AAD fails authentication | `tests/test_crypto_kms.py` | None known; key rotation preserves old versions by design |
| T-28 | Malicious or malformed cloud workspace response | TB-2 | pinned-host HTTPS transport, bounded body, strict shape validation, capability truth, token never echoed | `tests/test_cloud_host.py` | A compromised provider account can lie about its own metadata |

### Abuse cases explicitly out of scope

- A hostile local administrator, kernel compromise or a compromised Docker
  daemon.
- Denial of service from an attacker who can already run arbitrary code as the
  same user.
- Forging TLS certificates or defeating the pinned transport's DNS validation.
- Recovering secrets that were already exfiltrated before OGX ran.


### Documented-but-not-enforced controls

These are described in documentation only and must not be claimed as enforced:

- Independent security review and penetration test (SEC-14, PRD-02).
- Signed release provenance and reproducible builds (REL-04, REL-05).
- Full OS-level isolation on Windows without Docker.
- Seccomp profile management and network allowlisting inside the sandbox.
- Immutable (WORM) or externally anchored audit storage.
- Live enterprise SSO/MFA (ENT-03), PostgreSQL persistence (ENT-02) and a
  managed/HSM-backed KMS. RBAC (ENT-04), tenant-scoped service identities
  (ENT-05) and envelope encryption with a local test key provider (ENT-06)
  *are* implemented and tested; see `docs/ENTERPRISE_API_AND_AUTHZ.md` and
  `docs/KMS_AND_ENCRYPTION.md`.

### Maintenance rule

A new trust boundary, a new external input or a new privileged operation
requires a threat-model row before it is merged. A control row without a test
or a documented residual risk is a defect in this document.
