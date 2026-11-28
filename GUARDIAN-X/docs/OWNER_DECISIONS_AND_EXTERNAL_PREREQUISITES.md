# Owner decisions and external prerequisites

**Date:** 2026-11-28
**Branch:** `feat/production-acceptance-verification`
**Status:** open — nothing in this document is approved by default.

This register exists so that no blocked requirement is left as a bare label. For
every item it records the exact missing input, why the product needs it, which
offline work is already complete, the minimum owner action, and the procedure
that will produce evidence once the input exists.

**No credential, key, token or account is included here, and none may be
invented.** Values are supplied by the owner through environment variables or an
authorized test system, never committed.

## 1. Architecture decisions (D1–D12)

These are the relocation/identity decisions recorded in
`docs/UNIVERSAL_RELOCATION_IDENTITY_DESIGN.md`. The current behaviour is what
the code does today; the recommendation is not a decision.

| ID | Decision | Current behaviour | Recommendation | Security / migration impact | Ratification |
| --- | --- | --- | --- | --- | --- |
| D1 | Authority for host identity | `host.json` is authoritative; the registry mirrors it | Keep | A single authority prevents divergent host records | **NOT RATIFIED** |
| D2 | Project identity authenticity | Integrity hash only; no keyed attestation | Optional keyed attestation behind an approved key-custody model | Adds tamper evidence but requires key lifecycle and escrow | **NOT RATIFIED** |
| D3 | State-layout v2 activation | Opt-in migration only | Keep opt-in | A forced migration would strand existing state | **NOT RATIFIED** |
| D4 | PID on a filesystem copy | Same PID, new WID, conflict check | Keep | Prevents silent forks; copy is not a move | **NOT RATIFIED** |
| D5 | WID on same-volume rename | Retain WID | Keep | Preserves continuity; avoids duplicate projects | **NOT RATIFIED** |
| D6 | Registry schema v3 timing | With the state-layout phase | Keep | Coupling avoids two migrations | **NOT RATIFIED** |
| D7 | Drop-in storage | Atomic JSON indexes | Keep | Crash-safe without a database dependency | **NOT RATIFIED** |
| D8 | Machine-binding sources | OS ID + state-root ID | Keep | Over-binding hurts portability (ID-11/ID-12) | **NOT RATIFIED** |
| D9 | Two PIDs claiming one repository URL | Refuse by default; explicit fork record only | Keep | Prevents accidental identity merge | **NOT RATIFIED** |
| D10 | Repository URL change | Require confirmation | Keep | Blocks silent remote hijack | **NOT RATIFIED** |
| D11 | Default relocation semantics | Copy with the source retained | Keep | A destructive default is unacceptable | **NOT RATIFIED** |
| D12 | Cross-host import contents | Reports/audit first; registry only by explicit adoption | Keep | Prevents trust transfer between hosts | **NOT RATIFIED** |

**Decision needed:** ratify, amend or reject each row. Until then the code keeps
the documented current behaviour and no production claim depends on a
recommendation being accepted.

## 2. External prerequisites

Each row names the requirement IDs it unblocks. The offline implementation and
tests already exist unless the row says otherwise.

| ID | Requirement(s) | Exact missing resource | Why it is required | Offline work already complete | Minimum owner action | Verification after unblocking |
| --- | --- | --- | --- | --- | --- | --- |
| EXT-01 | ENT-02, ENT-07, ENT-10, TST-07 | A reachable PostgreSQL instance and `OGX_TEST_POSTGRES_URL` | Proves the opt-in PostgreSQL store, transactional queue claiming, pooling and migrations against a real server; SQLite tests are not PostgreSQL evidence | `enterprise/database.py`, PostgreSQL jobs path, `tests/test_postgres_backend.py` (4 live tests currently skipped), `docs/POSTGRESQL_BACKEND.md` | Provide a non-production database URL and credentials | `OGX_TEST_POSTGRES_URL=... python -m pytest tests/test_postgres_backend.py -q`, then a backup/restore and migration drill on that server |
| EXT-02 | ENT-03 | An authorized OIDC and/or SAML identity provider, MFA policy, client credentials and redirect URI | Human authentication, sessions, logout/revocation and MFA cannot be verified against documentation alone; ad-hoc crypto validation is forbidden | Service-identity authentication, deny-by-default policy engine and tenant binding exist; no human IdP flow is implemented | Approve the identity-provider choice and the dependency needed for standards-compliant validation; provide a non-production tenant | Authorization-code + PKCE login, state/nonce replay rejection, JWKS rotation, expiry/refresh, logout/revocation and MFA enrolment tests |
| EXT-03 | ENT-06, ENT-10 | A managed KMS/HSM account, key policy and escrow decision | The local 0600 keyring is a development provider; production key custody and recovery are owner decisions | `KMSProvider` seam, AEAD envelope format, rotation/revocation, local keyring, backup sealing | Choose the provider, approve key policy/escrow and provide a non-production key | Encrypt/decrypt through the provider, rotate a key, revoke a key, prove an outage/recovery path, re-verify a sealed backup |
| EXT-04 | SEC-12, AI-05, ORC-13, PLG-05 | A Docker daemon and an approved digest-pinned test image | Container isolation, resource caps, network denial, cleanup and the full inspect→plan→approve→edit→execute→test→review workflow are only mocked today | Hardened sandbox invocation, orchestrator stages, plugin runner and adversarial tests | Provide a Docker host and the image digest, and authorize disposable container execution | `python -m pytest tests/test_sandbox_isolation.py` with a live daemon; run ORC-13 end-to-end and record daemon/image/cleanup evidence |
| EXT-05 | HOST-10 | `kubectl`, a cluster and a read-only kubeconfig with namespace-scoped RBAC | Live Kubernetes read operations, pinned context and TLS enforcement are mocked only | `KubernetesHostAdapter` (read-only), 83 mocked tests, CLI registration | Provide a non-production cluster and least-privilege kubeconfig | `ogx host register --type kubernetes ...`, `host connect`, health/capabilities and a read-only namespace listing |
| EXT-06 | HOST-08 | An authorized SSH target, host key in `known_hosts` and a key | Live remote-host commands and fail-closed host-key checking are mocked only | `SshHostAdapter` with StrictHostKeyChecking/BatchMode, 18 mocked tests | Provide a disposable target and authorize key-based access | `ogx host register --type remote_ssh ...`, connect, bounded read command, wrong-host-key refusal |
| EXT-07 | HOST-11 | A GitHub Codespaces credential and an authorized workspace | The read-only cloud-workspace adapter is mocked only | `CloudWorkspaceHostAdapter`, 12 mocked tests | Provide a least-privilege token for a disposable codespace | `ogx host workspaces`, register/connect and read workspace metadata; confirm mutations stay `unsupported` |
| EXT-08 | ID-13, HOST-06, REL-07 | A GitHub token with the minimum write scope and explicit authorization to modify a non-production repository | The live probe was read-only; branch/file/commit/push/PR gates are tested against a mock transport only | Approval-gated write lifecycle, stale-ref checks, audit trail and 47 tests; live read-only evidence in `docs/evidence/github-live-2026-11-28.json` | Authorize a disposable repository and provide a scoped token; confirm that pushing to the product repository is out of scope | Create a branch, write a file, open a PR and verify rollback/conflict handling; record token scope and redaction evidence |
| EXT-09 | ID-12 | A genuinely separate second machine | Configuration portability, identity preservation and dependency verification cannot be proven on one host | Copy/adopt primitives, identity vectors and host-local difference reporting | Provide a second machine or VM with a clean OS account | Install, copy/adopt the project, verify PID/identity hash, run the suite and record differences |
| EXT-10 | ID-11, TST-11 | A real USB/removable volume | The engine was verified across two fixed volumes (C:→D:); removable-media removal, read-only media and reconnect behaviour need real hardware | Relocation journal, resume, read-only and media-failure tests; live cross-volume evidence in `docs/evidence/relocation-cross-drive-2026-11-28.json` | Attach a removable volume and authorize writes to it | Run `_dev/tools/verify_relocation_cross_drive.py --destination-root <usb>` plus removal/reconnect and read-only cases |
| EXT-11 | SEC-14, PRD-02 | An independent reviewer with access to code and evidence | Self-review cannot be presented as independent review | Threat model, evidence bundles, test inventory and gap matrix are prepared | Commission the review and define its scope | Reviewer findings tracked by severity, retested and linked; no internal sign-off substituted |
| EXT-12 | REL-04, REL-07 | Owner-authorized signing key custody and publication approval | Unsigned provenance cannot be presented as a signed release; publication is irreversible externally | Deterministic artifacts, SHA-256 manifests, SBOM, unsigned in-toto/SLSA-style statements and `verify`/`manifest` commands | Approve the signing method/key custody and authorize a release | Build twice, verify identical digests, sign the statement, publish with checksums/SBOM/provenance and a rollback policy |
| EXT-13 | ENT-10, PRD-03, PRD-04, PRD-06 | Approved RPO/RTO, production-like infrastructure and an authorized DR environment | A passing local restore drill is not disaster recovery at scale; production acceptance requires real operational evidence | Encrypted/signed SQLite backups, integrity proof, tamper detection, atomic restore, crash recovery and a live drill in `docs/evidence/backup-restore-drill-2026-11-28.json` | Approve RPO/RTO and provide production-like storage plus an authorized drill window | Backup, destroy, restore and reconcile under the approved RPO/RTO; verify monitoring/alert delivery and record the drill |
| EXT-14 | ID-14 | Ratification of D2 and an approved attestation key model | Signed identity attestation is a design decision, not an implementation detail | Integrity hash and the existing signature-verification boundary | Decide whether keyed attestation is in scope and who custodies keys | Cross-machine attestation issue/verify/rotate/revoke tests |
| EXT-15 | PRD-06 | Production deployment authorization and operational ownership | No production acceptance without a real deployment and an accountable operator | Readiness assessment, deployment topologies and gap matrix | Name the operator, approve the topology and authorize deployment | Deployment, monitoring, incident-response and rollback evidence, then sign-off |

## 3. Environment facts observed in this pass

| Capability | Observed on this host | Consequence |
| --- | --- | --- |
| Second fixed volume `D:` | present | Physical C:→D: relocation verified |
| Removable/USB media | not attached | ID-11 remains partial |
| Docker / kubectl / PostgreSQL | binaries not found | EXT-01/04/05 remain blocked |
| `OGX_GITHUB_TOKEN` | not set at verification time | Write lifecycle and provider writes remain blocked; the recorded read-only probe stands |
| Chromium (Playwright) | available | Dashboard E2E executed with screenshots |
| CPython | 3.15.2 | Hosted CI covers 3.11–3.13 |

## 4. What must not be claimed until these are closed

* That human SSO/MFA, managed KMS, PostgreSQL production use, Docker/Kubernetes/
  SSH live operation, removable-media portability, second-machine migration,
  independent security review or a signed release are verified.
* That the product is production-accepted. The current classification is
  **DEVELOPMENT INCOMPLETE — PRODUCTION ACCEPTANCE BLOCKED**.
