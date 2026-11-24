# Integration Security Model

## Overview

This document describes the security boundaries and guarantees for the OMEGA GUARDIAN-X Universal provider integration system.

## Security Principles

1. **Credential Isolation**: Each provider has exclusive access to its own credentials
2. **No Implicit Trust**: Every external response is untrusted until validated
3. **Explicit Authorization**: Write operations require explicit approval
4. **Auditability**: All integration events are logged with correlation IDs
5. **Fail Secure**: Security failures block operations, never degrade silently

## Credential Management

### Per-Provider Credential Policy

```python
from ogx.platform.security import CredentialPolicy

policy = CredentialPolicy(
    provider_id="github",
    env_vars=("OGX_GITHUB_TOKEN",),
)

# Resolves only from declared environment variables
token = policy.resolve(os.environ)
source = policy.source(os.environ)  # Returns env var name used
```

### Credential Rules

| Rule | Enforcement |
|------|-------------|
| Provider-specific env vars | Required (e.g., `OGX_GITHUB_TOKEN`) |
| No shared credentials | Each provider has isolated `CredentialPolicy` |
| No credentials in URLs | `validate_api_base()` rejects embedded credentials |
| Private storage | Stored in `_credential` (not serialized) |
| Redaction in logs | `redact()` removes known patterns |

### Environment Variable Naming

```
OGX_<PROVIDER>_TOKEN     # Primary token
OGX_<PROVIDER>_USERNAME  # Username (if separate)
OGX_<PROVIDER>_SECRET    # Secret (for app passwords)
```

## Endpoint Security

### HTTPS Enforcement

```python
from ogx.platform.security import validate_api_base

# This validates:
# - Scheme is HTTPS (HTTP only for loopback)
# - No embedded credentials
# - No cloud metadata hosts
# - No private/link-local IPs
# - No query strings or fragments
api_base = validate_api_base(
    "https://api.github.com",
    allow_loopback_http=False,
    allow_private_network=False,
)
```

### Blocked Endpoints

| Category | Examples | Reason |
|----------|----------|--------|
| Cloud metadata | `metadata.google.internal`, `169.254.169.254` | Credential theft risk |
| Private IPs | `10.0.0.1`, `192.168.1.1` | SSRF protection |
| Loopback (non-dev) | `localhost` with HTTP | Credential exposure |
| Credential in URL | `https://user:pass@host` | Credential leakage |

### Custom Endpoint Validation

```python
# Self-hosted GitHub Enterprise
api_base = validate_api_base(
    "https://github.enterprise.local/api",
    allow_private_network=True,  # Opt-in required
)
```

## Transport Security

### Request Security

```python
from ogx.platform.transport import HttpTransport

transport = HttpTransport(
    timeout=15.0,                    # 0-300 seconds
    max_response_bytes=2_000_000,    # 1 KiB - 64 MiB
    max_retries=2,                   # 0-5 retries
    backoff_base=0.25,               # Exponential backoff
    backoff_cap=2.0,                 # Max backoff
    allow_loopback_http=False,       # HTTPS required
    allow_private_network=False,     # No private IPs
)
```

### Security Features

| Feature | Implementation |
|---------|----------------|
| No redirects | Custom `_NoRedirect` handler prevents 3xx credential leakage |
| Host validation | `validate_request_url()` ensures pagination stays on same host |
| Response size limit | Bounded by `max_response_bytes` |
| Request timeout | Enforced per-request |
| Correlation IDs | `X-OGX-Correlation-Id` header on every request |
| No credential reuse | Transport never stores credentials |

### Retry Policy

| Condition | Retryable | Behavior |
|-----------|-----------|----------|
| 429 Rate Limited | ✅ | Respects `Retry-After`, exponential backoff |
| 5xx Server Error | ✅ | Exponential backoff, max retries |
| Network Error | ✅ | Exponential backoff, max retries |
| 4xx Client Error | ❌ | Immediate failure |
| Capability Error | ❌ | Immediate failure |
| Write Operations | ❌ | Never auto-retried |

## Write Operation Security

### Authorization Gates

```python
class ProviderAdapter:
    def create_pull_request(self, ...):
        self.require(Capability.PULL_REQUEST_CREATE)  # Gate 1: Capability
        # Gate 2: Approval workflow
        # Gate 3: Write operation flag
        response = self._transport.send(..., write=True)
```

### Approval Workflow

1. **Capability Check**: Operation requires supported capability
2. **Authentication**: Credential must be configured
3. **Permission**: Token must have required scopes
4. **Governance**: Explicit approval via `ogx governance` workflow
5. **Execution**: Approved request executed once, result recorded

### Write Operation Rules

| Rule | Enforcement |
|------|-------------|
| Separate methods | Read/write are different methods |
| State-changing HTTP | POST/PUT/PATCH/DELETE only |
| No auto-retry | Transport never retries writes |
| Explicit approval | `ogx governance execute` required |
| Single-use | Approval consumed on execution |
| Audit trail | All writes logged with correlation ID |

## Secret Redaction

### Automatic Redaction

```python
from ogx.platform.security import redact, redact_mapping

# String redaction
safe = redact("Authorization: Bearer ghp_abc123...")
# "Authorization: Bearer [REDACTED]"

# Mapping redaction
safe_headers = redact_mapping(headers, secrets=["my-token"])
# {"Authorization": "[REDACTED]", "Content-Type": "application/json"}
```

### Redacted Patterns

| Pattern | Example |
|---------|---------|
| GitHub tokens | `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_` |
| GitLab tokens | `glpat-` |
| Slack tokens | `xoxb-`, `xoxp-`, `xoxa-`, `xoxs-`, `xoxr-` |
| AWS keys | `AKIA...`, `ASIA...` |
| Bearer tokens | `Bearer <token>` |
| Basic auth | `Authorization: Basic ...` |
| Generic | `token=...`, `password=...`, `secret=...` |

### Error Message Redaction

```python
from ogx.platform.errors import error_for_status
from ogx.platform.security import redact

try:
    response = transport.send(...)
except Exception as e:
    detail = redact(response.text)[:800]  # Bounded + redacted
    error = error_for_status(response.status, provider="github", detail=detail)
    # error.to_dict() contains no secrets
```

## Audit Logging

### Event Types

```python
from ogx.integration.reports import IntegrationAuditEvent

event = IntegrationAuditEvent(
    event_type=IntegrationAuditEvent.PROVIDER_CONNECTED,
    provider_id="github",
    correlation_id="abc123",
    details={
        "api_base": "https://api.github.com",
        "authenticated": True,
    },
)
```

### Event Categories

| Event Type | Description | Security Level |
|------------|-------------|----------------|
| `provider_connected` | Provider initialized | INFO |
| `authentication_verified` | Credential validated | INFO |
| `authentication_failed` | Invalid credential | WARNING |
| `capability_checked` | Capability validated | DEBUG |
| `repository_discovered` | Repo found | INFO |
| `repository_inspected` | Repo metadata retrieved | INFO |
| `operation_approved` | Write approved | INFO |
| `operation_rejected` | Write denied | WARNING |
| `provider_unavailable` | Provider down | WARNING |
| `rate_limit_encountered` | 429 received | INFO |
| `security_violation` | Security boundary crossed | CRITICAL |
| `contract_violation` | Contract check failed | ERROR |

### Safe Logging

```python
from ogx.integration.reports import AuditLogger

logger = AuditLogger()

# Log event (automatically sanitized)
logger.log(event)

# Export for compliance
logger.export("audit-events.json")
```

## Compliance Guarantees

### What We Guarantee

| Guarantee | Mechanism |
|-----------|-----------|
| No credential leakage | Private storage + redaction + correlation IDs |
| No SSRF | `validate_api_base()` + host allowlisting |
| No redirect following | `_NoRedirect` handler |
| No credential reuse | Per-provider `CredentialPolicy` |
| No silent auth failures | Explicit `auth_status()` + `require()` |
| No auto-write | Write operations gated by approval |
| Bounded resources | Timeout + response size + retry limits |
| Audit trail | Correlation IDs + structured events |

### What We Don't Guarantee

| Limitation | Mitigation |
|------------|------------|
| Provider API security | Use `HTTPS` + validate provider |
| Token scope sufficiency | Document `required_permissions` |
| Provider availability | Health checks + circuit breaker pattern |
| Data integrity | Use `sha` verification for files |
| Rate limit accuracy | Provider-dependent, respect `Retry-After` |

## Testing Security Contracts

### Running Security Validation

```python
from ogx.integration.security import SecurityValidator

validator = SecurityValidator()
result = validator.validate_adapter(adapter)

# Check for critical security issues
for check in result.checks:
    if not check[1] and check[3] == "critical":
        raise SecurityViolationError(f"Security check failed: {check[0]}")
```

### Key Security Tests

| Test | Purpose |
|------|---------|
| `test_https_enforcement` | HTTPS required for all non-loopback endpoints |
| `test_credential_isolation` | Credentials not shared between providers |
| `test_secret_redaction` | Tokens redacted in logs/errors |
| `test_ssrf_protection` | Metadata/private IPs rejected |
| `test_response_limits` | Response size bounded |
| `test_timeout_enforcement` | Requests timeout |
| `test_error_serialization_safe` | No secrets in error messages |
| `test_no_credential_leakage` | Credentials not in str/repr |
| `test_write_authorization` | Write ops check capability + approval |
| `test_host_allowlisting` | Cross-host redirects blocked |

## Security Incident Response

### If Credentials Are Leaked

1. **Rotate immediately**: Revoke leaked token at provider
2. **Audit logs**: Check `AuditLogger` events for exposure scope
3. **Update environment**: Set new `OGX_<PROVIDER>_TOKEN`
4. **Re-verify**: Run `SecurityValidator` against all adapters

### If SSRF Attempted

1. **Block endpoint**: Add to deny list if not already blocked
2. **Audit**: Check `security_violation` events
3. **Review**: Validate `validate_api_base()` configuration

### If Unauthorized Write Detected

1. **Revoke approval**: Check `governance` approval workflow
2. **Audit**: Check `operation_rejected` events
3. **Verify**: Confirm capability gates and approval workflow