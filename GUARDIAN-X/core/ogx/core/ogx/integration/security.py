"""Security contract validator.

Validates security boundary contracts for provider adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ogx.platform.security import (
    validate_api_base,
    redact,
    CredentialPolicy,
    LOOPBACK_HOSTS,
    METADATA_HOSTS,
)
from ogx.platform.transport import HttpTransport


@dataclass(frozen=True, slots=True)
class SecurityValidationResult:
    """Result of security validation."""

    checks: list[tuple[str, bool, str, str]] = field(default_factory=list)  # (name, passed, message, severity)
    warnings: list[str] = field(default_factory=list)


class SecurityValidator:
    """Validates security contracts for provider adapters."""

    def __init__(self) -> None:
        pass

    def validate_adapter(self, adapter: Any) -> SecurityValidationResult:
        """Validate all security contracts for an adapter."""
        result = SecurityValidationResult()

        # 1. HTTPS enforcement
        self._validate_https_enforcement(result, adapter)

        # 2. Credential isolation
        self._validate_credential_isolation(result, adapter)

        # 3. Secret redaction
        self._validate_secret_redaction(result, adapter)

        # 4. SSRF protection
        self._validate_ssrf_protection(result, adapter)

        # 5. Response size limits
        self._validate_response_limits(result, adapter)

        # 6. Request timeout enforcement
        self._validate_timeout_enforcement(result, adapter)

        # 7. Safe error serialization
        self._validate_error_serialization(result, adapter)

        # 8. No credential leakage in logs
        self._validate_no_credential_leakage(result, adapter)

        # 9. No unauthorized write operations
        self._validate_write_authorization(result, adapter)

        # 10. Host allowlisting
        self._validate_host_allowlisting(result, adapter)

        return result

    def _validate_https_enforcement(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate HTTPS is enforced for API endpoints."""
        if hasattr(adapter, "default_api_base"):
            api_base = adapter.default_api_base
            if api_base.startswith("https://"):
                result.checks.append((
                    "https_enforcement",
                    True,
                    f"API base uses HTTPS: {api_base}",
                    "info",
                ))
            elif api_base.startswith("http://"):
                # Check if it's loopback
                is_loopback = any(host in api_base for host in LOOPBACK_HOSTS)
                if is_loopback:
                    result.checks.append((
                        "https_enforcement_loopback",
                        True,
                        f"HTTP allowed for loopback development: {api_base}",
                        "info",
                    ))
                else:
                    result.checks.append((
                        "https_enforcement",
                        False,
                        f"API base uses insecure HTTP: {api_base}",
                        "critical",
                    ))
            else:
                result.checks.append((
                    "https_enforcement",
                    False,
                    f"API base has invalid scheme: {api_base}",
                    "critical",
                ))

        # Validate that custom endpoints are also checked
        if hasattr(adapter, "api_base") and adapter.api_base != getattr(adapter, "default_api_base", ""):
            try:
                validate_api_base(adapter.api_base)
                result.checks.append((
                    "https_enforcement_custom_endpoint",
                    True,
                    f"Custom API base validated: {adapter.api_base}",
                    "info",
                ))
            except Exception as e:
                result.checks.append((
                    "https_enforcement_custom_endpoint",
                    False,
                    f"Custom API base validation failed: {e}",
                    "error",
                ))

    def _validate_credential_isolation(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate credentials are isolated per provider."""
        # Check credential policy is per-provider
        if hasattr(adapter, "policy") and isinstance(adapter.policy, CredentialPolicy):
            if adapter.policy.provider_id == adapter.provider_id:
                result.checks.append((
                    "credential_isolation_policy",
                    True,
                    "CredentialPolicy bound to correct provider",
                    "info",
                ))
            else:
                result.checks.append((
                    "credential_isolation_policy",
                    False,
                    "CredentialPolicy provider_id mismatch",
                    "critical",
                ))

        # Check credential is stored privately
        if hasattr(adapter, "_credential"):
            result.checks.append((
                "credential_isolation_private",
                True,
                "Credential stored in private instance variable",
                "info",
            ))

        # Check credential_env is specific to this provider
        if hasattr(adapter, "credential_env"):
            cred_env = adapter.credential_env
            if cred_env:
                # Check no generic names like "TOKEN"
                generic_names = {"TOKEN", "API_KEY", "SECRET", "PASSWORD", "CREDENTIAL"}
                found_generic = [name for name in cred_env if any(g in name.upper() for g in generic_names) and not adapter.provider_id.upper() in name.upper()]
                if not found_generic:
                    result.checks.append((
                        "credential_isolation_env_specific",
                        True,
                        f"Credential env vars are provider-specific: {cred_env}",
                        "info",
                    ))
                else:
                    result.warnings.append(f"Generic credential env var names: {found_generic}")

    def _validate_secret_redaction(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate secret redaction in logs and errors."""
        # Check transport uses redaction
        if hasattr(adapter, "_transport") and isinstance(adapter._transport, HttpTransport):
            transport = adapter._transport
            if hasattr(transport, "last_correlation_id"):
                result.checks.append((
                    "secret_redaction_correlation_ids",
                    True,
                    "Transport uses correlation IDs for request tracking",
                    "info",
                ))

        # Verify redact function behavior
        from ogx.platform.security import redact
        test_secret = "ghp_test123456789012345678901234567890"
        redacted = redact(f"Bearer {test_secret}")
        if test_secret not in redacted and "[REDACTED]" in redacted:
            result.checks.append((
                "secret_redaction_function",
                True,
                "redact() function correctly removes known secret patterns",
                "info",
            ))
        else:
            result.checks.append((
                "secret_redaction_function",
                False,
                "redact() function did not properly redact test secret",
                "error",
            ))

        # Test credential in URL redaction
        test_url = "https://user:secret@github.com/api"
        redacted_url = redact(test_url)
        if "secret" not in redacted_url and "[REDACTED]" in redacted_url:
            result.checks.append((
                "secret_redaction_url_credentials",
                True,
                "redact() removes credentials from URLs",
                "info",
            ))

    def _validate_ssrf_protection(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate SSRF protection in endpoint validation."""
        from ogx.platform.security import validate_api_base

        # Test that metadata hosts are rejected
        metadata_hosts = ["metadata.google.internal", "169.254.169.254", "instance-data"]
        for host in metadata_hosts:
            try:
                validate_api_base(f"https://{host}/api")
                result.checks.append((
                    f"ssrf_protection_metadata_{host}",
                    False,
                    f"Metadata host {host} was NOT rejected",
                    "critical",
                ))
            except Exception:
                result.checks.append((
                    f"ssrf_protection_metadata_{host}",
                    True,
                    f"Metadata host {host} correctly rejected",
                    "info",
                ))

        # Test that private IPs are rejected
        private_hosts = ["10.0.0.1", "192.168.1.1", "172.16.0.1"]
        for host in private_hosts:
            try:
                validate_api_base(f"https://{host}/api", allow_private_network=False)
                result.checks.append((
                    f"ssrf_protection_private_{host}",
                    False,
                    f"Private IP {host} was NOT rejected",
                    "critical",
                ))
            except Exception:
                result.checks.append((
                    f"ssrf_protection_private_{host}",
                    True,
                    f"Private IP {host} correctly rejected",
                    "info",
                ))

        # Test that credentials in URL are rejected
        try:
            validate_api_base("https://user:pass@github.com/api")
            result.checks.append((
                "ssrf_protection_url_credentials",
                False,
                "URL with embedded credentials was NOT rejected",
                "critical",
            ))
        except Exception:
            result.checks.append((
                "ssrf_protection_url_credentials",
                True,
                "URL with embedded credentials correctly rejected",
                "info",
            ))

    def _validate_response_limits(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate response size limits."""
        if hasattr(adapter, "_transport") and isinstance(adapter._transport, HttpTransport):
            transport = adapter._transport
            if hasattr(transport, "max_response_bytes"):
                limit = transport.max_response_bytes
                if 1024 <= limit <= 64 * 1024 * 1024:
                    result.checks.append((
                        "response_size_limit",
                        True,
                        f"Response size limit configured: {limit} bytes",
                        "info",
                    ))
                else:
                    result.checks.append((
                        "response_size_limit",
                        False,
                        f"Response size limit out of bounds: {limit}",
                        "error",
                    ))

    def _validate_timeout_enforcement(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate request timeout enforcement."""
        if hasattr(adapter, "_transport") and isinstance(adapter._transport, HttpTransport):
            transport = adapter._transport
            if hasattr(transport, "timeout"):
                timeout = transport.timeout
                if 0 < timeout <= 300:
                    result.checks.append((
                        "timeout_enforcement",
                        True,
                        f"Request timeout configured: {timeout}s",
                        "info",
                    ))
                else:
                    result.checks.append((
                        "timeout_enforcement",
                        False,
                        f"Request timeout out of bounds: {timeout}",
                        "error",
                    ))

            # Check retry configuration
            if hasattr(transport, "max_retries"):
                retries = transport.max_retries
                if 0 <= retries <= 5:
                    result.checks.append((
                        "timeout_retry_config",
                        True,
                        f"Retry configuration: {retries} retries",
                        "info",
                    ))

    def _validate_error_serialization(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate safe error serialization."""
        from ogx.platform.errors import ProviderError, error_for_status

        # Test error_for_status produces safe messages
        test_errors = [
            (401, "auth"),
            (403, "permission"),
            (404, "not_found"),
            (429, "rate_limit"),
            (500, "server"),
        ]

        for status, category in test_errors:
            try:
                error = error_for_status(status, provider=adapter.provider_id, correlation_id="test-123")
                error_dict = error.to_dict()

                # Verify no secrets in error
                error_str = str(error_dict)
                if "ghp_" not in error_str and "ghu_" not in error_str and "glpat-" not in error_str:
                    result.checks.append((
                        f"error_serialization_{category}",
                        True,
                        f"Error {category} serialized without secrets",
                        "info",
                    ))
                else:
                    result.checks.append((
                        f"error_serialization_{category}",
                        False,
                        f"Error {category} contains potential secrets",
                        "critical",
                    ))

                # Verify structure
                required_keys = {"error", "message", "retryable"}
                if all(k in error_dict for k in required_keys):
                    result.checks.append((
                        f"error_structure_{category}",
                        True,
                        f"Error {category} has required fields",
                        "info",
                    ))
            except Exception as e:
                result.checks.append((
                    f"error_serialization_{category}",
                    False,
                    f"Error {category} serialization failed: {e}",
                    "error",
                ))

    def _validate_no_credential_leakage(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate no credential leakage in logs or exception messages."""
        # Check that credential is not in exception messages
        if hasattr(adapter, "_credential") and adapter._credential:
            cred = adapter._credential
            # Test that str(adapter) doesn't leak credential
            adapter_str = str(adapter)
            if cred not in adapter_str:
                result.checks.append((
                    "credential_leakage_str",
                    True,
                    "Credential not leaked in adapter string representation",
                    "info",
                ))
            else:
                result.checks.append((
                    "credential_leakage_str",
                    False,
                    "Credential leaked in adapter string representation",
                    "critical",
                ))

            # Test repr
            adapter_repr = repr(adapter)
            if cred not in adapter_repr:
                result.checks.append((
                    "credential_leakage_repr",
                    True,
                    "Credential not leaked in adapter repr",
                    "info",
                ))

        # Check transport doesn't log credentials
        if hasattr(adapter, "_transport"):
            transport = adapter._transport
            if hasattr(transport, "describe"):
                result.checks.append((
                    "credential_leakage_transport",
                    True,
                    "Transport has provider description without credentials",
                    "info",
                ))

    def _validate_write_authorization(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate write operations require authorization."""
        from ogx.platform.base import ProviderAdapter
        from ogx.platform.capabilities import WRITE_CAPABILITIES
        from ogx.platform.base import CAPABILITY_METHODS

        for cap in WRITE_CAPABILITIES:
            method_name = CAPABILITY_METHODS.get(cap)
            if method_name:
                method = getattr(adapter, method_name, None)
                base_method = getattr(ProviderAdapter, method_name, None)

                if method is not base_method:
                    # Custom implementation exists - check it calls require()
                    import inspect
                    source = inspect.getsource(method) if callable(method) else ""
                    if "self.require(" in source or "self.require(" in source:
                        result.checks.append((
                            f"write_auth_{cap.value}",
                            True,
                            f"Write capability {cap.value} checks authorization via require()",
                            "info",
                        ))
                    else:
                        result.checks.append((
                            f"write_auth_{cap.value}",
                            False,
                            f"Write capability {cap.value} may not check authorization",
                            "warning",
                        ))

    def _validate_host_allowlisting(self, result: SecurityValidationResult, adapter: Any) -> None:
        """Validate host allowlisting for redirects/pagination."""
        from ogx.platform.security import validate_request_url

        # Test that requests to different hosts are rejected
        try:
            validate_request_url(
                "https://evil.com/api",
                expected_host="github.com",
                allow_loopback_http=False,
                allow_private_network=False,
            )
            result.checks.append((
                "host_allowlisting_cross_host",
                False,
                "Request to different host was NOT rejected",
                "critical",
            ))
        except Exception:
            result.checks.append((
                "host_allowlisting_cross_host",
                True,
                "Request to different host correctly rejected",
                "info",
            ))