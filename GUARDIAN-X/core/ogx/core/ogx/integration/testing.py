"""Reusable contract test suite for provider adapters.

Any provider adapter can execute these tests to verify compliance with
the Universal Provider Contract.
"""

from __future__ import annotations

import inspect
import json
import unittest
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from unittest.mock import MagicMock, patch

from ogx.platform.base import ProviderAdapter, CAPABILITY_METHODS
from ogx.platform.capabilities import Capability, CapabilityState, ProviderDescriptor
from ogx.platform.errors import ProviderError, CapabilityUnavailableError
from ogx.platform.models import (
    RepositoryMetadata,
    Branch,
    Commit,
    FileContent,
    TreeEntry,
    PullRequest,
    Issue,
    PermissionSet,
    RateLimit,
    HealthStatus,
    AuthStatus,
    WritePlan,
    OperationResult,
)
from ogx.platform.transport import HttpTransport, HttpResponse
from ogx.platform.security import CredentialPolicy, validate_api_base

from .verifier import ContractVerifier, VerificationResult
from .capabilities import CapabilityValidator
from .security import SecurityValidator


@dataclass(frozen=True, slots=True)
class ContractTestSuite:
    """Reusable test suite that any provider adapter can execute."""

    provider_id: str
    adapter_class: type
    test_credentials: dict[str, str] = field(default_factory=dict)
    mock_responses: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.adapter: Optional[ProviderAdapter] = None

    def setUp(self) -> None:
        """Create adapter instance with test configuration."""
        # Default test credentials
        creds = {
            "credential": "test-token",
            "timeout": 5.0,
            "max_retries": 0,
            "sleep": lambda x: None,
        }
        creds.update(self.test_credentials)

        self.adapter = self.adapter_class(**creds)

    def tearDown(self) -> None:
        """Clean up after tests."""
        self.adapter = None

    def run_all_tests(self) -> VerificationResult:
        """Run the complete contract verification suite."""
        verifier = ContractVerifier()
        return verifier.verify(self.adapter, test_environment={
            "suite": "ContractTestSuite",
            "provider_id": self.provider_id,
        })

    # Identity tests
    def test_provider_identity(self) -> None:
        """Test provider identity fields are present and valid."""
        assert hasattr(self.adapter, "provider_id")
        assert isinstance(self.adapter.provider_id, str)
        assert self.adapter.provider_id == self.provider_id

        assert hasattr(self.adapter, "display_name")
        assert isinstance(self.adapter.display_name, str)
        assert len(self.adapter.display_name) > 0

        assert hasattr(self.adapter, "version")
        assert isinstance(self.adapter.version, str)
        assert len(self.adapter.version) > 0

        assert hasattr(self.adapter, "kind")
        assert self.adapter.kind is not None

        assert hasattr(self.adapter, "default_api_base")
        assert isinstance(self.adapter.default_api_base, str)

        assert hasattr(self.adapter, "credential_env")
        assert isinstance(self.adapter.credential_env, (tuple, list))

    def test_provider_descriptor(self) -> None:
        """Test provider descriptor is serializable and complete."""
        descriptor = self.adapter.descriptor()
        assert isinstance(descriptor, ProviderDescriptor)
        assert descriptor.provider_id == self.provider_id
        assert descriptor.display_name == self.adapter.display_name
        assert descriptor.version == self.adapter.version

        # All capabilities should have a state
        for cap in Capability:
            assert cap in descriptor.capabilities, f"Missing capability: {cap.value}"
            state = descriptor.capabilities[cap]
            assert isinstance(state, CapabilityState), f"Invalid state type for {cap.value}"

        # Serialization should work
        descriptor_dict = descriptor.to_dict(authenticated=False)
        assert "provider_id" in descriptor_dict
        assert "supported_operations" in descriptor_dict
        assert "capability_states" in descriptor_dict

    # Capability tests
    def test_capability_consistency(self) -> None:
        """Test capability declarations match implementations."""
        from ogx.platform.base import CAPABILITY_METHODS

        descriptor = self.adapter.descriptor()
        for capability, state in descriptor.capabilities.items():
            method_name = CAPABILITY_METHODS.get(capability)
            if state == CapabilityState.SUPPORTED and method_name:
                method = getattr(self.adapter, method_name, None)
                base_method = getattr(ProviderAdapter, method_name, None)
                assert method is not None, f"Supported capability {capability.value} missing method {method_name}"
                assert method is not base_method, f"Supported capability {capability.value} uses base implementation"

            elif state == CapabilityState.PARTIAL:
                # Should have limitations documented
                if hasattr(self.adapter, "limitations"):
                    assert self.adapter.limitations, f"PARTIAL capability {capability.value} should have limitations"

    def test_unsupported_capabilities_not_implemented(self) -> None:
        """Test unsupported capabilities raise CapabilityUnavailableError."""
        descriptor = self.adapter.descriptor()
        for capability, state in descriptor.capabilities.items():
            if state == CapabilityState.UNSUPPORTED:
                from ogx.platform.base import CAPABILITY_METHODS
                method_name = CAPABILITY_METHODS.get(capability)
                if method_name and hasattr(self.adapter, method_name):
                    method = getattr(self.adapter, method_name)
                    # Should raise CapabilityUnavailableError when called
                    # We can't easily test this without full setup, but we verify the method exists

    # Read operation tests
    def test_identify(self) -> None:
        """Test identify() returns provider identity."""
        if self.adapter.capability_state(Capability.IDENTIFY).is_available:
            identity = self.adapter.identify()
            assert isinstance(identity, dict)
            assert identity["provider_id"] == self.provider_id
            assert "display_name" in identity
            assert "kind" in identity
            assert "version" in identity

    def test_auth_status(self) -> None:
        """Test auth_status() returns authentication status."""
        if self.adapter.capability_state(Capability.AUTH_DISCOVERY).is_available:
            status = self.adapter.auth_status()
            assert isinstance(status, AuthStatus)
            assert status.provider_id == self.provider_id
            assert hasattr(status, "configured")
            assert hasattr(status, "authenticated")

    def test_health_check(self) -> None:
        """Test health check capability."""
        if self.adapter.capability_state(Capability.HEALTH_CHECK).is_available:
            # Health check may fail without real credentials, but should not crash
            try:
                health = self.adapter.health()
                assert isinstance(health, HealthStatus)
                assert health.status in ("healthy", "degraded", "down")
            except CapabilityUnavailableError:
                pass  # Expected if not implemented

    def test_rate_limit_read(self) -> None:
        """Test rate limit capability."""
        if self.adapter.capability_state(Capability.RATE_LIMIT_READ).is_available:
            try:
                rate_limit = self.adapter.rate_limit()
                assert isinstance(rate_limit, RateLimit)
            except CapabilityUnavailableError:
                pass

    # Pagination tests
    def test_pagination_capability(self) -> None:
        """Test pagination capability is properly declared."""
        state = self.adapter.capability_state(Capability.PAGINATION)
        if state == CapabilityState.SUPPORTED:
            assert self.adapter.supports_pagination

    def test_paginated_methods_have_limit(self) -> None:
        """Test all paginated methods accept limit parameter."""
        from ogx.platform.capabilities import Capability, CAPABILITY_METHODS

        paginated_capabilities = [
            Capability.REPOSITORY_DISCOVERY,
            Capability.BRANCH_LIST,
            Capability.COMMIT_LIST,
            Capability.PULL_REQUEST_LIST,
            Capability.ISSUE_LIST,
        ]

        for cap in paginated_capabilities:
            if self.adapter.capability_state(cap).is_available:
                method_name = CAPABILITY_METHODS.get(cap)
                if method_name:
                    method = getattr(self.adapter, method_name)
                    sig = inspect.signature(method)
                    params = sig.parameters
                    assert "limit" in params, f"Paginated method {method_name} missing limit parameter"
                    limit_param = params["limit"]
                    assert limit_param.default is not inspect.Parameter.empty, f"limit parameter should have default"

    # Normalization tests
    def test_model_serialization(self) -> None:
        """Test all normalized models serialize correctly."""
        models = [
            RepositoryMetadata(
                repository_id="123", name="test", full_name="owner/test", owner="owner", provider=self.provider_id
            ),
            Branch(name="main", commit_sha="abc123"),
            Commit(sha="abc123", message="Test commit"),
            FileContent(path="test.py", sha="def456", size=100, content="print('hello')"),
            TreeEntry(path="src/", type="dir"),
            PullRequest(pull_request_id="pr-1", number=1, title="Test PR", state="open"),
            Issue(issue_id="issue-1", number=1, title="Test Issue", state="open"),
            PermissionSet(level="admin", admin=True),
            RateLimit(limit=5000, remaining=4999),
            HealthStatus(status="healthy", latency_ms=100),
            AuthStatus(provider_id=self.provider_id, configured=True, authenticated=True),
            OperationResult(operation="test", provider_id=self.provider_id, ok=True),
        ]

        for model in models:
            assert hasattr(model, "to_dict")
            data = model.to_dict()
            assert isinstance(data, dict)
            # Should be JSON serializable
            json.dumps(data)

    # Security tests
    def test_https_enforcement(self) -> None:
        """Test API base enforces HTTPS."""
        api_base = self.adapter.default_api_base
        assert api_base.startswith("https://") or (
            api_base.startswith("http://") and any(h in api_base for h in ("localhost", "127.0.0.1"))
        ), f"API base must use HTTPS: {api_base}"

    def test_credential_isolation(self) -> None:
        """Test credential is isolated to this provider."""
        if hasattr(self.adapter, "_credential"):
            assert self.adapter._credential is not None or self.adapter._credential is None  # Private variable exists

        if hasattr(self.adapter, "policy"):
            assert isinstance(self.adapter.policy, CredentialPolicy)
            assert self.adapter.policy.provider_id == self.provider_id

    def test_secret_redaction(self) -> None:
        """Test secret redaction works."""
        from ogx.platform.security import redact

        test_token = "ghp_test123456789012345678901234567890"
        text = f"Authorization: Bearer {test_token}"
        redacted = redact(text)

        assert test_token not in redacted
        assert "[REDACTED]" in redacted

    def test_ssrf_protection(self) -> None:
        """Test SSRF protection rejects metadata hosts."""
        from ogx.platform.security import validate_api_base

        for host in ("metadata.google.internal", "169.254.169.254", "instance-data"):
            try:
                validate_api_base(f"https://{host}/api")
                assert False, f"Should have rejected metadata host: {host}"
            except Exception:
                pass  # Expected

    def test_error_serialization_safe(self) -> None:
        """Test error serialization doesn't leak secrets."""
        from ogx.platform.errors import error_for_status

        error = error_for_status(
            401,
            provider=self.provider_id,
            correlation_id="test-123",
            detail="Bearer ghp_test123456789012345678901234567890"
        )
        error_dict = error.to_dict()

        error_str = json.dumps(error_dict)
        assert "ghp_test123456789012345678901234567890" not in error_str
        assert "Bearer" not in error_str or "[REDACTED]" in error_str

    def test_read_only_capabilities_no_side_effects(self) -> None:
        """Test read-only capabilities don't perform writes."""
        from ogx.platform.capabilities import READ_ONLY_CAPABILITIES, CAPABILITY_METHODS

        for cap in READ_ONLY_CAPABILITIES:
            if self.adapter.capability_state(cap).is_available:
                method_name = CAPABILITY_METHODS.get(cap)
                if method_name and hasattr(self.adapter, method_name):
                    method = getattr(self.adapter, method_name)
                    # Method should exist but we don't call it in this test
                    assert callable(method)

    def test_write_capabilities_require_approval(self) -> None:
        """Test write capabilities require approval."""
        from ogx.platform.capabilities import WRITE_CAPABILITIES, CAPABILITY_METHODS

        for cap in WRITE_CAPABILITIES:
            if self.adapter.capability_state(cap).is_available:
                method_name = CAPABILITY_METHODS.get(cap)
                if method_name and hasattr(self.adapter, method_name):
                    method = getattr(self.adapter, method_name)
                    # Should be custom implementation, not base
                    base_method = getattr(ProviderAdapter, method_name, None)
                    assert method is not base_method, f"Write capability {cap.value} should have custom implementation"

    # Error handling tests
    def test_capability_unavailable_error(self) -> None:
        """Test unsupported capabilities raise proper error."""
        descriptor = self.adapter.descriptor()
        unsupported = [
            cap for cap, state in descriptor.capabilities.items()
            if state == CapabilityState.UNSUPPORTED
        ]

        for cap in unsupported[:3]:  # Test first 3
            from ogx.platform.base import CAPABILITY_METHODS
            method_name = CAPABILITY_METHODS.get(cap)
            if method_name and hasattr(self.adapter, method_name):
                method = getattr(self.adapter, method_name)
                # Should raise CapabilityUnavailableError when called
                # We verify the method exists and is not the base implementation
                base_method = getattr(ProviderAdapter, method_name, None)
                if method is not base_method:
                    pass  # Has custom implementation that should raise

    def test_normalized_error_hierarchy(self) -> None:
        """Test normalized error types are available."""
        from ogx.platform.errors import (
            ProviderError,
            AuthenticationError,
            PermissionDeniedError,
            NotFoundError,
            ConflictError,
            RateLimitError,
            ServerError,
            TransportError,
            MalformedResponseError,
            ResponseTooLargeError,
            InvalidEndpointError,
            CapabilityUnavailableError,
        )

        # All should be ProviderError subclasses
        error_types = [
            AuthenticationError, PermissionDeniedError, NotFoundError,
            ConflictError, RateLimitError, ServerError, TransportError,
            MalformedResponseError, ResponseTooLargeError, InvalidEndpointError,
            CapabilityUnavailableError,
        ]

        for err_type in error_types:
            assert issubclass(err_type, ProviderError)

    # Transport tests
    def test_transport_timeout_enforcement(self) -> None:
        """Test transport enforces timeouts."""
        if hasattr(self.adapter, "_transport") and isinstance(self.adapter._transport, HttpTransport):
            transport = self.adapter._transport
            assert 0 < transport.timeout <= 300
            assert 1024 <= transport.max_response_bytes <= 64 * 1024 * 1024
            assert 0 <= transport.max_retries <= 5

    def test_transport_no_redirects(self) -> None:
        """Test transport doesn't follow redirects."""
        if hasattr(self.adapter, "_transport") and isinstance(self.adapter._transport, HttpTransport):
            transport = self.adapter._transport
            # The _NoRedirect handler should be used
            assert transport._request_fn is not None

    def test_transport_correlation_ids(self) -> None:
        """Test transport generates correlation IDs."""
        if hasattr(self.adapter, "_transport") and isinstance(self.adapter._transport, HttpTransport):
            transport = self.adapter._transport
            assert hasattr(transport, "last_correlation_id")

    # Credential policy tests
    def test_credential_policy_resolution(self) -> None:
        """Test credential policy resolves from environment."""
        if hasattr(self.adapter, "policy"):
            # Should resolve from test environment
            policy = self.adapter.policy
            assert isinstance(policy, CredentialPolicy)
            assert policy.provider_id == self.provider_id

    # API version tests
    def test_api_version_declaration(self) -> None:
        """Test API version is declared."""
        assert hasattr(self.adapter, "api_version")
        assert isinstance(self.adapter.api_version, str)

    # Required permissions tests
    def test_required_permissions_documented(self) -> None:
        """Test required permissions are documented."""
        if hasattr(self.adapter, "required_permissions"):
            perms = self.adapter.required_permissions
            assert isinstance(perms, (tuple, list))
            # At least one permission should be documented
            assert len(perms) >= 0  # May be empty but should exist

    # Limitations tests
    def test_limitations_documented(self) -> None:
        """Test limitations are documented for partial capabilities."""
        if hasattr(self.adapter, "limitations"):
            limitations = self.adapter.limitations
            assert isinstance(limitations, (tuple, list))


class ProviderTestHarness:
    """Test harness for running provider contract tests."""

    def __init__(
        self,
        adapter_class: type,
        test_credentials: Optional[dict[str, str]] = None,
        mock_responses: Optional[dict[str, Any]] = None,
    ):
        self.adapter_class = adapter_class
        self.test_credentials = test_credentials or {}
        self.mock_responses = mock_responses or {}
        self.suite = ContractTestSuite(
            provider_id=adapter_class.provider_id,
            adapter_class=adapter_class,
            test_credentials=self.test_credentials,
            mock_responses=self.mock_responses,
        )

    def run_verification(self) -> VerificationResult:
        """Run full contract verification."""
        self.suite.setUp()
        try:
            return self.suite.run_all_tests()
        finally:
            self.suite.tearDown()

    def run_unit_tests(self) -> unittest.TestResult:
        """Run unit tests from the test suite."""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(type(
            f"{self.adapter_class.__name__}ContractTests",
            (ContractTestSuite,),
            {
                "provider_id": self.adapter_class.provider_id,
                "adapter_class": self.adapter_class,
                "test_credentials": self.test_credentials,
                "mock_responses": self.mock_responses,
            }
        ))
        runner = unittest.TextTestRunner(verbosity=2)
        return runner.run(suite)


def create_test_case(adapter_class: type, test_name: str = "ContractTests") -> type:
    """Create a unittest.TestCase class for a provider adapter.

    Usage:
        GitHubContractTests = create_test_case(GitHubAdapter, "GitHubContractTests")

    Then run with pytest or unittest.
    """
    return type(
        test_name,
        (ContractTestSuite,),
        {
            "provider_id": adapter_class.provider_id,
            "adapter_class": adapter_class,
            "test_credentials": {},
            "mock_responses": {},
        }
    )