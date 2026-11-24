# Universal Provider Integration Contracts

## Overview

This document defines the canonical contracts that all provider adapters must satisfy to be considered compatible with the OMEGA GUARDIAN-X Universal integration engine.

## Contract Version

Current: **1.0.0**

## Provider Identity Contract

Every provider adapter **must** declare:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider_id` | `str` | Yes | Stable, unique identifier (e.g., "github", "gitlab") |
| `display_name` | `str` | Yes | Human-readable name |
| `version` | `str` | Yes | Adapter version (semver) |
| `api_version` | `str` | Yes | Upstream API version (e.g., "2022-11-28") |
| `kind` | `ProviderKind` | Yes | Adapter family classification |
| `default_api_base` | `str` | Yes | Default API endpoint (must be HTTPS) |
| `credential_env` | `tuple[str, ...]` | Yes | Environment variable names for credentials |
| `self_hosted` | `bool` | No | Supports self-hosted instances |
| `docs_url` | `str` | No | Documentation URL |
| `required_permissions` | `tuple[str, ...]` | No | Required OAuth scopes/permissions |
| `limitations` | `tuple[str, ...]` | No | Known implementation limitations |
| `supports_pagination` | `bool` | No | Whether pagination is supported |

## Capability Contract

### Capability States

| State | Description | Availability |
|-------|-------------|--------------|
| `supported` | Fully implemented | ✅ Available |
| `partial` | Partially implemented | ⚠️ Available (with limitations) |
| `unsupported` | Not implemented | ❌ Blocking |
| `requires_auth` | Needs authentication | 🔒 Conditional |
| `requires_permission` | Needs specific permission | 🔒 Conditional |
| `requires_workspace` | Needs workspace context | 🔒 Conditional |
| `not_configured` | Missing configuration | 🔒 Conditional |
| `unavailable` | Temporarily unavailable | ❌ Blocking |
| `failed` | Last attempt failed | ❌ Blocking |

### Capability-to-Method Mapping

Each capability **must** map to exactly one adapter method:

```python
CAPABILITY_METHODS = {
    Capability.REPOSITORY_DISCOVERY: "discover_repositories",
    Capability.REPOSITORY_METADATA: "get_repository",
    Capability.BRANCH_LIST: "list_branches",
    Capability.COMMIT_LIST: "list_commits",
    Capability.COMMIT_INSPECT: "get_commit",
    Capability.FILE_READ: "read_file",
    Capability.TREE_READ: "read_tree",
    Capability.PULL_REQUEST_LIST: "list_pull_requests",
    Capability.PULL_REQUEST_CREATE: "create_pull_request",
    Capability.ISSUE_LIST: "list_issues",
    Capability.ISSUE_CREATE: "create_issue",
    Capability.ISSUE_UPDATE: "update_issue",
    Capability.PERMISSION_READ: "get_permissions",
    Capability.RATE_LIMIT_READ: "rate_limit",
}
```

### Capability Declaration Rules

1. **Supported = Implemented**: A capability reported as `supported` **must** have a custom method implementation (not the base class stub).

2. **Partial = Documented**: A `partial` capability **must** have limitations documented in the `limitations` field.

3. **Unsupported = No False Positives**: An `unsupported` capability **must not** return successful results when invoked.

4. **Write Separation**: Write capabilities (`PULL_REQUEST_CREATE`, `ISSUE_CREATE`, `ISSUE_UPDATE`) **must** be separate methods from read operations.

5. **Authentication Context**: Capabilities requiring auth **must** report `requires_auth` when no credential is configured.

## Normalized Data Model Contract

All provider responses **must** normalize to these canonical shapes:

### RepositoryMetadata
```python
@dataclass(frozen=True)
class RepositoryMetadata:
    repository_id: str
    name: str
    full_name: str
    owner: str
    provider: str
    description: str = ""
    owner_type: str = "unknown"
    default_branch: str = ""
    visibility: str = "unknown"
    archived: bool = False
    disabled: bool = False
    fork: bool = False
    language: Optional[str] = None
    languages: Mapping[str, int] = {}
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    pushed_at: Optional[str] = None
    clone_url: Optional[str] = None
    ssh_url: Optional[str] = None
    html_url: Optional[str] = None
    size_kb: int = 0
    topics: tuple[str, ...] = ()
    license: Optional[str] = None
    permissions: Mapping[str, Any] = {}
    extra: Mapping[str, Any] = {}
```

### Branch
```python
@dataclass(frozen=True)
class Branch:
    name: str
    commit_sha: str
    protected: bool = False
    protected_by: Optional[str] = None
```

### Commit
```python
@dataclass(frozen=True)
class Commit:
    sha: str
    message: str
    author: str = ""
    author_email: str = ""
    authored_at: Optional[str] = None
    committer: str = ""
    parents: tuple[str, ...] = ()
    url: Optional[str] = None
```

### FileContent
```python
@dataclass(frozen=True)
class FileContent:
    path: str
    sha: str
    size: int
    content: str
    encoding: str = "utf-8"
    truncated: bool = False
```

### PullRequest
```python
@dataclass(frozen=True)
class PullRequest:
    pull_request_id: str
    number: Optional[int]
    title: str
    state: str  # "open", "closed", "merged", "draft"
    source_branch: str = ""
    target_branch: str = ""
    url: Optional[str] = None
    author: str = ""
    draft: bool = False
    created_at: Optional[str] = None
    merged: bool = False
    merged_at: Optional[str] = None
```

### Issue
```python
@dataclass(frozen=True)
class Issue:
    issue_id: str
    number: Optional[int]
    title: str
    state: str  # "open", "closed"
    url: Optional[str] = None
    author: str = ""
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None
```

### PermissionSet
```python
@dataclass(frozen=True)
class PermissionSet:
    level: PermissionLevel = PermissionLevel.UNKNOWN
    admin: Optional[bool] = None
    maintain: Optional[bool] = None
    write: Optional[bool] = None
    triage: Optional[bool] = None
    read: Optional[bool] = None
    raw: Mapping[str, Any] = {}
```

### Pagination Contract

All paginated methods **must**:

1. Accept a `limit: int` parameter with a default value
2. Respect the limit (return at most `limit` items)
3. Return a tuple of normalized models
4. Support the declared pagination style (`link`, `page`, `offset`, `body_next`, `continuation`)

### Rate Limit Contract

If `RATE_LIMIT_READ` is supported:
- `rate_limit()` **must** return `RateLimit` with `limit`, `remaining`, `reset_at`
- Transport **must** respect `Retry-After` headers
- Transport **must** implement bounded exponential backoff

### Error Contract

All provider errors **must** normalize to `ProviderError` taxonomy:

| HTTP Status | Error Type | Retryable |
|-------------|------------|-----------|
| 401 | `AuthenticationError` | ❌ |
| 403 | `PermissionDeniedError` | ❌ |
| 404 | `NotFoundError` | ❌ |
| 409 | `ConflictError` | ❌ |
| 429 | `RateLimitError` | ✅ |
| 500-599 | `ServerError` | ✅ |
| Network | `TransportError` | ✅ |
| Capability | `CapabilityUnavailableError` | ❌ |

Error messages **must** be redacted (no secrets, tokens, or credentials).

## Security Contract

### HTTPS Enforcement
- `default_api_base` **must** use HTTPS
- HTTP only permitted for explicit loopback development (`127.0.0.1`, `localhost`)
- Custom endpoints validated via `validate_api_base()`

### Credential Isolation
- Each provider **must** have its own `CredentialPolicy`
- Credentials **must not** be shared between provider instances
- Credential environment variables **must** be provider-prefixed (e.g., `OGX_GITHUB_TOKEN`)

### Secret Redaction
- All logging/serialization **must** use `redact()` and `redact_mapping()`
- No tokens, passwords, or credentials in error messages
- Correlation IDs used instead of credentials for request tracking

### SSRF Protection
- `validate_api_base()` **must** reject:
  - Cloud metadata hosts (`metadata.google.internal`, `169.254.169.254`, etc.)
  - Private/link-local/reserved IP addresses
  - Non-HTTP(S) schemes
  - Embedded credentials in URLs
  - Query strings or fragments in base URLs

### Host Allowlisting
- Transport **must** validate request URLs match expected host
- No redirects followed (prevents credential leakage to other hosts)
- Pagination `next` URLs validated before use

### Response Size Limits
- Transport **must** enforce `max_response_bytes` (1 KiB - 64 MiB)
- Oversized responses raise `ResponseTooLargeError`

### Request Timeout Enforcement
- Transport **must** enforce timeout (0-300 seconds)
- No indefinite blocking on network calls

## Write Operation Contract

Write capabilities (`PULL_REQUEST_CREATE`, `ISSUE_CREATE`, `ISSUE_UPDATE`):

1. **Must** be separate methods from read operations
2. **Must** call `self.require(capability)` at start
3. **Must** use state-changing HTTP methods (POST/PUT/PATCH/DELETE)
4. **Must** require explicit approval via governance workflow
5. **Must not** be auto-retried by transport
6. **Must** return `OperationResult` with `approval_required=True`

## Compatibility Verification

A provider is **compatible** only when:

1. ✅ All identity fields present and valid
2. ✅ All capabilities have valid states
3. ✅ Every `supported` capability has custom implementation
4. ✅ Method signatures match contract
5. ✅ Return types match normalized models
6. ✅ Error handling follows taxonomy
7. ✅ Capability states are consistent
8. ✅ Security contracts pass (HTTPS, isolation, redaction, SSRF, limits, timeouts)
9. ✅ Pagination behavior is consistent
10. ✅ Read/write separation enforced
11. ✅ Authentication/authorization contracts met
12. ✅ No credential leakage
13. ✅ Rate limit handling implemented

## Compatibility Verdicts

| Verdict | Criteria |
|---------|----------|
| `compatible` | All required checks pass, no warnings |
| `compatible_with_limitations` | All required checks pass, but has warnings/unsupported capabilities |
| `incompatible` | Any critical check fails |
| `not_verified` | Verification not run |

## Version Compatibility

| Contract Version | Provider Versions | Notes |
|------------------|-------------------|-------|
| 1.0.0 | 1.x.x | Initial release |

Providers **must** declare `contract_version` they implement.