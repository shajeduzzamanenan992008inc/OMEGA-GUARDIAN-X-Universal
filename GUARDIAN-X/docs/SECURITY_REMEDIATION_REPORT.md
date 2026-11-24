# Security Remediation Report (Phase 2)

Date: 2026-11-24
Scope: actual implementation inspected and changed, not documentation alone.

## 1. DNS rebinding / SSRF (High) — FIXED in the transport path

### Before

`validate_api_base` checked the URL hostname (and literal IPs), but the default
request path handed the URL to `urllib`, which resolved DNS independently at
connect time. A hostname that passed validation could therefore resolve to a
private or loopback address when the socket was opened (TOCTOU/rebinding).

### After

- `ogx.platform.security.resolve_pinned_address()` resolves the host once with
  `socket.getaddrinfo`, validates **every** answer (IPv4 and IPv6), and returns
  one validated address.
- Policy: private, loopback, link-local, reserved, multicast and unspecified
  addresses are refused. A mixed public/private answer set is rejected entirely
  (fail closed). Loopback is allowed only for a **literal** loopback hostname
  (`localhost`, `127.0.0.1`, `::1`) and only when loopback was explicitly
  enabled; a public name resolving to loopback is rejected.
- `ogx.platform.transport` dials the validated address through
  `_PinnedHTTPConnection` / `_PinnedHTTPSConnection`. The HTTPS connection
  wraps the socket with `server_hostname=<original host>`, so SNI and
  certificate hostname verification still apply to the configured hostname.
- Resolution failure fails closed with `InvalidEndpointError`.
- Redirects are refused explicitly at the transport layer (3xx raises), even if
  the caller supplied a custom `request_fn`. State-changing requests are still
  never retried.
- `validate_api_base` and `validate_request_url` remain in force for URL shape,
  scheme, credential-in-URL and host pinning.

### Behaviour change

The transport now connects directly. `HTTP_PROXY`/`HTTPS_PROXY` environment
variables are **not** honored; there is no proxy support. This avoids a proxy as
an unvalidated resolution path but means enterprise egress proxies require a
dedicated, reviewed integration (not implemented).

### Tests (11, all passing)

`tests/test_network_policy.py`: public pinning, private refusal, mixed-answer
fail-closed, metadata/link-local refusal, public-name-to-loopback refusal,
literal-loopback opt-in, IPv6 loopback/ULA, private opt-in, resolution failure,
classification helpers, redirect refusal without retry.

## 2. Audit integrity (Medium) — tamper-evident, not immutable

- `ogx/platform/audit.py` appends each record with
  `integrity = {algorithm, seq, prev_hash, hash}` where
  `hash = sha256(prev_hash + "\n" + canonical(record body))`.
- `verify_chain()` recomputes the chain and reports sequence errors, previous-hash
  mismatches and content-hash mismatches, plus chained/unchained counts.
- `append_audit` now writes chained records; `verify_audit` is the public
  verification API.
- CLI: `ogx platform audit` prints the verdict; `--json` is machine-readable; a
  tampered log exits non-zero.
- Legacy (pre-chain) lines are counted as `unchained` and restart the chain;
  they are not integrity evidence.

### Tests (8 passing, 1 skip)

`tests/test_audit_chain.py`: linkage, content tampering, reordering, legacy
records, missing file, secret redaction, malformed lines, and an explicit test
that tail truncation is only detectable with an externally stored head.

### Explicit limitations

- A hash chain does not stop an actor who can rewrite the whole file and
  recompute every hash.
- Truncating the newest records is undetectable without an external head; the
  head is printed but not published/anchored.
- No signing key, no trusted external store, no WORM storage.
- Retention/export policy is not implemented; records grow without rotation.

Do **not** describe this as enterprise-grade immutable audit storage.

## 3. Secret protection — verified

- Existing provider redaction covers GitHub/GitLab/Bitbucket/AWS token shapes,
  `Authorization`/`Bearer`/`private-token` headers and mapping keys.
- Audit records are redacted before hashing and writing; a test appends a
  `ghp_…` value and asserts it does not appear on disk.
- Credentials remain environment-only, resolved per provider, never loaded from
  the analyzed repository and never written to the registry.
- No real credential was used in any test.

## 4. Approval and permission boundaries — verified

- `plan_pull_request` performs no I/O and rejects protected head branches,
  invalid branch names, oversized payloads and unavailable capabilities.
- `execute_pull_request` refuses without `approved=True`; destructive operations
  have no implementation.
- New contract test asserts an unapproved write raises `ApprovalRequiredError`
  without network access.
- Provider contract tests assert every verified adapter raises
  `CapabilityUnavailableError` before network I/O when unauthenticated.

## 5. Residual security risks

| Risk | Status |
| --- | --- |
| DNS rebinding/TOCTOU | Mitigated by resolve-once + pinned dial; no address allowlist |
| Proxy/egress policy | Not implemented; environment proxies ignored |
| TLS trust | Standard CA verification; no certificate pinning |
| Audit immutability | Hash chain only; no signing/external anchor |
| Audit retention/rotation | Not implemented |
| Credential storage/rotation | Environment only; no keychain/Vault/KMS |
| Sandbox | Docker profile exists; not executed in this environment |
| Provider live behaviour | Unverified; mocked/static tests only |
| Scanner accuracy | Heuristic; clean scan is not a security certificate |
