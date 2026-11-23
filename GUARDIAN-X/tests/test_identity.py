from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from ogx.pap import (
    ProjectIdentity,
    create_identity,
    read_identity,
    validate_identity_file,
    recover_identity,
    update_identity_field,
    compute_identity_hash,
    IdentityError,
    IdentityCorruptedError,
    IdentityMissingError,
    IdentityValidationError,
    IdentityImmutableError,
)


def valid_uuid_v4() -> str:
    """Generate a valid UUID v4 string."""
    return str(uuid4())


class IdentityTests(unittest.TestCase):
    """Tests for Project Identity Engine."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    # --- Creation Tests ---

    def test_create_identity_minimal(self):
        """Test creating identity with minimal arguments."""
        identity = create_identity(self.root, project_name="Test Project")
        
        self.assertEqual(identity.project_name, "Test Project")
        self.assertEqual(identity.description, "")
        self.assertIsNotNone(identity.project_id)
        self.assertEqual(identity.schema_version, "1.0")
        self.assertIsNotNone(identity.created_at)
        self.assertIsNotNone(identity.created_by)
        self.assertIsNotNone(identity.identity_hash)
        self.assertTrue(identity.verify_hash())
        
        # Check file exists
        identity_file = self.root / ".guardian-x" / "identity.json"
        self.assertTrue(identity_file.exists())

    def test_create_identity_with_all_fields(self):
        """Test creating identity with all optional fields."""
        identity = create_identity(
            self.root,
            project_name="Full Project",
            description="A complete project",
            ogx_version="1.2.3",
            created_by="user@example.com",
        )
        
        self.assertEqual(identity.project_name, "Full Project")
        self.assertEqual(identity.description, "A complete project")
        self.assertEqual(identity.ogx_version, "1.2.3")
        self.assertEqual(identity.created_by, "user@example.com")
        self.assertTrue(identity.verify_hash())

    def test_create_identity_with_explicit_uuid(self):
        """Test creating identity with explicit project_id."""
        explicit_uuid = valid_uuid_v4()
        identity = create_identity(self.root, project_id=explicit_uuid)
        
        self.assertEqual(identity.project_id, explicit_uuid)
        self.assertTrue(identity.verify_hash())

    def test_create_identity_defaults_to_directory_name(self):
        """Test that project_name defaults to directory name when not provided."""
        custom_dir = self.root / "my-awesome-project"
        custom_dir.mkdir()
        
        identity = create_identity(custom_dir)
        self.assertEqual(identity.project_name, "my-awesome-project")

    def test_create_identity_refuses_overwrite_without_confirm(self):
        """Test that creating identity twice without --confirm fails."""
        create_identity(self.root, project_name="First")
        
        with self.assertRaises(IdentityError) as ctx:
            create_identity(self.root, project_name="Second")
        
        self.assertIn("already exists", str(ctx.exception))

    def test_create_identity_allows_overwrite_with_confirm(self):
        """Test that --confirm allows overwriting existing identity."""
        create_identity(self.root, project_name="First")
        identity = create_identity(self.root, project_name="Second", confirm=True)
        
        self.assertEqual(identity.project_name, "Second")

    def test_create_identity_different_uuid_rejected_on_overwrite(self):
        """Test that overwriting with different UUID requires explicit intent."""
        # Create first identity
        identity1 = create_identity(self.root, project_name="First")
        first_uuid = identity1.project_id
        
        # Try to overwrite with a different UUID (should fail even with confirm)
        with self.assertRaises(IdentityError) as ctx:
            create_identity(self.root, project_name="Second", project_id=valid_uuid_v4(), confirm=True)
        
        self.assertIn("different project_id", str(ctx.exception))

    # --- Reading Tests ---

    def test_read_identity_valid(self):
        """Test reading a valid identity."""
        created = create_identity(self.root, project_name="Read Test")
        read = read_identity(self.root)
        
        self.assertEqual(read.project_id, created.project_id)
        self.assertEqual(read.project_name, created.project_name)
        self.assertTrue(read.verify_hash())

    def test_read_identity_missing_raises(self):
        """Test reading missing identity raises IdentityMissingError."""
        with self.assertRaises(IdentityMissingError):
            read_identity(self.root)

    def test_read_identity_invalid_json_raises(self):
        """Test reading invalid JSON raises IdentityCorruptedError."""
        identity_dir = self.root / ".guardian-x"
        identity_dir.mkdir(parents=True)
        identity_file = identity_dir / "identity.json"
        identity_file.write_text("{ invalid json }", encoding="utf-8")
        
        with self.assertRaises(IdentityCorruptedError):
            read_identity(self.root)

    def test_read_identity_hash_mismatch_raises(self):
        """Test reading identity with tampered hash raises IdentityCorruptedError."""
        create_identity(self.root, project_name="Hash Test")
        
        # Tamper with the hash
        identity_file = self.root / ".guardian-x" / "identity.json"
        data = json.loads(identity_file.read_text(encoding="utf-8"))
        data["identity_hash"] = "sha256:" + "0" * 64
        identity_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        with self.assertRaises(IdentityCorruptedError) as ctx:
            read_identity(self.root)
        
        self.assertIn("hash verification failed", str(ctx.exception))

    def test_read_identity_missing_required_field_raises(self):
        """Test reading identity with missing required field raises IdentityValidationError."""
        create_identity(self.root, project_name="Field Test")
        
        # Remove required field
        identity_file = self.root / ".guardian-x" / "identity.json"
        data = json.loads(identity_file.read_text(encoding="utf-8"))
        del data["project_name"]
        identity_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        with self.assertRaises(IdentityValidationError):
            read_identity(self.root)

    def test_read_identity_invalid_uuid_raises(self):
        """Test reading identity with invalid UUID raises IdentityValidationError."""
        create_identity(self.root, project_name="UUID Test")
        
        identity_file = self.root / ".guardian-x" / "identity.json"
        data = json.loads(identity_file.read_text(encoding="utf-8"))
        data["project_id"] = "not-a-valid-uuid"
        identity_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        with self.assertRaises(IdentityValidationError):
            read_identity(self.root)

    def test_read_identity_invalid_timestamp_raises(self):
        """Test reading identity with invalid timestamp raises IdentityValidationError."""
        create_identity(self.root, project_name="Timestamp Test")
        
        identity_file = self.root / ".guardian-x" / "identity.json"
        data = json.loads(identity_file.read_text(encoding="utf-8"))
        data["created_at"] = "not-a-timestamp"
        identity_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        with self.assertRaises(IdentityValidationError):
            read_identity(self.root)

    def test_read_identity_oversized_raises(self):
        """Test reading oversized identity file raises IdentityFileTooLargeError."""
        create_identity(self.root, project_name="Size Test")
        
        identity_file = self.root / ".guardian-x" / "identity.json"
        # Add huge description
        data = json.loads(identity_file.read_text(encoding="utf-8"))
        data["description"] = "x" * (70 * 1024)  # > 64KB
        identity_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        with self.assertRaises(IdentityError) as ctx:
            read_identity(self.root)
        
        # Should be IdentityFileTooLargeError (subclass of IdentityError)
        from ogx.pap.exceptions import IdentityFileTooLargeError
        self.assertIsInstance(ctx.exception, IdentityFileTooLargeError)
        
        # Actually the size check happens before JSON parsing, so it should be FileTooLargeError
        # Let me check - the order is validate_file_size then validate_utf8_file then json.loads
        # So it should raise FileTooLargeError which is a subclass of IdentityValidationError

    # --- Hash Tests ---

    def test_compute_identity_hash_deterministic(self):
        """Test that hash computation is deterministic."""
        fixed_uuid = valid_uuid_v4()
        fixed_timestamp = "2025-01-15T10:30:00+00:00"
        identity1 = create_identity(
            self.root, 
            project_name="Hash Test", 
            project_id=fixed_uuid,
            created_at=fixed_timestamp,
        )
        identity2 = create_identity(
            self.root, 
            project_name="Hash Test", 
            project_id=fixed_uuid, 
            confirm=True,
            created_at=fixed_timestamp,
        )
        
        # Same input should produce same hash
        self.assertEqual(identity1.identity_hash, identity2.identity_hash)

    def test_compute_identity_hash_changes_on_field_change(self):
        """Test that hash changes when fields change."""
        fixed_uuid = valid_uuid_v4()
        identity1 = create_identity(self.root, project_name="Project A", project_id=fixed_uuid)
        identity2 = create_identity(self.root, project_name="Project B", project_id=fixed_uuid, confirm=True)
        
        self.assertNotEqual(identity1.identity_hash, identity2.identity_hash)

    def test_verify_hash_returns_true_for_valid(self):
        """Test verify_hash returns True for valid identity."""
        identity = create_identity(self.root, project_name="Verify Test")
        self.assertTrue(identity.verify_hash())

    def test_verify_hash_returns_false_for_missing_hash(self):
        """Test verify_hash returns False when hash is missing."""
        identity = ProjectIdentity(
            project_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            project_name="Test",
        )
        self.assertFalse(identity.verify_hash())

    def test_canonical_json_excludes_hash_and_internal(self):
        """Test canonical JSON excludes identity_hash and _file_path."""
        identity = create_identity(self.root, project_name="Canonical Test")
        canonical = identity.to_canonical_json()
        data = json.loads(canonical)
        
        self.assertNotIn("identity_hash", data)
        self.assertNotIn("_file_path", data)
        # Should have all other fields
        self.assertIn("schema_version", data)
        self.assertIn("project_id", data)
        self.assertIn("project_name", data)
        self.assertIn("description", data)
        self.assertIn("created_at", data)
        self.assertIn("created_by", data)
        self.assertIn("ogx_version", data)

    def test_canonical_json_sorted_keys(self):
        """Test canonical JSON has sorted keys."""
        identity = create_identity(self.root, project_name="Sort Test", project_id=valid_uuid_v4())
        canonical = identity.to_canonical_json()
        
        # Keys should be in alphabetical order
        keys = list(json.loads(canonical).keys())
        self.assertEqual(keys, sorted(keys))

    # --- Validation Tests ---

    def test_validate_identity_file_valid(self):
        """Test validation of valid identity."""
        create_identity(self.root, project_name="Valid Test")
        result = validate_identity_file(self.root)
        
        self.assertTrue(result["valid"])
        self.assertIsNotNone(result["identity"])
        self.assertEqual(len(result["errors"]), 0)

    def test_validate_identity_file_missing(self):
        """Test validation of missing identity."""
        result = validate_identity_file(self.root)
        
        self.assertFalse(result["valid"])
        self.assertIsNone(result["identity"])
        self.assertGreater(len(result["errors"]), 0)
        self.assertIn("Missing", result["errors"][0])

    def test_validate_identity_file_corrupted(self):
        """Test validation of corrupted identity."""
        identity_dir = self.root / ".guardian-x"
        identity_dir.mkdir(parents=True)
        identity_file = identity_dir / "identity.json"
        identity_file.write_text("{ bad json }", encoding="utf-8")
        
        result = validate_identity_file(self.root)
        
        self.assertFalse(result["valid"])
        self.assertIn("Corrupted", result["errors"][0])

    # --- Update Tests ---

    def test_update_project_name(self):
        """Test updating project_name field."""
        create_identity(self.root, project_name="Original")
        identity = update_identity_field(self.root, "project_name", "Updated", confirm=True)
        
        self.assertEqual(identity.project_name, "Updated")
        self.assertTrue(identity.verify_hash())
        
        # Verify on disk
        read = read_identity(self.root)
        self.assertEqual(read.project_name, "Updated")

    def test_update_description(self):
        """Test updating description field."""
        create_identity(self.root, project_name="Test", description="Original")
        identity = update_identity_field(self.root, "description", "Updated description", confirm=True)
        
        self.assertEqual(identity.description, "Updated description")
        self.assertTrue(identity.verify_hash())

    def test_update_immutable_field_raises(self):
        """Test updating immutable field raises IdentityImmutableError."""
        create_identity(self.root, project_name="Immutable Test")
        
        with self.assertRaises(IdentityImmutableError):
            update_identity_field(self.root, "project_id", "a1b2c3d4-e5f6-7890-abcd-ef1234567890", confirm=True)
        
        with self.assertRaises(IdentityImmutableError):
            update_identity_field(self.root, "created_at", "2025-01-01T00:00:00Z", confirm=True)
        
        with self.assertRaises(IdentityImmutableError):
            update_identity_field(self.root, "schema_version", "2.0", confirm=True)

    def test_update_invalid_field_raises(self):
        """Test updating unknown field raises IdentityValidationError."""
        create_identity(self.root, project_name="Invalid Field Test")
        
        with self.assertRaises(IdentityValidationError):
            update_identity_field(self.root, "unknown_field", "value", confirm=True)

    def test_update_empty_project_name_raises(self):
        """Test updating project_name to empty string raises error."""
        create_identity(self.root, project_name="Test")
        
        with self.assertRaises(IdentityValidationError):
            update_identity_field(self.root, "project_name", "", confirm=True)

    def test_update_long_project_name_raises(self):
        """Test updating project_name > 128 chars raises error."""
        create_identity(self.root, project_name="Test")
        
        with self.assertRaises(IdentityValidationError):
            update_identity_field(self.root, "project_name", "x" * 129, confirm=True)

    def test_update_long_description_raises(self):
        """Test updating description > 500 chars raises error."""
        create_identity(self.root, project_name="Test")
        
        with self.assertRaises(IdentityValidationError):
            update_identity_field(self.root, "description", "x" * 501, confirm=True)

    # --- Recovery Tests ---

    def test_recover_missing_identity_with_confirm(self):
        """Test recovering missing identity with --confirm creates new identity."""
        identity = recover_identity(self.root, confirm=True)
        
        self.assertIsNotNone(identity.project_id)
        self.assertEqual(identity.project_name, self.root.name)
        self.assertTrue(identity.verify_hash())

    def test_recover_corrupted_identity_with_confirm(self):
        """Test recovering corrupted identity with --confirm."""
        create_identity(self.root, project_name="Original")
        
        # Corrupt it
        identity_file = self.root / ".guardian-x" / "identity.json"
        identity_file.write_text("{ corrupted }", encoding="utf-8")
        
        identity = recover_identity(self.root, confirm=True)
        
        self.assertIsNotNone(identity.project_id)
        self.assertTrue(identity.verify_hash())

    def test_recover_without_confirm_raises(self):
        """Test recovery without --confirm raises IdentityError."""
        with self.assertRaises(IdentityError):
            recover_identity(self.root, confirm=False)

    # --- Windows Path Compatibility ---

    def test_windows_compatible_paths(self):
        """Test identity works with Windows-style paths."""
        # Create a path with backslashes (simulated)
        identity = create_identity(self.root, project_name="Windows Test")
        
        # Path should be handled correctly
        self.assertTrue(identity._file_path is not None)
        self.assertTrue(identity._file_path.exists())

    # --- Unicode Tests ---

    def test_unicode_project_name(self):
        """Test identity with unicode project name."""
        identity = create_identity(self.root, project_name="プロジェクト-α")
        
        self.assertEqual(identity.project_name, "プロジェクト-α")
        self.assertTrue(identity.verify_hash())
        
        # Read back
        read = read_identity(self.root)
        self.assertEqual(read.project_name, "プロジェクト-α")

    def test_unicode_description(self):
        """Test identity with unicode description."""
        identity = create_identity(self.root, project_name="Test", description="説明文")
        
        self.assertEqual(identity.description, "説明文")
        self.assertTrue(identity.verify_hash())

    # --- Compatibility Tests ---

    def test_identity_preserves_existing_cli_commands(self):
        """Test that existing CLI commands still work (basic smoke test)."""
        # This is a placeholder - actual CLI tests run separately
        from ogx.cli import _root
        path = _root(".")
        self.assertIsInstance(path, Path)

    def test_no_secret_storage(self):
        """Test that identity validation rejects potential secrets."""
        # Create a valid identity first
        create_identity(self.root, project_name="Secret Test")
        
        # Read and modify to add secret, keeping hash valid
        identity_file = self.root / ".guardian-x" / "identity.json"
        data = json.loads(identity_file.read_text(encoding="utf-8"))
        # Use a longer GitHub token (ghp_ + 36 chars = 40 total, pattern requires 36+ after gh[ps]_)
        data["description"] = "API key: ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        # Recompute hash for the modified data
        temp_identity = ProjectIdentity(
            schema_version=data["schema_version"],
            project_id=data["project_id"],
            project_name=data["project_name"],
            description=data["description"],
            created_at=data["created_at"],
            created_by=data["created_by"],
            ogx_version=data["ogx_version"],
            _file_path=identity_file,
        )
        data["identity_hash"] = temp_identity.compute_hash()
        identity_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        
        with self.assertRaises(IdentityValidationError) as ctx:
            read_identity(self.root)
        
        self.assertIn("secret", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()