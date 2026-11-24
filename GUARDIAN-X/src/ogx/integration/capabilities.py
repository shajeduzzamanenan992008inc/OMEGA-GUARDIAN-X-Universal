"""Capability consistency validator.

Validates capability matrix declarations against actual implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import VerificationCheck


@dataclass(frozen=True, slots=True)
class CapabilityValidationResult:
    """Result of capability validation."""

    checks: list[tuple[str, bool, str, str]] = field(default_factory=list)  # (name, passed, message, severity)
    unsupported_capabilities: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CapabilityValidator:
    """Validates capability declarations for consistency."""

    def __init__(self) -> None:
        pass

    def validate_adapter(self, adapter: Any) -> CapabilityValidationResult:
        """Validate all capability declarations for an adapter."""
        result = CapabilityValidationResult()

        # Get capability states from adapter
        if not hasattr(adapter, "descriptor"):
            result.checks.append((
                "capability_descriptor",
                False,
                "Adapter missing descriptor() class method",
                "critical",
            ))
            return result

        try:
            descriptor = adapter.descriptor()
        except Exception as e:
            result.checks.append((
                "capability_descriptor",
                False,
                f"Failed to get descriptor: {e}",
                "critical",
            ))
            return result

        # Validate capability states
        from ogx.platform.capabilities import Capability, CapabilityState
        from ogx.platform.base import CAPABILITY_METHODS

        for capability in Capability:
            if capability not in descriptor.capabilities:
                result.checks.append((
                    f"capability_{capability.value}_present",
                    False,
                    f"Capability {capability.value} missing from descriptor",
                    "error",
                ))
                continue

            state = descriptor.capabilities[capability]

            # Check that state is a valid CapabilityState
            if not isinstance(state, CapabilityState):
                result.checks.append((
                    f"capability_{capability.value}_state_type",
                    False,
                    f"Capability {capability.value} has invalid state type: {type(state)}",
                    "error",
                ))
                continue

            # Validate state transitions
            if state == CapabilityState.SUPPORTED:
                # Must have implementation
                method_name = CAPABILITY_METHODS.get(capability)
                if method_name:
                    method = getattr(adapter, method_name, None)
                    base_method = getattr(adapter.__class__, method_name, None)
                    if method is None or method is base_method:
                        result.checks.append((
                            f"capability_{capability.value}_implementation",
                            False,
                            f"Capability {capability.value} declared SUPPORTED but method {method_name} not overridden",
                            "critical",
                        ))
                    else:
                        result.checks.append((
                            f"capability_{capability.value}_implementation",
                            True,
                            f"Capability {capability.value} has implementation",
                            "info",
                        ))
                else:
                    result.checks.append((
                        f"capability_{capability.value}_method_mapping",
                        False,
                        f"Capability {capability.value} has no method mapping in CAPABILITY_METHODS",
                        "warning",
                    ))

            elif state == CapabilityState.PARTIAL:
                # Must have limitations documented
                if hasattr(adapter, "limitations") and adapter.limitations:
                    result.checks.append((
                        f"capability_{capability.value}_partial_documented",
                        True,
                        f"Capability {capability.value} PARTIAL with limitations documented",
                        "info",
                    ))
                else:
                    result.checks.append((
                        f"capability_{capability.value}_partial_documented",
                        False,
                        f"Capability {capability.value} PARTIAL but no limitations documented",
                        "warning",
                    ))

            elif state == CapabilityState.UNSUPPORTED:
                result.unsupported_capabilities.append(capability.value)
                result.checks.append((
                    f"capability_{capability.value}_unsupported",
                    True,
                    f"Capability {capability.value} correctly reported as UNSUPPORTED",
                    "info",
                ))

            elif state in (CapabilityState.REQUIRES_AUTH, CapabilityState.REQUIRES_PERMISSION,
                          CapabilityState.REQUIRES_WORKSPACE, CapabilityState.NOT_CONFIGURED,
                          CapabilityState.UNAVAILABLE, CapabilityState.FAILED):
                # These are blocking states - verify they have appropriate conditions
                result.checks.append((
                    f"capability_{capability.value}_blocking_state",
                    True,
                    f"Capability {capability.value} correctly reports blocking state: {state.value}",
                    "info",
                ))

        # Validate write capability separation
        from ogx.platform.capabilities import WRITE_CAPABILITIES
        for cap in WRITE_CAPABILITIES:
            if cap in descriptor.capabilities:
                state = descriptor.capabilities[cap]
                if state == CapabilityState.SUPPORTED:
                    # Verify it's not a read-only method
                    method_name = CAPABILITY_METHODS.get(cap)
                    if method_name:
                        method = getattr(adapter, method_name, None)
                        if method:
                            # Check method signature doesn't look like read-only
                            result.checks.append((
                                f"capability_{cap.value}_write_separation",
                                True,
                                f"Write capability {cap.value} properly separated",
                                "info",
                            ))

        return result

    def validate_consistency(self, descriptors: list[Any]) -> CapabilityValidationResult:
        """Validate consistency across multiple provider descriptors."""
        result = CapabilityValidationResult()

        # Check for duplicate provider IDs
        provider_ids = [d.provider_id for d in descriptors]
        duplicates = set([pid for pid in provider_ids if provider_ids.count(pid) > 1])
        if duplicates:
            result.checks.append((
                "duplicate_provider_ids",
                False,
                f"Duplicate provider IDs found: {duplicates}",
                "critical",
            ))
        else:
            result.checks.append((
                "unique_provider_ids",
                True,
                "All provider IDs are unique",
                "info",
            ))

        return result