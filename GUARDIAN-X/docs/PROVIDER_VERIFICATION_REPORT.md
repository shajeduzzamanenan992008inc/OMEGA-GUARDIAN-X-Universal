# Provider Verification Report (Phase 5)

Date: 2026-11-24
Registry: 28 providers — **7 verified, 21 descriptor-only**
Credentials present during verification: **0**
Live API calls: **none**

## 1. New shared contract suite

`tests/test_provider_contract.py` — **30 tests, 30 passed**, parametrized over
the seven verified adapters. No network is permitted: a sentinel
`request_fn` raises if any adapter tries to call out.

Per provider:

1. identity payload (`provider_id`, `kind=git_hosting`,
   `implementation_status=verified`, HTTPS API base);
2. unauthenticated auth status is truthful (`configured=False`,
   `authenticated=False`) and names its required environment variables;
3. `get_repository` without a credential raises
   `CapabilityUnavailableError` **before** network I/O;
4. registry descriptor matches the adapter and reports
   `requires_auth` for repository metadata;
5. descriptor-only providers never report a domain capability as supported.

Plus: an unapproved pull-request write raises `ApprovalRequiredError`.

## 2. Provider status

| Provider | Implementation | Evidence |
| --- | --- | --- |
| `github` | VERIFIED (mock) | contract suite + adapter tests |
| `gitlab` | VERIFIED (mock) | contract suite + adapter tests |
| `bitbucket` | VERIFIED (mock) | contract suite + adapter tests |
| `gitea` | VERIFIED (mock) | contract suite + adapter tests |
| `forgejo` | VERIFIED (mock) | contract suite + adapter tests |
| `azure_devops` | VERIFIED (mock) | contract suite + adapter tests; requires org |
| `aws_codecommit` | VERIFIED (mock) | contract suite + SigV4 tests |
| `sourcehut` | DESCRIPTOR_ONLY | registry; no domain operation |
| 20 family providers (CI/CD, cloud, workspace, registries, PM) | DESCRIPTOR_ONLY | identity/auth/API-version only |

“Verified” here means **code exists and is covered by deterministic mocked
tests**. It does not mean live-verified.

## 3. Transport contract exercised

- response size cap (`tests/test_transport_bounds.py`);
- redirect refusal (`tests/test_network_policy.py`);
- bounded retries and no retry for writes (`tests/test_platform_engine.py`);
- pagination host pinning via `validate_request_url`;
- credential redaction in error bodies;
- resolved-address validation and IP pinning for the default transport.

## 4. Not verified / blocked

- Live authentication, scopes, rate limits and provider error payloads.
- Pagination behaviour against a real cursor chain.
- Webhook signature verification (not implemented).
- OAuth device/refresh flows (not implemented).
- Credential rotation/revocation (not implemented).
- Enterprise/self-hosted instance variants beyond base-URL configuration.

No live provider run was possible: no credentials, no approved test accounts,
and the task forbids using real credentials without authorization.

## 5. Reproduce

```bash
cd GUARDIAN-X
python -m pytest -q tests/test_provider_contract.py
python -m ogx providers list
python -m ogx platform status
```
