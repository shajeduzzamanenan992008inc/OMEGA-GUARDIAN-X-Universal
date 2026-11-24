# Contract Testing Guide

## Overview

This guide describes the reusable contract test suite that any provider adapter can execute to verify compliance with the Universal Provider Contract.

## Quick Start

### 1. Create Test Case

```python
# tests/test_my_provider_contract.py
from ogx.integration.testing import create_test_case
from ogx.platform.adapters.my_provider import MyProviderAdapter

MyProviderContractTests = create_test_case(MyProviderAdapter, "MyProviderContractTests")
```

### 2. Run with pytest

```bash
pytest tests/test_my_provider_contract.py -v
```

### 3. Run with unittest

```bash
python -m unittest tests.test_my_provider_contract -v
```

## Test Suite Architecture

### ContractTestSuite

The base test class provides 30+ reusable tests covering:

| Category | Tests |
|----------|-------|
| Identity | 6 tests |
| Capabilities | 4 tests |
| Read Operations | 4 tests |
| Pagination | 2 tests |
| Normalization | 1 test |
| Security | 5 tests |
| Error Handling | 4 tests |
| Transport | 3 tests |
| Credential Policy | 1 test |
| API Version | 1 test |
| Required Permissions | 1 test |
| Limitations | 1 test |

### ProviderTestHarness

For programmatic verification:

```python
from ogx.integration.testing import ProviderTestHarness

harness = ProviderTestHarness(
    MyProviderAdapter,
    test_credentials={"credential": "test-token"},
)

# Full contract verification
result = harness.run_verification()
print(f"Verdict: {result.verdict}")

# Unit test execution
test_result = harness.run_unit_tests()
```

## Test Categories

### Identity Tests

```python
def test_provider_identity(self):
    """Test provider identity fields are present and valid."""
    assert self.adapter.provider_id
    assert self.adapter.display_name
    assert self.adapter.version
    assert self.adapter.kind
    assert self.adapter.default_api_base.startswith("https://")
    assert self.adapter.credential_env
```

```python
def test_provider_descriptor(self):
    """Test provider descriptor is serializable and complete."""
    descriptor = self.adapter.descriptor()
    assert descriptor.provider_id == self.provider_id
    # All capabilities have states
    for cap in Capability:
        assert cap in descriptor.capabilities
```

### Capability Tests

```python
def test_capability_consistency(self):
    """Test capability declarations match implementations."""
    for capability, state in descriptor.capabilities.items():
        if state == CapabilityState.SUPPORTED:
            method_name = CAPABILITY_METHODS.get(capability)
            method = getattr(self.adapter, method_name)
            assert method is not base_method
```

```python
def test_unsupported_capabilities_not_implemented(self):
    """Test unsupported capabilities raise CapabilityUnavailableError."""
    for capability, state in descriptor.capabilities.items():
        if state == CapabilityState.UNSUPPORTED:
            method_name = CAPABILITY_METHODS.get(capability)
            # Should raise when called
```

### Read Operation Tests

```python
def test_identify(self):
    """Test identify() returns provider identity."""
    identity = self.adapter.identify()
    assert identity["provider_id"] == self.provider_id
    assert "display_name" in identity
    assert "kind" in identity
    assert "version" in identity
```

```python
def test_auth_status(self):
    """Test auth_status() returns authentication status."""
    status = self.adapter.auth_status()
    assert isinstance(status, AuthStatus)
    assert status.provider_id == self.provider_id
```

### Pagination Tests

```python
def test_paginated_methods_have_limit(self):
    """Test all paginated methods accept limit parameter."""
    for cap in paginated_capabilities:
        if self.adapter.capability_state(cap).is_available:
            method_name = CAPABILITY_METHODS.get(cap)
            method = getattr(self.adapter, method_name)
            sig = inspect.signature(method)
            assert "limit" in sig.parameters
            assert sig.parameters["limit"].default is not inspect.Parameter.empty
```

### Normalization Tests

```python
def test_model_serialization(self):
    """Test all normalized models serialize correctly."""
    models = [
        RepositoryMetadata(...),
        Branch(...),
        Commit(...),
        FileContent(...),
        TreeEntry(...),
        PullRequest(...),
        Issue(...),
        PermissionSet(...),
        RateLimit(...),
        HealthStatus(...),
        AuthStatus(...),
        OperationResult(...),
    ]

    for model in models:
        assert hasattr(model, "to_dict")
        data = model.to_dict()
        assert isinstance(data, dict)
        json.dumps(data)  # Must be JSON serializable
```

### Security Tests

```python
def test_https_enforcement(self):
    """Test API base enforces HTTPS."""
    api_base = self.adapter.default_api_base
    assert api_base.startswith("https://") or is_loopback(api_base)
```

```python
def test_credential_isolation(self):
    """Test credential is isolated to this provider."""
    if hasattr(self.adapter, "policy"):
        assert isinstance(self.adapter.policy, CredentialPolicy)
        assert self.adapter.policy.provider_id == self.provider_id
```

```python
def test_secret_redaction(self):
    """Test secret redaction works."""
    from ogx.platform.security import redact
    test_token = "ghp_test123456789012345678901234567890"
    text = f"Authorization: Bearer {test_token}"
    redacted = redact(text)
    assert test_token not in redacted
    assert "[REDACTED]" in redacted
```

```python
def test_ssrf_protection(self):
    """Test SSRF protection rejects metadata hosts."""
    from ogx.platform.security import validate_api_base
    for host in ("metadata.google.internal", "169.254.169.254"):
        try:
            validate_api_base(f"https://{host}/api")
            assert False, f"Should have rejected: {host}"
        except Exception:
            pass  # Expected
```

```python
def test_error_serialization_safe(self):
    """Test error serialization doesn't leak secrets."""
    from ogx.platform.errors import error_for_status
    error = error_for_status(401, provider=self.provider_id, detail="Bearer ghp_test...")
    error_str = json.dumps(error.to_dict())
    assert "ghp_test..." not in error_str
```

### Error Handling Tests

```python
def test_capability_unavailable_error(self):
    """Test unsupported capabilities raise proper error."""
    for cap in unsupported_capabilities[:3]:
        method_name = CAPABILITY_METHODS.get(cap)
        if method_name and hasattr(self.adapter, method_name):
            method = getattr(self.adapter, method_name)
            base_method = getattr(ProviderAdapter, method_name, None)
            assert method is not base_method
```

```python
def test_normalized_error_hierarchy(self):
    """Test normalized error types are available."""
    from ogx.platform.errors import (
        AuthenticationError, PermissionDeniedError, NotFoundError,
        RateLimitError, ServerError, CapabilityUnavailableError,
    )
    for err_type in [AuthenticationError, PermissionDeniedError, ...]:
        assert issubclass(err_type, ProviderError)
```

### Transport Tests

```python
def test_transport_timeout_enforcement(self):
    """Test transport enforces timeouts."""
    if isinstance(self.adapter._transport, HttpTransport):
        transport = self.adapter._transport
        assert 0 < transport.timeout <= 300
        assert 1024 <= transport.max_response_bytes <= 64 * 1024 * 1024
        assert 0 <= transport.max_retries <= 5
```

```python
def test_transport_no_redirects(self):
    """Test transport doesn't follow redirects."""
    # Custom _NoRedirect handler prevents credential leakage via 3xx
```

```python
def test_transport_correlation_ids(self):
    """Test transport generates correlation IDs."""
    transport = self.adapter._transport
    assert hasattr(transport, "last_correlation_id")
```

## Running Tests for Existing Providers

### GitHub Adapter

```bash
# Using the harness
python -c "
from ogx.integration.testing import ProviderTestHarness
from ogx.platform.adapters.github import GitHubAdapter

harness = ProviderTestHarness(GitHubAdapter, test_credentials={'credential': 'test'})
result = harness.run_verification()
print(f'Verdict: {result.verdict}')
for check in result.check_details:
    status = '✅' if check.passed else '❌'
    print(f'{status} {check.name}: {check.message}')
"
```

### All Adapters

```python
from ogx.platform.adapters import github, gitlab, bitbucket, gitea, azure_devops

adapters = [
    github.GitHubAdapter,
    gitlab.GitLabAdapter,
    bitbucket.BitbucketAdapter,
    gitea.GiteaAdapter,
    azure_devops.AzureDevOpsAdapter,
]

for adapter_class in adapters:
    harness = ProviderTestHarness(adapter_class, test_credentials={'credential': 'test'})
    result = harness.run_verification()
    print(f"{adapter_class.provider_id}: {result.verdict}")
```

## Extending Tests

### Adding Custom Tests

```python
from ogx.integration.testing import ContractTestSuite

class MyExtendedTests(ContractTestSuite):
    def test_custom_business_rule(self):
        """Custom validation specific to my provider."""
        # Your custom logic
        assert self.adapter.some_custom_method() == "expected"
```

### Custom Test Fixtures

```python
class MyProviderContractTests(ContractTestSuite):
    provider_id = "my_provider"
    adapter_class = MyProviderAdapter
    
    test_credentials = {
        "credential": "test-token",
        "api_base": "https://api.example.com",
    }
    
    mock_responses = {
        "get_repository": {"id": "123", "name": "test"},
    }
```

## CI/CD Integration

### GitHub Actions

```yaml
name: Provider Contract Tests

on: [push, pull_request]

jobs:
  contract-tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        provider: [github, gitlab, bitbucket, gitea, azure_devops]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e .
      - run: |
          python -c "
          from ogx.integration.testing import ProviderTestHarness
          from ogx.platform.adapters.${{ matrix.provider }} import ${{ matrix.provider|capitalize }}Adapter
          
          harness = ProviderTestHarness(${ {matrix.provider|capitalize} }Adapter, test_credentials={'credential': 'test'})
          result = harness.run_verification()
          
          if result.verdict == 'incompatible':
              print('::error::Provider is incompatible')
              exit(1)
          elif result.verdict == 'compatible_with_limitations':
              print('::warning::Provider has limitations')
          
          print(f'Verdict: {result.verdict}')
          "
```

## Debugging Test Failures

### Common Failure Patterns

| Failure | Likely Cause |
|---------|-------------|
| `identity_provider_id` missing | `provider_id` class variable not defined |
| `capability_X_implementation` | Method not overridden from base |
| `https_enforcement` | `default_api_base` uses HTTP |
| `secret_redaction` | `redact()` not removing token patterns |
| `credential_isolation_policy` | `CredentialPolicy` has wrong provider_id |
| `error_serialization` | Error contains unredacted secrets |

### Verbose Output

```bash
# Run with verbose output
pytest tests/test_my_provider_contract.py -v -s

# Run single test
pytest tests/test_my_provider_contract.py::MyProviderContractTests::test_capability_consistency -v
```

### Inspect Verification Result

```python
result = harness.run_verification()

# Print all checks
for check in result.check_details:
    status = "✅" if check.passed else "❌"
    print(f"{status} {check.name} [{check.severity}]: {check.message}")
    if check.detail:
        print(f"    Detail: {check.detail}")

# Print summary
print(f"\nVerdict: {result.verdict}")
print(f"Compatible: {result.compatible}")
print(f"Passed: {len(result.passed_checks)}")
print(f"Failed: {len(result.failed_checks)}")
print(f"Warnings: {len(result.warnings)}")
```

## Test Maintenance

### When Contract Evolves

1. Update `ContractTestSuite` with new tests
2. Update `ContractVerifier` with new verification checks
3. Update `SecurityValidator` with new security checks
4. Run tests against all providers
5. Update compatibility matrix

### Adding New Provider

1. Implement adapter following `ProviderAdapter`
2. Create `create_test_case()` for the adapter
3. Run full verification
4. Document limitations
5. Add to CI/CD matrix