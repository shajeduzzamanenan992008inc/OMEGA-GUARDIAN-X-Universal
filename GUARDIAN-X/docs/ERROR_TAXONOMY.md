# Error Taxonomy

## Overview

This document defines the canonical error taxonomy for all provider integrations in OMEGA GUARDIAN-X Universal. All provider-specific errors are normalized to this taxonomy before being exposed to callers.

## Error Hierarchy

```
ProviderError (base)
├── AuthenticationError (401)
├── PermissionDeniedError (403)
├── NotFoundError (404)
├── ConflictError (409)
├── RateLimitError (429) *
├── ServerError (500-599) *
├── TransportError (network) *
├── MalformedResponseError
├── ResponseTooLargeError
├── InvalidEndpointError
├── CapabilityUnavailableError
│   └── UnsupportedOperationError
├── DuplicateEntityError
└── ApprovalRequiredError
```

* = Retryable

## Error Definitions

### ProviderError (Base)

```python
class ProviderError(Exception):
    kind = "provider_error"
    retryable = False
    http_status: int | None = None

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        status: int | None = None,
        correlation_id: str | None = None,
        capability: str | None = None,
        detail: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        ...
```

**Common Fields:**
- `kind`: Error category identifier
- `message`: Redacted human-readable message
- `retryable`: Whether operation can be retried
- `provider`: Provider identifier
- `status`: HTTP status code (if applicable)
- `correlation_id`: Request correlation ID
- `capability`: Capability that failed
- `detail`: Additional context (redacted)
- `retry_after_seconds`: Seconds to wait before retry (if applicable)

### AuthenticationError (401)

```python
class AuthenticationError(ProviderError):
    kind = "authentication_failed"
    http_status = 401
```

**Triggered when:**
- Invalid or expired token
- Missing authentication header
- Token revoked or malformed

**Remediation:** Re-authenticate with valid credentials

### PermissionDeniedError (403)

```python
class PermissionDeniedError(ProviderError):
    kind = "permission_denied"
    http_status = 403
```

**Triggered when:**
- Valid token but insufficient scope/permissions
- Resource access denied by policy
- Organization-level restrictions

**Remediation:** Request additional permissions or use elevated token

### NotFoundError (404)

```python
class NotFoundError(ProviderError):
    kind = "not_found"
    http_status = 404
```

**Triggered when:**
- Repository, branch, commit, issue, PR doesn't exist
- Invalid owner/repository identifier
- Resource moved or deleted

**Remediation:** Verify resource identifier

### ConflictError (409)

```python
class ConflictError(ProviderError):
    kind = "conflict"
    http_status = 409
```

**Triggered when:**
- Resource already exists (duplicate creation)
- Concurrent modification conflict
- Branch protection rules violated

**Remediation:** Handle conflict resolution or use different identifier

### RateLimitError (429) - RETRYABLE

```python
class RateLimitError(ProviderError):
    kind = "rate_limited"
    retryable = True
    http_status = 429
```

**Triggered when:**
- API rate limit exceeded
- Provider returns `Retry-After` header

**Remediation:** Wait `retry_after_seconds` before retry

### ServerError (5xx) - RETRYABLE

```python
class ServerError(ProviderError):
    kind = "provider_unavailable"
    retryable = True
```

**Triggered when:**
- Provider returns 500-599
- Temporary provider outage

**Remediation:** Exponential backoff retry

### TransportError (Network) - RETRYABLE

```python
class TransportError(ProviderError):
    kind = "connection_failed"
    retryable = True
```

**Triggered when:**
- DNS resolution failure
- Connection timeout
- TLS handshake failure
- Network unreachable

**Remediation:** Retry with backoff

### MalformedResponseError

```python
class MalformedResponseError(ProviderError):
    kind = "malformed_response"
```

**Triggered when:**
- Response is not valid JSON
- Response encoding mismatch
- Unexpected response structure

**Remediation:** Check provider API version compatibility

### ResponseTooLargeError

```python
class ResponseTooLargeError(ProviderError):
    kind = "response_too_large"
```

**Triggered when:**
- Response exceeds `max_response_bytes` limit
- Default: 2 MiB, configurable up to 64 MiB

**Remediation:** Use pagination or request smaller payload

### InvalidEndpointError

```python
class InvalidEndpointError(ProviderError):
    kind = "invalid_endpoint"
```

**Triggered when:**
- API base URL uses HTTP (non-loopback)
- Embedded credentials in URL
- Cloud metadata host detected
- Private IP address detected
- Non-HTTP(S) scheme

**Remediation:** Configure valid HTTPS endpoint

### CapabilityUnavailableError

```python
class CapabilityUnavailableError(ProviderError):
    kind = "capability_unavailable"
```

**Triggered when:**
- Operation requires unsupported capability
- Capability requires authentication but none configured
- Capability requires specific permission

**Remediation:** Check capability matrix before invoking

### UnsupportedOperationError

```python
class UnsupportedOperationError(CapabilityUnavailableError):
    kind = "unsupported_operation"
```

**Triggered when:**
- Provider fundamentally doesn't support the operation
- Not a temporary limitation

### DuplicateEntityError

```python
class DuplicateEntityError(ProviderError):
    kind = "duplicate_entity"
```

**Triggered when:**
- Creating resource that already exists
- Name collision in target namespace

### ApprovalRequiredError

```python
class ApprovalRequiredError(ProviderError):
    kind = "approval_required"
```

**Triggered when:**
- Write operation attempted without governance approval
- Operation requires explicit confirmation

## Error Mapping

The `error_for_status()` function maps HTTP status codes:

```python
def error_for_status(
    status: int,
    *,
    provider: str | None,
    correlation_id: str | None = None,
    detail: str | None = None,
    retry_after: float | None = None,
) -> ProviderError:
```

| Status | Error Type |
|--------|------------|
| 400, 422 | ProviderError |
| 401 | AuthenticationError |
| 403 | PermissionDeniedError |
| 404 | NotFoundError |
| 409 | ConflictError |
| 429 | RateLimitError |
| 500-599 | ServerError |
| Other | ProviderError |

## Integration Error Taxonomy

For contract verification failures:

| Error | Category | Description |
|-------|----------|-------------|
| `ContractViolationError` | Contract | Universal contract violated |
| `CapabilityMismatchError` | Contract | Declared capability ≠ implementation |
| `NormalizationError` | Data | Response cannot be normalized |
| `SecurityViolationError` | Security | Security contract violated |
| `AuthenticationContractError` | Security | Auth contract violated |
| `AuthorizationContractError` | Security | AuthZ contract violated |
| `PaginationContractError` | Contract | Pagination contract violated |
| `MetadataContractError` | Contract | Metadata contract violated |
| `UnsupportedCapabilityContractError` | Contract | Unsupported capability incorrectly reported |
| `ReadOnlyContractViolationError` | Security | Read-only op attempted write |
| `CredentialLeakageError` | Security | Credentials leaked in logs/errors |

## Error Handling Best Practices

### 1. Always Use Normalized Errors

```python
from ogx.platform.errors import error_for_status

try:
    response = transport.send(...)
except ProviderError as e:
    # e is already normalized with redacted messages
    log.error("Provider error", extra=e.to_dict())
    raise
```

### 2. Check Retryability

```python
if error.retryable:
    wait = error.retry_after_seconds or exponential_backoff(attempt)
    time.sleep(wait)
    return retry()
```

### 3. Use Correlation IDs

```python
# Every request gets correlation ID
correlation_id = transport.last_correlation_id
# Include in all log entries
log.info("Request failed", extra={"correlation_id": correlation_id})
```

### 4. Never Log Raw Errors

```python
# WRONG
log.error(f"API error: {response.text}")  # May contain tokens!

# RIGHT
log.error("Provider error", extra=error.to_dict())  # Redacted
```

### 5. Redact Details

```python
from ogx.platform.security import redact

detail = redact(response.text)[:800]  # Bounded and redacted
```

## Error Serialization

All errors serialize to a safe dictionary:

```python
error.to_dict()
# Returns:
{
    "error": "authentication_failed",
    "message": "github rejected the credential (HTTP 401).",
    "retryable": false,
    "provider": "github",
    "status": 401,
    "correlation_id": "abc123",
    "capability": "repository_metadata",
    "detail": "invalid token"
}
```

## Testing Error Handling

Contract tests verify:
1. All HTTP statuses map to correct error types
2. Messages are redacted (no tokens/secrets)
3. Required fields present in serialization
4. Retryability correctly set
5. Correlation IDs preserved