from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ogx.pap import (
    ManifestLocation,
    ManifestConflictResolution,
    ManifestDiscoveryResult,
    ManifestValidationResult,
    TrustClassification,
    ActivationReadiness,
    ProjectConfiguration,
    discover_manifest,
    resolve_manifest_conflict,
    load_and_validate_manifest,
    create_default_manifest_data,
    resolve_project_identity,
    compute_trust_classification,
    evaluate_activation_readiness,
    resolve_project_configuration,
    compute_effective_config,
)
from ogx.pap.exceptions import IdentityValidationError


class ManifestTests(unittest.TestCase):
    """Tests for GX-PAP Manifest Engine."""
    
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
    
    def tearDown(self):
        self.temp_dir.cleanup()
    
    # --- Discovery Tests ---
    
    def test_discover_no_manifest(self):
        """Test discovery when no manifest exists."""
        result = discover_manifest(self.root)
        
        self.assertFalse(result.has_manifest)
        self.assertIsNone(result.location)
        self.assertFalse(result.conflict)
        self.assertIsNone(result.manifest_path)
    
    def test_discover_root_manifest(self):
        """Test discovery of GUARDIAN-X.json."""
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_file.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        result = discover_manifest(self.root)
        
        self.assertTrue(result.has_manifest)
        self.assertEqual(result.location, ManifestLocation.ROOT_JSON)
        self.assertFalse(result.conflict)
        self.assertEqual(result.manifest_path, manifest_file)
    
    def test_discover_hidden_manifest(self):
        """Test discovery of .guardian-x/manifest.json."""
        hidden_dir = self.root / ".guardian-x"
        hidden_dir.mkdir()
        manifest_file = hidden_dir / "manifest.json"
        manifest_file.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        result = discover_manifest(self.root)
        
        self.assertTrue(result.has_manifest)
        self.assertEqual(result.location, ManifestLocation.HIDDEN_DIR)
        self.assertFalse(result.conflict)
        self.assertEqual(result.manifest_path, manifest_file)
    
    def test_discover_conflicting_manifests(self):
        """Test discovery when both manifests exist."""
        root_manifest = self.root / "GUARDIAN-X.json"
        root_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        hidden_dir = self.root / ".guardian-x"
        hidden_dir.mkdir()
        hidden_manifest = hidden_dir / "manifest.json"
        hidden_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        result = discover_manifest(self.root)
        
        self.assertTrue(result.has_manifest)
        self.assertTrue(result.conflict)
        self.assertEqual(len(result.conflicting_locations), 2)
        self.assertIn(ManifestLocation.ROOT_JSON, result.conflicting_locations)
        self.assertIn(ManifestLocation.HIDDEN_DIR, result.conflicting_locations)
        self.assertIsNone(result.manifest_path)
    
    def test_discover_ignores_symlinks(self):
        """Test that symlinked manifests are ignored."""
        real_manifest = self.root / "real.json"
        real_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        symlink = self.root / "GUARDIAN-X.json"
        try:
            symlink.symlink_to(real_manifest)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on this platform")
        
        result = discover_manifest(self.root)
        
        self.assertFalse(result.has_manifest)
    
    # --- Conflict Resolution Tests ---
    
    def test_resolve_conflict_root_json_wins(self):
        """Test resolving conflict with ROOT_JSON_WINS."""
        root_manifest = self.root / "GUARDIAN-X.json"
        root_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        hidden_dir = self.root / ".guardian-x"
        hidden_dir.mkdir()
        hidden_manifest = hidden_dir / "manifest.json"
        hidden_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        resolved = resolve_manifest_conflict(self.root, ManifestConflictResolution.ROOT_JSON_WINS)
        
        self.assertEqual(resolved, root_manifest)
    
    def test_resolve_conflict_hidden_dir_wins(self):
        """Test resolving conflict with HIDDEN_DIR_WINS."""
        root_manifest = self.root / "GUARDIAN-X.json"
        root_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        hidden_dir = self.root / ".guardian-x"
        hidden_dir.mkdir()
        hidden_manifest = hidden_dir / "manifest.json"
        hidden_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        resolved = resolve_manifest_conflict(self.root, ManifestConflictResolution.HIDDEN_DIR_WINS)
        
        self.assertEqual(resolved, hidden_manifest)
    
    def test_resolve_conflict_reject(self):
        """Test resolving conflict with REJECT."""
        root_manifest = self.root / "GUARDIAN-X.json"
        root_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        hidden_dir = self.root / ".guardian-x"
        hidden_dir.mkdir()
        hidden_manifest = hidden_dir / "manifest.json"
        hidden_manifest.write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        resolved = resolve_manifest_conflict(self.root, ManifestConflictResolution.REJECT)
        
        self.assertIsNone(resolved)
    
    # --- Validation Tests ---
    
    def test_load_and_validate_valid_manifest(self):
        """Test loading and validating a valid manifest."""
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_content = {
            "schema_version": "1.0",
            "project": {"name": "Test Project"},
            "guardian_x": {"enabled": True, "mode": "automatic"},
        }
        manifest_file.write_text(json.dumps(manifest_content), encoding="utf-8")
        
        result = load_and_validate_manifest(manifest_file)
        
        self.assertTrue(result.valid)
        self.assertEqual(result.schema_version, "1.0")
        self.assertIsNotNone(result.data)
        self.assertTrue(result.defaults_applied)
        self.assertEqual(len(result.errors), 0)
    
    def test_load_and_validate_invalid_json(self):
        """Test loading invalid JSON."""
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_file.write_text("{ invalid json }", encoding="utf-8")
        
        result = load_and_validate_manifest(manifest_file)
        
        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Invalid JSON", result.errors[0])
    
    def test_load_and_validate_missing_schema_version(self):
        """Test validation fails with missing schema_version."""
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_file.write_text('{"project": {"name": "Test"}}', encoding="utf-8")
        
        result = load_and_validate_manifest(manifest_file)
        
        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("schema_version", result.errors[0].lower())
    
    def test_load_and_validate_unknown_schema_version(self):
        """Test validation fails with unknown schema version."""
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_file.write_text('{"schema_version": "2.0"}', encoding="utf-8")
        
        result = load_and_validate_manifest(manifest_file)
        
        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("unsupported", result.errors[0].lower())
    
    def test_load_and_validate_rejects_secrets(self):
        """Test validation rejects potential secrets."""
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_content = {
            "schema_version": "1.0",
            "project": {"name": "Test", "description": "API key: ghp_abcdefghijklmnopqrstuvwxyz1234567890"},
        }
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_file.write_text(json.dumps(manifest_content), encoding="utf-8")
        
        result = load_and_validate_manifest(manifest_file)
        
        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("secret", result.errors[0].lower())
    
    def test_load_and_validate_rejects_oversized_file(self):
        """Test validation rejects oversized manifest."""
        manifest_file = self.root / "GUARDIAN-X.json"
        # Create a manifest larger than 64KB
        large_desc = "x" * (70 * 1024)
        manifest_content = {
            "schema_version": "1.0",
            "project": {"name": "Test", "description": large_desc},
        }
        manifest_file.write_text(json.dumps(manifest_content), encoding="utf-8")
        
        result = load_and_validate_manifest(manifest_file)
        
        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("exceeds maximum size", result.errors[0])
    
    def test_load_and_validate_utf8_only(self):
        """Test validation requires UTF-8."""
        manifest_file = self.root / "GUARDIAN-X.json"
        # Write invalid UTF-8
        manifest_file.write_bytes(b'\xff\xfe\x00\x00')
        
        result = load_and_validate_manifest(manifest_file)
        
        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("utf-8", result.errors[0].lower())
    
    # --- Default Manifest Tests ---
    
    def test_create_default_manifest_data(self):
        """Test creating default manifest data from project detection."""
        # Create a Python project
        (self.root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
        
        default_manifest = create_default_manifest_data(self.root)
        
        self.assertEqual(default_manifest["schema_version"], "1.0")
        self.assertIn("project", default_manifest)
        self.assertIn("guardian_x", default_manifest)
        self.assertIn("policies", default_manifest)
        self.assertIn("hosts", default_manifest)
        self.assertIn("trust", default_manifest)
        self.assertEqual(default_manifest["project"]["name"], self.root.name)
    
    def test_default_manifest_has_safe_policies(self):
        """Test default manifest has safe policies."""
        default_manifest = create_default_manifest_data(self.root)
        
        policies = default_manifest["policies"]
        self.assertEqual(policies["execution"], "never_without_authorization")
        self.assertEqual(policies["modification"], "approval_required")
        self.assertEqual(policies["deletion"], "approval_required")
        self.assertEqual(policies["deployment"], "approval_required")
        self.assertEqual(policies["credentials"], "never_store")
    
    def test_default_manifest_trust_untrusted(self):
        """Test default manifest has untrusted classification."""
        default_manifest = create_default_manifest_data(self.root)
        
        self.assertEqual(default_manifest["trust"]["classification"], "untrusted")
    
    def test_default_manifest_has_automatic_mode(self):
        """Test default manifest has automatic mode."""
        default_manifest = create_default_manifest_data(self.root)
        
        self.assertEqual(default_manifest["guardian_x"]["mode"], "automatic")
        self.assertTrue(default_manifest["guardian_x"]["enabled"])
        self.assertTrue(default_manifest["guardian_x"]["capabilities"]["discovery"])
    
    # --- Identity Integration Tests ---
    
    def test_resolve_project_identity_no_identity(self):
        """Test identity resolution without identity.json."""
        identity, warnings = resolve_project_identity(self.root, None)
        
        self.assertIsNone(identity)
        self.assertEqual(len(warnings), 0)
    
    def test_resolve_project_identity_with_manifest_project_id(self):
        """Test identity resolution with manifest project_id."""
        manifest_data = {"project": {"project_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}
        identity, warnings = resolve_project_identity(self.root, manifest_data)
        
        self.assertIsNone(identity)
        self.assertEqual(len(warnings), 0)
    
    # --- Trust Classification Tests ---
    
    def test_compute_trust_classification_with_warnings(self):
        """Test trust classification with warnings returns untrusted."""
        trust = compute_trust_classification(None, None, ["warning"])
        
        self.assertEqual(trust, TrustClassification.UNTRUSTED)
    
    def test_compute_trust_classification_with_manifest_trust(self):
        """Test trust classification from manifest."""
        manifest_data = {"trust": {"classification": "reviewed"}}
        trust = compute_trust_classification(manifest_data, None, [])
        
        self.assertEqual(trust, TrustClassification.REVIEWED)
    
    def test_compute_trust_classification_with_identity(self):
        """Test trust classification with identity but no warnings."""
        from ogx.pap.identity import ProjectIdentity
        from uuid import UUID
        
        identity = ProjectIdentity(
            project_id=UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
            project_name="Test",
        )
        trust = compute_trust_classification(None, identity, [])
        
        self.assertEqual(trust, TrustClassification.REVIEWED)
    
    def test_compute_trust_classification_default_untrusted(self):
        """Test default trust classification is untrusted."""
        trust = compute_trust_classification(None, None, [])
        
        self.assertEqual(trust, TrustClassification.UNTRUSTED)
    
    # --- Activation Readiness Tests ---
    
    def test_evaluate_readiness_config_invalid(self):
        """Test readiness with invalid configuration."""
        readiness = evaluate_activation_readiness(
            manifest_valid=False,
            manifest_data=None,
            identity=None,
            validation_result=None,
            warnings=[],
        )
        
        self.assertEqual(readiness, ActivationReadiness.CONFIGURATION_INVALID)
    
    def test_evaluate_readiness_unsupported_manifest(self):
        """Test readiness with unsupported manifest version."""
        from ogx.pap.manifest_engine import ManifestValidationResult
        
        validation_result = ManifestValidationResult(
            valid=False,
            schema_version="2.0",
            data=None,
        )
        readiness = evaluate_activation_readiness(
            manifest_valid=False,
            manifest_data=None,
            identity=None,
            validation_result=validation_result,
            warnings=[],
        )
        
        self.assertEqual(readiness, ActivationReadiness.UNSUPPORTED_MANIFEST)
    
    def test_evaluate_readiness_with_warnings(self):
        """Test readiness with warnings."""
        readiness = evaluate_activation_readiness(
            manifest_valid=True,
            manifest_data={},
            identity=None,
            validation_result=None,
            warnings=["warning"],
        )
        
        self.assertEqual(readiness, ActivationReadiness.READY_WITH_WARNINGS)
    
    def test_evaluate_readiness_with_defaults_and_identity(self):
        """Test readiness with defaults and identity."""
        readiness = evaluate_activation_readiness(
            manifest_valid=False,
            manifest_data=None,
            identity="identity",
            validation_result=None,
            warnings=[],
            using_defaults=True,
        )
        
        self.assertEqual(readiness, ActivationReadiness.READY_WITH_DEFAULTS)
    
    def test_evaluate_readiness_ready(self):
        """Test readiness with valid manifest and no warnings."""
        readiness = evaluate_activation_readiness(
            manifest_valid=True,
            manifest_data={},
            identity="identity",
            validation_result=None,
            warnings=[],
        )
        
        self.assertEqual(readiness, ActivationReadiness.READY)
    
    # --- Configuration Resolution Tests ---
    
    def test_resolve_project_configuration_no_manifest(self):
        """Test resolving configuration without manifest."""
        (self.root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
        
        config = resolve_project_configuration(self.root)
        
        self.assertIsInstance(config, ProjectConfiguration)
        self.assertIsNotNone(config.manifest_data)
        self.assertIsNotNone(config.project_profile)
        self.assertEqual(config.activation_readiness, ActivationReadiness.READY_WITH_DEFAULTS)
        self.assertEqual(config.trust_classification, TrustClassification.UNTRUSTED)
        self.assertTrue(len(config.source_priority) >= 6)
    
    def test_resolve_project_configuration_with_valid_manifest(self):
        """Test resolving configuration with valid manifest."""
        manifest_file = self.root / "GUARDIAN-X.json"
        manifest_content = {
            "schema_version": "1.0",
            "project": {"name": "Test Project", "description": "A test"},
            "guardian_x": {"enabled": True, "mode": "automatic"},
        }
        (self.root / "GUARDIAN-X.json").write_text(json.dumps(manifest_content), encoding="utf-8")
        
        config = resolve_project_configuration(self.root)
        
        self.assertIsInstance(config, ProjectConfiguration)
        self.assertEqual(config.manifest_data["project"]["name"], "Test Project")
        self.assertEqual(config.activation_readiness, ActivationReadiness.READY)
    
    def test_resolve_project_configuration_manifest_conflict(self):
        """Test resolving configuration with manifest conflict."""
        (self.root / "GUARDIAN-X.json").write_text('{"schema_version": "1.0"}', encoding="utf-8")
        hidden_dir = self.root / ".guardian-x"
        hidden_dir.mkdir()
        (hidden_dir / "manifest.json").write_text('{"schema_version": "1.0"}', encoding="utf-8")
        
        config = resolve_project_configuration(self.root)
        
        self.assertTrue(any("conflict" in w.lower() for w in config.validation_warnings))
        # Should still resolve with default resolution (ROOT_JSON_WINS)
        self.assertIsInstance(config, ProjectConfiguration)
    
    # --- Effective Config Tests ---
    
    def test_compute_effective_config_defaults(self):
        """Test effective config with no manifest."""
        config = compute_effective_config(None, self.root)
        
        self.assertIn("guardian_x", config)
        self.assertIn("policies", config)
        self.assertIn("hosts", config)
        self.assertIn("trust", config)
        self.assertEqual(config["policies"]["execution"], "never_without_authorization")
    
    def test_compute_effective_config_manifest_overrides(self):
        """Test manifest overrides with higher precedence."""
        manifest_data = {
            "guardian_x": {"enabled": False, "mode": "manual"},
            "policies": {"execution": "disabled"},  # More restrictive
            "hosts": {"supported": ["local", "gitlab"]},
        }
        config = compute_effective_config(manifest_data, self.root)
        
        self.assertFalse(config["guardian_x"]["enabled"])
        self.assertEqual(config["guardian_x"]["mode"], "manual")
        self.assertEqual(config["policies"]["execution"], "disabled")  # More restrictive applied
        self.assertIn("gitlab", config["hosts"]["supported"])
    
    def test_compute_effective_config_policy_restriction_only(self):
        """Test manifest policies can only restrict, not grant."""
        # Current default is "never_without_authorization" (most restrictive)
        # Manifest tries to set "requires_approval" (less restrictive) - should be ignored
        manifest_data = {"policies": {"execution": "requires_approval"}}
        config = compute_effective_config(manifest_data, self.root)
        
        # Should keep the more restrictive default
        self.assertEqual(config["policies"]["execution"], "never_without_authorization")
    
    def test_compute_effective_config_can_restrict(self):
        """Test manifest can make policies more restrictive."""
        # Default is "approval_required", manifest can set "disabled" (more restrictive)
        manifest_data = {"policies": {"modification": "disabled"}}
        config = compute_effective_config(manifest_data, self.root)
        
        self.assertEqual(config["policies"]["modification"], "disabled")


if __name__ == "__main__":
    unittest.main()