# Provider Compatibility Guide

## Overview

This guide explains how provider adapters are evaluated for compatibility with the OMEGA GUARDIAN-X Universal integration engine.

## Compatibility Verification Process

### 1. Automated Contract Verification

Run the contract verifier against any provider adapter:

```python
from ogx.integration import verify_provider
from ogx.platform.adapters.github import GitHubAdapter

adapter = GitHubAdapter(credential="test-token")
result = verify_provider(adapter, test_environment={"suite": "compatibility"})

print(f"Verdict: {result.verdict}")
print(f"Compatible: {result.compatible}")
print(f"Passed: {len(result.passed_checks)}")
print(f"Failed: {len(result.failed_checks)}")
print(f"Warnings: {len(result.warnings)}")
```

### 2. Compatibility Verdicts

| Verdict | Meaning | Action |
|---------|---------|--------|
| `compatible` | Fully compliant, no issues | ✅ Contract-compatible (mocked contract suite; not a live-API or production-readiness claim) |
| `compatible_with_limitations` | Compliant but has known limitations | ⚠️ Review limitations before use |
| `incompatible` | Critical contract violations | ❌ Fix violations before use |
| `not_verified` | Not yet evaluated | 🔄 Run verification |

### 3. Report Generation

```python
from ogx.integration.reports import CompatibilityReport, generate_compatibility_report

report = generate_compatibility_report(
    provider_id="github",
    provider_version="1.0.0",
    verification_result=result,
)

# Save to file
report.save("github-compatibility-report.json")

# Or get JSON
print(report.to_json())
```

## Evaluating a New Provider Adapter

### Step 1: Identity Verification

Verify the adapter declares all required identity fields:

```python
adapter = MyProviderAdapter()
assert adapter.provider_id
assert adapter.display_name
assert adapter.version
assert adapter.kind
assert adapter.default_api_base.startswith("https://")
assert adapter.credential_env
```

### Step 2: Capability Matrix Validation

```python
from ogx.integration.capabilities import CapabilityValidator

validator = CapabilityValidator()
result = validator.validate_adapter(adapter)

# Check for critical issues
for check in result.checks:
    if not check[1] and check[3] == "critical":
        print(f"CRITICAL: {check[0]} - {check[2]}")
```

### Step 3: Security Contract Validation

```python
from ogx.integration.security import SecurityValidator

validator = SecurityValidator()
result = validator.validate_adapter(adapter)

# Check for security violations
for check in result.checks:
    if not check[1] and check[3] in ("critical", "error"):
        print(f"SECURITY: {check[0]} - {check[2]}")
```

### Step 4: Functional Contract Tests

Run the reusable test suite:

```python
from ogx.integration.testing import ProviderTestHarness

harness = ProviderTestHarness(
    MyProviderAdapter,
    test_credentials={"credential": "test-token"},
)

# Run full verification
result = harness.run_verification()

# Or run as unittest
test_result = harness.run_unit_tests()
```

## Provider Compatibility Matrix

> **Phase 0 correction (2026-11-26).** The matrix below is a historical
> contract-verification template dated 2024-11-24, not a live-provider record.
> Every adapter test in this repository uses mocked or fake transports, no
> provider credential was present, and no provider API call was made. The
> current registry has seven contract-verified adapters (GitHub, GitLab,
> Bitbucket, Gitea, Forgejo, Azure DevOps Repos, AWS CodeCommit); Forgejo and
> AWS CodeCommit are absent from this table. Read `compatible` as "passes the
> mocked contract suite", never as production readiness. See
> `../../docs/MASTER_BASELINE_AUDIT.md`.

| Provider | Version | Verdict | Contract Version | Last Contract Verification |
|----------|---------|---------|------------------|---------------|
| GitHub | 1.0.0 | compatible | 1.0.0 | 2024-11-24 |
| GitLab | 1.0.0 | compatible | 1.0.0 | 2024-11-24 |
| Bitbucket | 1.0.0 | compatible | 1.0.0 | 2024-11-24 |
| Gitea | 1.0.0 | compatible | 1.0.0 | 2024-11-24 |
| Azure DevOps | 1.0.0 | compatible | 1.0.0 | 2024-11-24 |

## Known Limitations by Provider

### GitHub
- Contents API returns empty body above ~1 MiB; such files marked truncated
- Repository permissions may lag organization policy
- GitHub Enterprise Server supported via explicit `api_base`

### GitLab
- GraphQL API not yet implemented (REST only)
- Project-level webhook permissions not exposed

### Bitbucket
- App passwords used for authentication (user:secret format)
- Workspace-scoped APIs require explicit workspace context

### Gitea
- Self-hosted instances require explicit `api_base` configuration
- Some enterprise features not available in community edition

### Azure DevOps
- PAT tokens require specific scopes
- Organization name must be provided separately

## Extending Compatibility Checks

### Adding Custom Checks

```python
from ogx.integration.verifier import ContractVerifier, VerificationCheck

class MyCustomVerifier(ContractVerifier):
    def verify(self, adapter, test_environment=None):
        result = super().verify(adapter, test_environment)
        
        # Add custom check
        result.add_check(VerificationCheck(
            name="custom_business_rule",
            passed=self._check_business_rule(adapter),
            message="Custom business rule validation",
            severity="warning",
        ))
        
        return result
    
    def _check_business_rule(self, adapter):
        # Your custom logic
        return True
```

### Adding Security Validations

```python
from ogx.integration.security import SecurityValidator, SecurityValidationResult

class MySecurityValidator(SecurityValidator):
    def validate_adapter(self, adapter):
        result = super().validate_adapter(adapter)
        
        # Add custom security check
        result.checks.append((
            "custom_security_check",
            self._check_custom_security(adapter),
            "Custom security validation",
            "warning" if not self._check_custom_security(adapter) else "info",
        ))
        
        return result
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Provider Compatibility

on: [push, pull_request]

jobs:
  compatibility:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install -e .
      - name: Run compatibility verification
        run: |
          python -c "
          from ogx.integration import verify_provider
          from ogx.platform.adapters.github import GitHubAdapter
          
          adapter = GitHubAdapter(credential='test-token')
          result = verify_provider(adapter)
          
          if result.verdict == 'incompatible':
              print('::error::Provider is incompatible')
              exit(1)
          elif result.verdict == 'compatible_with_limitations':
              print('::warning::Provider has limitations')
          
          print(f'Verdict: {result.verdict}')
          "
```

## Troubleshooting Common Issues

### Missing Identity Fields

```
ERROR: identity_provider_id - Missing or invalid identity field: provider_id
```

**Fix:** Ensure `provider_id` class variable is defined as a non-empty string.

### Capability Declared but Not Implemented

```
ERROR: capability_repository_discovery_implementation - Capability repository_discovery declared supported but method discover_repositories not implemented
```

**Fix:** Override the method in your adapter class with a custom implementation.

### HTTPS Enforcement Failure

```
CRITICAL: https_enforcement - API base uses insecure HTTP: http://api.example.com
```

**Fix:** Change `default_api_base` to use `https://` or ensure it's a loopback address for development.

### Credential Leakage

```
CRITICAL: credential_leakage_str - Credential leaked in adapter string representation
```

**Fix:** Ensure `__str__` and `__repr__` don't include `self._credential`.

## Maintenance

### Updating Contract Version

When the universal contract evolves:

1. Update `contract_version` in verification results
2. Update `INTEGRATION_CONTRACTS.md` with new requirements
3. Regenerate compatibility reports for all providers
4. Update `PROVIDER_COMPATIBILITY.md` matrix

### Adding New Providers

1. Implement adapter following `ProviderAdapter` base class
2. Run full verification suite
3. Document any limitations
4. Add to compatibility matrix
5. Add CI/CD verification job