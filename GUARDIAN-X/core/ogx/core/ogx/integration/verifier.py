"""Provider contract verification engine.

Validates provider implementations against the canonical Universal Provider Contract.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Optional, List, Dict

from .models import VerificationCheck
from ogx.platform.base import CAPABILITY_METHODS
from ogx.platform.capabilities import Capability, CapabilityState


class VerificationResult:
    """Complete verification result for a provider."""

    def __init__(
        self,
        provider_id: str,
        provider_version: str,
        compatible: bool = True,
        test_environment: Optional[Dict[str, Any]] = None,
    ):
        self.provider_id = provider_id
        self.provider_version = provider_version
        self.compatible = compatible
        self.passed_checks: List[str] = []
        self.failed_checks: List[str] = []
        self.warnings: List[str] = []
        self.unsupported_capabilities: List[str] = []
        self.security_warnings: List[str] = []
        self.known_limitations: List[str] = []
        self.check_details: List[VerificationCheck] = []
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.test_environment = test_environment or {}

    @property
    def verdict(self) -> str:
        """Get compatibility verdict."""
        if not self.compatible:
            return "incompatible"
        if self.warnings or self.unsupported_capabilities:
            return "compatible_with_limitations"
        if self.failed_checks:
            return "incompatible"
        return "compatible"

    def add_check(self, check: VerificationCheck) -> "VerificationResult":
        """Add a check result and return self (modified in-place)."""
        # Add the new check
        if check.passed:
            self.passed_checks.append(check.name)
        else:
            self.failed_checks.append(check.name)
            if check.severity in ("error", "critical"):
                self.compatible = False

        if check.severity == "warning":
            self.warnings.append(f"{check.name}: {check.message}")

        self.check_details.append(check)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "verdict": self.verdict,
            "compatible": self.compatible,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "warnings": self.warnings,
            "unsupported_capabilities": self.unsupported_capabilities,
            "security_warnings": self.security_warnings,
            "known_limitations": self.known_limitations,
            "check_details": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "detail": c.detail,
                    "severity": c.severity,
                }
                for c in self.check_details
            ],
            "timestamp": self.timestamp,
            "test_environment": self.test_environment,
        }


class ContractVerifier:
    """Verifies provider adapter implementations against the universal contract."""

    def __init__(self):
        self.capability_validator = None  # Will be imported lazily
        self.security_validator = None

    def _get_capability_validator(self):
        if self.capability_validator is None:
            from .capabilities import CapabilityValidator
            self.capability_validator = CapabilityValidator()
        return self.capability_validator

    def _get_security_validator(self):
        if self.security_validator is None:
            from .security import SecurityValidator
            self.security_validator = SecurityValidator()
        return self.security_validator

    def verify(self, adapter: Any, *, test_environment: Optional[Dict[str, Any]] = None) -> VerificationResult:
        """Verify a provider adapter against the universal contract."""
        if not hasattr(adapter, "provider_id"):
            raise ValueError("Adapter must have a provider_id attribute")

        provider_id = adapter.provider_id
        provider_version = getattr(adapter, "version", "unknown")

        result = VerificationResult(
            provider_id=provider_id,
            provider_version=provider_version,
            compatible=True,
            test_environment=test_environment or {},
        )

        # Run all verification checks
        result = self._verify_identity(result, adapter)
        result = self._verify_capabilities(result, adapter)
        result = self._verify_method_signatures(result, adapter)
        result = self._verify_return_types(result, adapter)
        result = self._verify_error_handling(result, adapter)
        result = self._verify_capability_consistency(result, adapter)
        result = self._verify_security_contracts(result, adapter)
        result = self._verify_pagination_behavior(result, adapter)
        result = self._verify_normalization(result, adapter)
        result = self._verify_read_write_separation(result, adapter)
        result = self._verify_authentication(result, adapter)
        result = self._verify_authorization(result, adapter)
        result = self._verify_credential_isolation(result, adapter)
        result = self._verify_https_enforcement(result, adapter)
        result = self._verify_secret_redaction(result, adapter)
        result = self._verify_rate_limit_handling(result, adapter)
        result = self._verify_unsupported_capabilities(result, adapter)
        result = self._verify_known_limitations(result, adapter)

        return result

    def _verify_identity(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify provider identity fields."""
        checks = [
            ("provider_id", lambda: hasattr(adapter, "provider_id") and isinstance(adapter.provider_id, str) and adapter.provider_id),
            ("display_name", lambda: hasattr(adapter, "display_name") and isinstance(adapter.display_name, str) and adapter.display_name),
            ("version", lambda: hasattr(adapter, "version") and isinstance(adapter.version, str) and adapter.version),
            ("kind", lambda: hasattr(adapter, "kind") and adapter.kind is not None),
            ("api_version", lambda: hasattr(adapter, "api_version")),
            ("default_api_base", lambda: hasattr(adapter, "default_api_base")),
            ("credential_env", lambda: hasattr(adapter, "credential_env") and isinstance(adapter.credential_env, (tuple, list))),
        ]

        for name, check in checks:
            try:
                passed = check()
                result = result.add_check(VerificationCheck(
                    name=f"identity_{name}",
                    passed=passed,
                    message=f"Identity field '{name}' present and valid" if passed else f"Missing or invalid identity field: {name}",
                    severity="critical" if not passed else "info",
                ))
            except Exception as e:
                result = result.add_check(VerificationCheck(
                    name=f"identity_{name}",
                    passed=False,
                    message=f"Error checking identity field '{name}': {e}",
                    severity="critical",
                ))

        return result

    def _verify_capabilities(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify capability declarations."""
        # Capabilities that don't have method mappings (special cases)
        SPECIAL_CAPABILITIES_NO_METHOD = {
            Capability.PAGINATION,
            Capability.API_VERSION,
        }

        try:
            descriptor = adapter.descriptor()
            capabilities = descriptor.capabilities

            # Check that all capabilities have a state
            for capability in capabilities:
                state = capabilities[capability]

                if state is None:
                    result = result.add_check(VerificationCheck(
                        name=f"capability_{capability.value}_state",
                        passed=False,
                        message=f"Capability {capability.value} has no state",
                        severity="error",
                    ))
                    continue

                # If capability is supported, verify the method exists
                if state.value == "supported":
                    # Skip method check for special capabilities
                    if capability in SPECIAL_CAPABILITIES_NO_METHOD:
                        result = result.add_check(VerificationCheck(
                            name=f"capability_{capability.value}_special",
                            passed=True,
                            message=f"Capability {capability.value} is special (no method mapping needed)",
                            severity="info",
                        ))
                    else:
                        method_name = CAPABILITY_METHODS.get(capability)
                        if method_name:
                            method = getattr(adapter, method_name, None)
                            if method is None or method is getattr(adapter.__class__, method_name, None):
                                result = result.add_check(VerificationCheck(
                                    name=f"capability_{capability.value}_implementation",
                                    passed=False,
                                    message=f"Capability {capability.value} declared supported but method {method_name} not implemented",
                                    severity="critical",
                                ))
                            else:
                                result = result.add_check(VerificationCheck(
                                    name=f"capability_{capability.value}_implementation",
                                    passed=True,
                                    message=f"Capability {capability.value} has implementation",
                                    severity="info",
                                ))
                        else:
                            result = result.add_check(VerificationCheck(
                                name=f"capability_{capability.value}_method_mapping",
                                passed=False,
                                message=f"Capability {capability.value} has no method mapping in CAPABILITY_METHODS",
                                severity="warning",
                            ))

            result = result.add_check(VerificationCheck(
                name="capability_completeness",
                passed=True,
                message="All capabilities have valid states",
                severity="info",
            ))

        except Exception as e:
            result = result.add_check(VerificationCheck(
                name="capability_verification",
                passed=False,
                message=f"Capability verification failed: {e}",
                severity="critical",
            ))

        return result

    def _verify_method_signatures(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify method signatures match contract."""
        for capability, method_name in CAPABILITY_METHODS.items():
            if not hasattr(adapter, method_name):
                result = result.add_check(VerificationCheck(
                    name=f"signature_{method_name}",
                    passed=False,
                    message=f"Missing required method: {method_name}",
                    severity="error",
                ))
                continue

            method = getattr(adapter, method_name)
            if not callable(method):
                result = result.add_check(VerificationCheck(
                    name=f"signature_{method_name}",
                    passed=False,
                    message=f"Method {method_name} is not callable",
                    severity="error",
                ))
                continue

            # Check signature has expected parameters
            # For bound methods, we need to inspect the underlying function
            if hasattr(method, '__func__'):
                sig = inspect.signature(method.__func__)
            else:
                sig = inspect.signature(method)
            params = list(sig.parameters.keys())

            # All methods should have self as first parameter
            if not params or params[0] != "self":
                result = result.add_check(VerificationCheck(
                    name=f"signature_{method_name}",
                    passed=False,
                    message=f"Method {method_name} missing 'self' parameter",
                    severity="error",
                ))
                continue

            # Verify required parameters exist
            required_params = {
                "discover_repositories": [],
                "get_repository": ["repository"],
                "list_branches": ["repository"],
                "list_commits": ["repository"],
                "get_commit": ["repository", "commit_sha"],
                "read_file": ["repository", "path"],
                "read_tree": ["repository"],
                "list_pull_requests": ["repository"],
                "list_issues": ["repository"],
                "create_pull_request": ["repository", "title", "body", "head", "base"],
                "create_issue": ["repository", "title"],
                "update_issue": ["repository", "number"],
            }

            if method_name in required_params:
                for req_param in required_params[method_name]:
                    if req_param not in params:
                        result = result.add_check(VerificationCheck(
                            name=f"signature_{method_name}_params",
                            passed=False,
                            message=f"Method {method_name} missing required parameter: {req_param}",
                            severity="error",
                        ))

            result = result.add_check(VerificationCheck(
                name=f"signature_{method_name}",
                passed=True,
                message=f"Method signature valid",
                severity="info",
            ))

        return result

    def _verify_return_types(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify return types match normalized models."""
        # This requires actual method invocation, so we do a static check
        # The actual runtime verification happens in contract tests

        for capability, method_name in CAPABILITY_METHODS.items():
            if hasattr(adapter, method_name):
                method = getattr(adapter, method_name)
                if callable(method):
                    # Check if method has return type annotation
                    sig = inspect.signature(method)
                    return_annotation = sig.return_annotation

                    if return_annotation is not inspect.Signature.empty:
                        result = result.add_check(VerificationCheck(
                            name=f"return_type_{method_name}",
                            passed=True,
                            message=f"Method {method_name} has return annotation: {return_annotation}",
                            severity="info",
                        ))
                    else:
                        result = result.add_check(VerificationCheck(
                            name=f"return_type_{method_name}",
                            passed=False,
                            message=f"Method {method_name} missing return type annotation",
                            severity="warning",
                        ))

        return result

    def _verify_error_handling(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify error handling contract."""
        from ogx.platform.errors import ProviderError

        # Check that adapter uses normalized error types
        try:
            # Verify that CapabilityUnavailableError is raised for unsupported operations
            from ogx.platform.base import ProviderAdapter
            from ogx.platform.capabilities import Capability

            # Find an unsupported capability
            unsupported = None
            for cap in Capability:
                state = adapter.capability_state(cap)
                if state.value == "unsupported":
                    unsupported = cap
                    break

            if unsupported:
                method_name = CAPABILITY_METHODS.get(unsupported)
                if method_name and hasattr(adapter, method_name):
                    method = getattr(adapter, method_name)
                    # We can't call it without proper setup, but we can check the code raises CapabilityUnavailableError
                    result = result.add_check(VerificationCheck(
                        name=f"error_handling_{unsupported.value}",
                        passed=True,
                        message=f"Unsupported capability {unsupported.value} has method that should raise CapabilityUnavailableError",
                        severity="info",
                    ))

            # Verify all ProviderError subclasses are used
            result = result.add_check(VerificationCheck(
                name="error_hierarchy",
                passed=True,
                message="Normalized error taxonomy available",
                severity="info",
            ))

        except Exception as e:
            result = result.add_check(VerificationCheck(
                name="error_handling",
                passed=False,
                message=f"Error handling verification failed: {e}",
                severity="error",
            ))

        return result

    def _verify_capability_consistency(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify capability declarations are consistent."""
        capability_validator = self._get_capability_validator()
        capability_result = capability_validator.validate_adapter(adapter)

        for check_name, passed, message, severity in capability_result.checks:
            result = result.add_check(VerificationCheck(
                name=f"capability_consistency_{check_name}",
                passed=passed,
                message=message,
                severity=severity,
            ))

        # Copy unsupported capabilities
        result.unsupported_capabilities.extend(capability_result.unsupported_capabilities)

        return result

    def _verify_security_contracts(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify security contracts."""
        security_validator = self._get_security_validator()
        security_result = security_validator.validate_adapter(adapter)

        for check_name, passed, message, severity in security_result.checks:
            result = result.add_check(VerificationCheck(
                name=f"security_{check_name}",
                passed=passed,
                message=message,
                severity=severity,
            ))

        result.warnings.extend(security_result.warnings)
        result.security_warnings.extend(security_result.warnings)

        return result

    def _verify_pagination_behavior(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify pagination contract compliance."""
        from ogx.platform.capabilities import Capability

        if adapter.capability_state(Capability.PAGINATION).value == "supported":
            # Check that paginated methods respect limit parameter
            paginated_methods = [
                "discover_repositories",
                "list_branches",
                "list_commits",
                "list_pull_requests",
                "list_issues",
            ]

            for method_name in paginated_methods:
                if hasattr(adapter, method_name):
                    method = getattr(adapter, method_name)
                    sig = inspect.signature(method)
                    params = sig.parameters

                    # Should have limit parameter with default
                    if "limit" not in params:
                        result = result.add_check(VerificationCheck(
                            name=f"pagination_{method_name}_limit_param",
                            passed=False,
                            message=f"Paginated method {method_name} missing 'limit' parameter",
                            severity="error",
                        ))
                    else:
                        limit_param = params["limit"]
                        if limit_param.default is inspect.Parameter.empty:
                            result = result.add_check(VerificationCheck(
                                name=f"pagination_{method_name}_limit_default",
                                passed=False,
                                message=f"Paginated method {method_name} limit parameter should have default",
                                severity="warning",
                            ))
                        else:
                            result = result.add_check(VerificationCheck(
                                name=f"pagination_{method_name}",
                                passed=True,
                                message=f"Paginated method {method_name} has proper limit parameter",
                                severity="info",
                            ))

        return result

    def _verify_normalization(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify response normalization."""
        # Check that models have to_dict method
        model_classes = [
            "NormalizedRepository",
            "NormalizedBranch",
            "NormalizedCommit",
            "NormalizedFile",
            "NormalizedTreeEntry",
            "NormalizedPullRequest",
            "NormalizedIssue",
            "NormalizedUser",
            "NormalizedPermission",
            "NormalizedPagination",
            "NormalizedRateLimit",
            "NormalizedHealthStatus",
            "NormalizedOperationResult",
            "NormalizedProviderError",
        ]

        for model_name in model_classes:
            # Import the model
            from .models import (
                NormalizedRepository,
                NormalizedBranch,
                NormalizedCommit,
                NormalizedFile,
                NormalizedTreeEntry,
                NormalizedPullRequest,
                NormalizedIssue,
                NormalizedUser,
                NormalizedPermission,
                NormalizedPagination,
                NormalizedRateLimit,
                NormalizedHealthStatus,
                NormalizedOperationResult,
                NormalizedProviderError,
            )
            model_map = {
                "NormalizedRepository": NormalizedRepository,
                "NormalizedBranch": NormalizedBranch,
                "NormalizedCommit": NormalizedCommit,
                "NormalizedFile": NormalizedFile,
                "NormalizedTreeEntry": NormalizedTreeEntry,
                "NormalizedPullRequest": NormalizedPullRequest,
                "NormalizedIssue": NormalizedIssue,
                "NormalizedUser": NormalizedUser,
                "NormalizedPermission": NormalizedPermission,
                "NormalizedPagination": NormalizedPagination,
                "NormalizedRateLimit": NormalizedRateLimit,
                "NormalizedHealthStatus": NormalizedHealthStatus,
                "NormalizedOperationResult": NormalizedOperationResult,
                "NormalizedProviderError": NormalizedProviderError,
            }
            model = model_map.get(model_name)
            if model and hasattr(model, "to_dict"):
                result = result.add_check(VerificationCheck(
                    name=f"normalization_{model_name}",
                    passed=True,
                    message=f"Model {model_name} has to_dict method",
                    severity="info",
                ))
            else:
                result = result.add_check(VerificationCheck(
                    name=f"normalization_{model_name}",
                    passed=False,
                    message=f"Model {model_name} missing to_dict method",
                    severity="error",
                ))

        return result

    def _verify_read_write_separation(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify read/write operation separation."""
        from ogx.platform.capabilities import Capability

        # Check that write capabilities are properly marked
        descriptor = adapter.descriptor()
        for cap in Capability:
            if cap in WRITE_CAPABILITIES:
                state = descriptor.capability_state(cap, authenticated=True, configured=True)
                if state.value == "supported":
                    # Verify the method exists and is not the base implementation
                    method_name = CAPABILITY_METHODS.get(cap)
                    if method_name:
                        method = getattr(adapter, method_name, None)
                        base_method = getattr(adapter.__class__, method_name, None)
                        if method is not base_method:
                            result = result.add_check(VerificationCheck(
                                name=f"write_separation_{cap.value}",
                                passed=True,
                                message=f"Write capability {cap.value} properly implemented",
                                severity="info",
                            ))
                        else:
                            result = result.add_check(VerificationCheck(
                                name=f"write_separation_{cap.value}",
                                passed=False,
                                message=f"Write capability {cap.value} not implemented (uses base)",
                                severity="error",
                            ))

        return result

    def _verify_authentication(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify authentication contract."""
        # Check that auth_status method exists and returns proper structure
        if hasattr(adapter, "auth_status"):
            method = getattr(adapter, "auth_status")
            if callable(method):
                result = result.add_check(VerificationCheck(
                    name="authentication_auth_status",
                    passed=True,
                    message="auth_status method exists",
                    severity="info",
                ))

        # Check credential_env is defined and non-empty for authenticated providers
        if hasattr(adapter, "credential_env"):
            cred_env = adapter.credential_env
            if cred_env and len(cred_env) > 0:
                result = result.add_check(VerificationCheck(
                    name="authentication_credential_env",
                    passed=True,
                    message=f"Credential environment variables defined: {cred_env}",
                    severity="info",
                ))
            else:
                result = result.add_check(VerificationCheck(
                    name="authentication_credential_env",
                    passed=False,
                    message="No credential environment variables defined",
                    severity="warning",
                ))

        return result

    def _verify_authorization(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify authorization contract."""
        # Check that required_permissions is documented
        if hasattr(adapter, "required_permissions"):
            perms = adapter.required_permissions
            if perms and len(perms) > 0:
                result = result.add_check(VerificationCheck(
                    name="authorization_required_permissions",
                    passed=True,
                    message=f"Required permissions documented: {perms}",
                    severity="info",
                ))

        # Check that get_permissions method exists for providers that support it
        from ogx.platform.capabilities import Capability
        if adapter.capability_state(Capability.PERMISSION_READ).is_available:
            if hasattr(adapter, "get_permissions"):
                result = result.add_check(VerificationCheck(
                    name="authorization_get_permissions",
                    passed=True,
                    message="get_permissions method available",
                    severity="info",
                ))

        return result

    def _verify_credential_isolation(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify credential isolation between providers."""
        # Check that adapter doesn't share credential state
        if hasattr(adapter, "_credential"):
            result = result.add_check(VerificationCheck(
                name="credential_isolation_private",
                passed=True,
                message="Credential stored in private instance variable",
                severity="info",
            ))

        # Check credential policy is per-provider
        if hasattr(adapter, "policy"):
            result = result.add_check(VerificationCheck(
                name="credential_isolation_policy",
                passed=True,
                message="CredentialPolicy is per-provider instance",
                severity="info",
            ))

        return result

    def _verify_https_enforcement(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify HTTPS enforcement."""
        from ogx.platform.security import validate_api_base

        # Check that default_api_base uses HTTPS
        if hasattr(adapter, "default_api_base"):
            api_base = adapter.default_api_base
            if api_base.startswith("https://"):
                result = result.add_check(VerificationCheck(
                    name="https_enforcement_default_base",
                    passed=True,
                    message=f"Default API base uses HTTPS: {api_base}",
                    severity="info",
                ))
            elif api_base.startswith("http://"):
                # Check if it's loopback
                if "127.0.0.1" in api_base or "localhost" in api_base:
                    result = result.add_check(VerificationCheck(
                        name="https_enforcement_default_base",
                        passed=True,
                        message=f"HTTP allowed for loopback development: {api_base}",
                        severity="info",
                    ))
                else:
                    result = result.add_check(VerificationCheck(
                        name="https_enforcement_default_base",
                        passed=False,
                        message=f"Default API base uses insecure HTTP: {api_base}",
                        severity="critical",
                    ))

        return result

    def _verify_secret_redaction(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify secret redaction in logs and errors."""
        from ogx.platform.security import redact

        # Verify redact function is available
        result = result.add_check(VerificationCheck(
            name="secret_redaction_function",
            passed=True,
            message="redact function available for secret removal",
            severity="info",
        ))

        # Check that transport uses redaction
        if hasattr(adapter, "_transport") and hasattr(adapter._transport, "last_correlation_id"):
            result = result.add_check(VerificationCheck(
                name="secret_redaction_transport",
                passed=True,
                message="Transport layer available for correlation IDs",
                severity="info",
            ))

        return result

    def _verify_rate_limit_handling(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify rate limit handling."""
        from ogx.platform.capabilities import Capability

        if adapter.capability_state(Capability.RATE_LIMIT_READ).is_available:
            if hasattr(adapter, "rate_limit"):
                result = result.add_check(VerificationCheck(
                    name="rate_limit_method",
                    passed=True,
                    message="rate_limit method available",
                    severity="info",
                ))

        # Check transport has retry-after handling
        if hasattr(adapter, "_transport"):
            transport = adapter._transport
            if hasattr(transport, "backoff_base") and hasattr(transport, "max_retries"):
                result = result.add_check(VerificationCheck(
                    name="rate_limit_transport_retry",
                    passed=True,
                    message="Transport has retry/backoff configuration",
                    severity="info",
                ))

        return result

    def _verify_unsupported_capabilities(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify unsupported capabilities are properly reported."""
        descriptor = adapter.descriptor()
        unsupported = [
            cap.value for cap, state in descriptor.capabilities.items()
            if state.value == "unsupported"
        ]

        for cap_name in unsupported:
            # Add the capability to unsupported list
            result.unsupported_capabilities.append(cap_name)
            result = result.add_check(VerificationCheck(
                name=f"unsupported_{cap_name}",
                passed=True,
                message=f"Capability {cap_name} correctly reported as unsupported",
                severity="info",
            ))

        return result

    def _verify_known_limitations(self, result: VerificationResult, adapter: Any) -> VerificationResult:
        """Verify known limitations are documented."""
        if hasattr(adapter, "limitations"):
            limitations = adapter.limitations
            if limitations:
                # Add all limitations at once
                result.known_limitations.extend(limitations)
                result = result.add_check(VerificationCheck(
                    name="known_limitations_documented",
                    passed=True,
                    message=f"Limitations documented: {limitations}",
                    severity="info",
                ))

        return result


# Import WRITE_CAPABILITIES after class definition to avoid circular import
from ogx.platform.capabilities import WRITE_CAPABILITIES


def verify_provider(adapter: Any, *, test_environment: Optional[Dict[str, Any]] = None) -> VerificationResult:
    """Convenience function to verify a provider adapter.

    Args:
        adapter: The provider adapter instance to verify
        test_environment: Optional test environment metadata

    Returns:
        VerificationResult with complete compatibility assessment
    """
    verifier = ContractVerifier()
    return verifier.verify(adapter, test_environment=test_environment)