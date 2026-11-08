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
