from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from .exceptions import (
    IdentityCorruptedError,
    IdentityMissingError,
    IdentityValidationError,
)
from .identity import read_identity, validate_identity_file
from .manifest_schema import (
    MAX_MANIFEST_FILE_SIZE,
    SUPPORTED_SCHEMA_VERSIONS,
    CURRENT_SCHEMA_VERSION,
    validate_manifest_json,
    apply_manifest_defaults,
    validate_utf8_file,
    validate_file_size,
    validate_no_secrets,
    validate_no_path_traversal,
    get_schema_for_version,
    apply_manifest_defaults,
)
from ..detector import detect_project
from ..registry import get_registry


class ManifestLocation(str, Enum):
    """Supported manifest file locations."""
    ROOT_JSON = "GUARDIAN-X.json"
    HIDDEN_DIR = ".guardian-x/manifest.json"


class ManifestConflictResolution(str, Enum):
    """How to resolve conflicting manifests."""
    ROOT_JSON_WINS = "root_json_wins"  # GUARDIAN-X.json takes precedence
    HIDDEN_DIR_WINS = "hidden_dir_wins"  # .guardian-x/manifest.json takes precedence
    REJECT = "reject"  # Reject and require manual resolution


@dataclass(frozen=True, slots=True)
class ManifestDiscoveryResult:
    """Result of manifest discovery."""
    has_manifest: bool
    location: ManifestLocation | None
    conflict: bool
    conflicting_locations: list[ManifestLocation] = field(default_factory=list)
    manifest_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ManifestValidationResult:
    """Result of manifest validation."""
    valid: bool
    schema_version: str | None
    data: dict[str, Any] | None
    defaults_applied: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TrustClassification(str, Enum):
    """Project trust classification."""
    UNKNOWN = "unknown"
    UNTRUSTED = "untrusted"
    REVIEWED = "reviewed"
    TRUSTED = "trusted"


class ActivationReadiness(str, Enum):
    """Project activation readiness status."""
    READY = "ready"
    READY_WITH_DEFAULTS = "ready_with_defaults"
    READY_WITH_WARNINGS = "ready_with_warnings"
    CONFIGURATION_INVALID = "configuration_invalid"
    IDENTITY_CONFLICT = "identity_conflict"
    UNSUPPORTED_MANIFEST = "unsupported_manifest"
    SECURITY_RESTRICTION = "security_restriction"


@dataclass(frozen=True, slots=True)
class ProjectConfiguration:
    """Resolved project configuration with all defaults applied."""
    manifest_data: dict[str, Any]
    project_profile: Any  # From detector
    identity: Any | None  # ProjectIdentity from identity.json
    registry_data: dict[str, Any] | None
    effective_config: dict[str, Any]
    source_priority: list[str]  # Ordered list of config sources applied
    trust_classification: TrustClassification
    activation_readiness: ActivationReadiness
    validation_warnings: list[str] = field(default_factory=list)


def discover_manifest(project_root: Path) -> ManifestDiscoveryResult:
    """
    Discover manifest files in project root.
    
    Returns discovery result with conflict detection.
    """
    project_root = project_root.expanduser().resolve()
    
    root_manifest = project_root / "GUARDIAN-X.json"
    hidden_manifest = project_root / ".guardian-x" / "manifest.json"
    
    root_exists = root_manifest.exists() and not root_manifest.is_symlink()
    hidden_exists = hidden_manifest.exists() and not hidden_manifest.is_symlink()
    
    if root_exists and hidden_exists:
        return ManifestDiscoveryResult(
            has_manifest=True,
            location=None,
            conflict=True,
            conflicting_locations=[ManifestLocation.ROOT_JSON, ManifestLocation.HIDDEN_DIR],
            manifest_path=None,
        )
    elif root_exists:
        return ManifestDiscoveryResult(
            has_manifest=True,
            location=ManifestLocation.ROOT_JSON,
            conflict=False,
            manifest_path=root_manifest,
        )
    elif hidden_exists:
        return ManifestDiscoveryResult(
            has_manifest=True,
            location=ManifestLocation.HIDDEN_DIR,
            conflict=False,
            manifest_path=hidden_manifest,
        )
    else:
        return ManifestDiscoveryResult(
            has_manifest=False,
            location=None,
            conflict=False,
            manifest_path=None,
        )


def resolve_manifest_conflict(
    project_root: Path,
    resolution: ManifestConflictResolution = ManifestConflictResolution.ROOT_JSON_WINS,
) -> Path | None:
    """Resolve manifest conflict according to policy."""
    if resolution == ManifestConflictResolution.ROOT_JSON_WINS:
        path = project_root / "GUARDIAN-X.json"
        if path.exists() and not path.is_symlink():
            return path
    elif resolution == ManifestConflictResolution.HIDDEN_DIR_WINS:
        path = project_root / ".guardian-x" / "manifest.json"
        if path.exists() and not path.is_symlink():
            return path
    elif resolution == ManifestConflictResolution.REJECT:
        return None
    return None


def load_and_validate_manifest(manifest_path: Path) -> ManifestValidationResult:
    """Load and validate a manifest file."""
    try:
        # Validate file size and encoding
        validate_file_size(manifest_path)
        validate_utf8_file(manifest_path)
        
        # Load JSON
        raw_text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return ManifestValidationResult(
            valid=False,
            schema_version=None,
            data=None,
            errors=[f"Invalid JSON: {e}"]
        )
    except Exception as e:
        return ManifestValidationResult(
            valid=False,
            schema_version=None,
            data=None,
            errors=[str(e)]
        )
    
    # Validate against schema
    try:
        validated_data = validate_manifest_json(data)
        schema_version = validated_data.get("schema_version", "unknown")
        
        # Apply defaults
        data_with_defaults = apply_manifest_defaults(validated_data)
        
        return ManifestValidationResult(
            valid=True,
            schema_version=schema_version,
            data=data_with_defaults,
            defaults_applied=True,
        )
    except Exception as e:
        return ManifestValidationResult(
            valid=False,
            schema_version=data.get("schema_version"),
            data=None,
            errors=[str(e)]
        )


def create_default_manifest_data(project_root: Path) -> dict[str, Any]:
    """Create default manifest data from project detection."""
    profile = detect_project(project_root)
    
    # Determine project type from detected languages/ecosystems
    project_type = "unknown"
    if "Python" in profile.languages:
        project_type = "python"
    elif "JavaScript/TypeScript" in profile.languages:
        project_type = "javascript"
    elif "Rust" in profile.languages:
        project_type = "rust"
    elif "Go" in profile.languages:
        project_type = "go"
    elif "Java" in profile.languages:
        project_type = "java"
    elif "C/C++" in profile.languages or "C#" in profile.languages:
        project_type = "c_cpp"
    
    default_manifest = {
        "schema_version": "1.0",
        "project": {
            "name": project_root.name,
            "description": f"Auto-detected {project_type} project",
        },
        "guardian_x": {
            "enabled": True,
            "mode": "automatic",
            "capabilities": {
                "discovery": True,
                "analysis": True,
                "monitoring": True,
                "maintenance": True,
                "engineering": True,
            }
        },
        "policies": {
            "execution": "never_without_authorization",
            "modification": "approval_required",
            "deletion": "approval_required",
            "deployment": "approval_required",
            "credentials": "never_store",
        },
        "hosts": {
            "supported": ["local", "github"]
        },
        "trust": {
            "classification": "untrusted"
        }
    }
    
    return default_manifest


def resolve_project_identity(
    project_root: Path,
    manifest_data: dict[str, Any] | None,
) -> tuple[Any | None, list[str]]:
    """
    Resolve project identity using identity.json, registry, and manifest.
    
    Returns (identity_object, warnings_list)
    """
    from ..registry import get_registry
    
    warnings = []
    identity = None
    
    # 1. Try identity.json (authoritative)
    try:
        identity = read_identity(project_root)
    except IdentityMissingError:
        pass
    except IdentityCorruptedError as e:
        warnings.append(f"Identity file corrupted: {e}")
    except IdentityValidationError as e:
        warnings.append(f"Identity validation failed: {e}")
    
    # 2. Check manifest project_id (supporting evidence)
    manifest_project_id = None
    if manifest_data and "project" in manifest_data:
        manifest_project_id = manifest_data["project"].get("project_id")
    
    # 3. Check registry for this project path
    registry = get_registry()
    
    if identity:
        # Verify manifest project_id matches identity.json if present
        if manifest_project_id and manifest_project_id != str(identity.project_id):
            warnings.append(
                f"Manifest project_id ({manifest_project_id}) "
                f"does not match identity.json ({identity.project_id})"
            )
    
    # 4. If no identity.json, try registry lookup
    if not identity:
        # Try to find project in registry by path
        pass  # Registry lookup would go here
    
    return identity, warnings


def compute_trust_classification(
    manifest_data: dict[str, Any] | None,
    identity: Any | None,
    warnings: list[str],
) -> TrustClassification:
    """Compute trust classification based on available evidence."""
    if warnings:
        # Any identity/validation warnings → untrusted at best
        return TrustClassification.UNTRUSTED
    
    if manifest_data and "trust" in manifest_data:
        classification = manifest_data["trust"].get("classification", "untrusted")
        try:
            return TrustClassification(classification)
        except ValueError:
            pass
    
    if identity:
        return TrustClassification.REVIEWED
    
    return TrustClassification.UNTRUSTED


def evaluate_activation_readiness(
    manifest_valid: bool,
    manifest_data: dict[str, Any] | None,
    identity: Any | None,
    validation_result: ManifestValidationResult | None,
    warnings: list[str],
    using_defaults: bool = False,
) -> ActivationReadiness:
    """Evaluate project activation readiness."""
    
    if not manifest_valid:
        if validation_result and validation_result.schema_version:
            if validation_result.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
                return ActivationReadiness.UNSUPPORTED_MANIFEST
        # If using defaults (no manifest or invalid manifest), not a configuration error
        if using_defaults:
            return ActivationReadiness.READY_WITH_DEFAULTS
        return ActivationReadiness.CONFIGURATION_INVALID
    
    if warnings:
        return ActivationReadiness.READY_WITH_WARNINGS
    
    # Valid manifest, no warnings
    return ActivationReadiness.READY


def resolve_project_configuration(
    project_root: Path,
    conflict_resolution: ManifestConflictResolution = ManifestConflictResolution.ROOT_JSON_WINS,
) -> ProjectConfiguration:
    """
    Main entry point: resolve complete project configuration.
    
    This is the main API for future automatic activation engine.
    """
    project_root = project_root.expanduser().resolve()
    
    # 1. Discover manifest
    discovery = discover_manifest(project_root)
    warnings = []
    
    manifest_path = None
    if discovery.has_manifest and not discovery.conflict:
        manifest_path = discovery.manifest_path
    elif discovery.conflict:
        warnings.append(
            f"Manifest conflict: {', '.join(l.value for l in discovery.conflicting_locations)}. "
            f"Using {conflict_resolution.value}."
        )
        manifest_path = resolve_manifest_conflict(project_root, conflict_resolution)
    
    # 2. Load and validate manifest (or create defaults)
    manifest_data = None
    validation_result = None
    if manifest_path:
        validation_result = load_and_validate_manifest(manifest_path)
        if validation_result.valid:
            manifest_data = validation_result.data
        else:
            # Invalid manifest - will use defaults
            pass
    
    # 3. Create defaults if no valid manifest
    if not manifest_data:
        manifest_data = create_default_manifest_data(project_root)
        validation_result = ManifestValidationResult(
            valid=False,
            schema_version="1.0",
            data=manifest_data,
            defaults_applied=True,
            warnings=["No valid manifest found; using auto-detected defaults"]
        )
    
    # 4. Detect project
    project_profile = detect_project(project_root)
    
    # 5. Resolve identity
    identity, identity_warnings = resolve_project_identity(project_root, manifest_data)
    warnings.extend(identity_warnings)
    
    # 6. Get registry data
    registry_data = None
    try:
        registry = get_registry()
        # Could look up by identity project_id
        if identity:
            pass  # Registry lookup would go here
    except Exception:
        pass
    
    # 7. Compute effective configuration with precedence
    effective_config = compute_effective_config(manifest_data, project_root)
    
    # 8. Compute trust classification
    trust = compute_trust_classification(manifest_data, identity, warnings)
    
    # 9. Evaluate activation readiness
    # using_defaults is True when we're using auto-detected defaults (no valid manifest)
    using_defaults = validation_result is None or not (validation_result and validation_result.valid)
    readiness = evaluate_activation_readiness(
        manifest_valid=validation_result.valid if validation_result else False,
        manifest_data=manifest_data,
        identity=identity,
        validation_result=validation_result,
        warnings=warnings,
        using_defaults=using_defaults,
    )
    
    # 9. Build source priority list
    source_priority = [
        "GUARDIAN-X security policies",
        "User-level configuration",
        "Host-level configuration",
        "Valid project manifest",
        "Automatically detected project characteristics",
        "Safe default configuration",
    ]
    
    return ProjectConfiguration(
        manifest_data=manifest_data,
        project_profile=project_profile,
        identity=identity,
        registry_data=registry_data,
        effective_config=effective_config,
        source_priority=source_priority,
        trust_classification=trust,
        activation_readiness=readiness,
        validation_warnings=warnings,
    )


def compute_effective_config(
    manifest_data: dict[str, Any] | None,
    project_root: Path,
) -> dict[str, Any]:
    """Compute effective configuration with precedence."""
    # Start with safe defaults
    config = {
        "guardian_x": {
            "enabled": True,
            "mode": "automatic",
            "capabilities": {
                "discovery": True,
                "analysis": True,
                "monitoring": True,
                "maintenance": True,
                "engineering": True,
            }
        },
        "policies": {
            "execution": "never_without_authorization",
            "modification": "approval_required",
            "deletion": "approval_required",
            "deployment": "approval_required",
            "credentials": "never_store",
        },
        "hosts": {
            "supported": ["local", "github"]
        },
        "trust": {
            "classification": "untrusted"
        }
    }
    
    # Apply manifest config (lower precedence than global policies)
    if manifest_data:
        if "guardian_x" in manifest_data:
            config["guardian_x"].update(manifest_data["guardian_x"])
        if "policies" in manifest_data:
            # Manifest policies can only restrict, not grant
            manifest_policies = manifest_data["policies"]
            for key, value in manifest_policies.items():
                # Only apply if more restrictive
                if key in config["policies"]:
                    current = config["policies"][key]
                    if is_more_restrictive(current, value, key):
                        config["policies"][key] = value
        if "hosts" in manifest_data:
            config["hosts"].update(manifest_data["hosts"])
        if "trust" in manifest_data:
            config["trust"].update(manifest_data["trust"])
    
    return config


def is_more_restrictive(current: str, proposed: str, policy_key: str) -> bool:
    """Check if proposed policy is more restrictive than current."""
    # Define restriction ordering for each policy (most restrictive first, index 0)
    restrictions = {
        "execution": ["disabled", "never_without_authorization", "requires_approval"],
        "modification": ["disabled", "approval_required"],
        "deletion": ["disabled", "approval_required"],
        "deployment": ["disabled", "approval_required"],
        "credentials": ["never_store", "external_only"],
    }
    
    if policy_key not in restrictions:
        return False
    
    order = restrictions[policy_key]
    try:
        current_idx = order.index(current)
        proposed_idx = order.index(proposed)
        return proposed_idx <= current_idx  # Lower index = more restrictive
    except ValueError:
        return False