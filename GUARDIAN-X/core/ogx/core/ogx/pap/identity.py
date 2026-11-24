from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .identity_schema import (
    CURRENT_SCHEMA_VERSION,
    MAX_IDENTITY_FILE_SIZE,
    validate_identity_json,
    validate_utf8_file,
    validate_file_size,
)
from .exceptions import (
    IdentityCorruptedError,
    IdentityError,
    IdentityImmutableError,
    IdentityMissingError,
    IdentityValidationError,
)


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    """
    Immutable project identity.
    
    The project_id is generated once and never changes.
    All other fields are set at creation time.
    """
    schema_version: str = CURRENT_SCHEMA_VERSION
    project_id: str = field(default_factory=lambda: str(uuid4()))
    project_name: str = ""
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    created_by: str = ""
    ogx_version: str = ""
    identity_hash: str = ""
    
    # Internal: not serialized
    _file_path: Path | None = field(default=None, repr=False, compare=False)
    
    def to_dict(self, include_hash: bool = True) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Remove internal fields
        data.pop("_file_path", None)
        if not include_hash:
            data.pop("identity_hash", None)
        return data
    
    def to_canonical_json(self) -> bytes:
        """
        Generate deterministic canonical JSON for hashing.
        
        Rules:
        - UTF-8 encoding
        - Sorted keys
        - No whitespace
        - Exclude identity_hash field
        - Exclude internal _file_path field
        """
        data = self.to_dict(include_hash=False)
        return json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    
    def compute_hash(self) -> str:
        """Compute SHA-256 hash of canonical identity content."""
        canonical = self.to_canonical_json()
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    
    def with_hash(self) -> ProjectIdentity:
        """Return new identity with computed hash."""
        return ProjectIdentity(
            schema_version=self.schema_version,
            project_id=self.project_id,
            project_name=self.project_name,
            description=self.description,
            created_at=self.created_at,
            created_by=self.created_by,
            ogx_version=self.ogx_version,
            identity_hash=self.compute_hash(),
            _file_path=self._file_path,
        )
    
    def verify_hash(self) -> bool:
        """Verify stored hash matches computed hash."""
        if not self.identity_hash:
            return False
        return self.identity_hash == self.compute_hash()


def get_default_created_by() -> str:
    """Get default actor identity from environment or system."""
    # Check environment variables
    for env_var in ["OGX_ACTOR_SUBJECT", "OGX_APPROVER_SUBJECT", "USER", "USERNAME"]:
        value = os.environ.get(env_var)
        if value:
            return value
    # Fallback to system user
    return os.environ.get("USER", os.environ.get("USERNAME", "local-user"))


def create_identity(
    project_root: Path,
    project_name: str | None = None,
    description: str = "",
    ogx_version: str = "",
    created_by: str | None = None,
    project_id: str | None = None,
    confirm: bool = False,
    created_at: str | None = None,  # For testing: deterministic timestamp
) -> ProjectIdentity:
    """
    Create a new project identity.
    
    Args:
        project_root: Project root directory
        project_name: Human-readable project name (defaults to directory name)
        description: Optional project description
        ogx_version: OGX version creating this identity
        created_by: Actor identity (defaults to environment/user)
        project_id: Optional explicit UUID (for recovery). If None, generates new UUID v4.
        confirm: If True, allow overwriting existing identity
        created_at: Optional explicit timestamp (ISO 8601 UTC) for deterministic creation. For testing only.
    
    Returns:
        ProjectIdentity with computed hash
    
    Raises:
        IdentityError: If identity already exists and confirm=False
        IdentityValidationError: If validation fails
    """
    project_root = project_root.expanduser().resolve()
    identity_dir = project_root / ".guardian-x"
    identity_file = identity_dir / "identity.json"
    
    # Check for existing identity
    if identity_file.exists():
        if not confirm:
            raise IdentityError(
                f"Identity already exists at {identity_file}. Use --confirm to overwrite.",
                details={"path": str(identity_file), "existing": True}
            )
        # Read existing to check if it's the same project
        try:
            existing = read_identity(project_root)
            if existing.project_id != (project_id or existing.project_id):
                raise IdentityError(
                    "Existing identity has different project_id. Overwrite requires explicit confirmation of replacement.",
                    details={
                        "existing_project_id": existing.project_id,
                        "requested_project_id": project_id,
                        "path": str(identity_file)
                    }
                )
        except (IdentityMissingError, IdentityCorruptedError, IdentityValidationError):
            # Existing identity is corrupted or unreadable, allow overwrite with confirm
            pass
    
    # Create identity directory
    identity_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate identity
    now = created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    identity = ProjectIdentity(
        schema_version=CURRENT_SCHEMA_VERSION,
        project_id=project_id or str(uuid4()),
        project_name=project_name or project_root.name,
        description=description,
        created_at=now,
        created_by=created_by or get_default_created_by(),
        ogx_version=ogx_version or "unknown",
        _file_path=identity_file,
    )
    
    # Compute and set hash
    identity = identity.with_hash()
    
    # Write atomically
    write_identity_atomic(identity_file, identity.to_dict())
    
    return identity


def write_identity_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write identity file atomically using temp file + rename."""
    # Validate before writing
    validate_identity_json(data)
    
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".identity-",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    
    try:
        # Atomic replace
        tmp_path.replace(path)
    finally:
        # Cleanup on failure
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def read_identity(project_root: Path) -> ProjectIdentity:
    """
    Read and validate project identity from .guardian-x/identity.json.
    
    Args:
        project_root: Project root directory
    
    Returns:
        Validated ProjectIdentity
    
    Raises:
        IdentityMissingError: If identity file doesn't exist
        IdentityCorruptedError: If file is corrupted (invalid JSON, hash mismatch)
        IdentityValidationError: If validation fails
    """
    project_root = project_root.expanduser().resolve()
    identity_file = project_root / ".guardian-x" / "identity.json"
    
    if not identity_file.exists():
        raise IdentityMissingError(
            f"Identity file not found: {identity_file}",
            path=str(identity_file)
        )
    
    validate_file_size(identity_file)
    validate_utf8_file(identity_file)
    
    try:
        raw_text = identity_file.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise IdentityCorruptedError(
            f"Invalid JSON in identity file: {e}",
            path=str(identity_file)
        ) from e
    
    if not isinstance(data, dict):
        raise IdentityCorruptedError(
            "Identity file must contain a JSON object",
            path=str(identity_file)
        )
    
    # Validate against schema
    validated_data = validate_identity_json(data)
    
    # Create identity object
    identity = ProjectIdentity(
        schema_version=validated_data["schema_version"],
        project_id=validated_data["project_id"],
        project_name=validated_data["project_name"],
        description=validated_data.get("description", ""),
        created_at=validated_data["created_at"],
        created_by=validated_data["created_by"],
        ogx_version=validated_data["ogx_version"],
        identity_hash=validated_data.get("identity_hash", ""),
        _file_path=identity_file,
    )
    
    # Verify hash integrity
    if not identity.verify_hash():
        raise IdentityCorruptedError(
            "Identity hash verification failed - file may have been tampered with",
            path=str(identity_file),
            details={
                "stored_hash": identity.identity_hash,
                "computed_hash": identity.compute_hash()
            }
        )
    
    return identity


def validate_identity_file(project_root: Path) -> dict[str, Any]:
    """
    Validate identity file without raising on corruption.
    
    Returns dict with validation result:
    {
        "valid": bool,
        "identity": ProjectIdentity | None,
        "errors": list[str],
        "warnings": list[str],
    }
    """
    result = {
        "valid": False,
        "identity": None,
        "errors": [],
        "warnings": [],
    }
    
    try:
        identity = read_identity(project_root)
        result["valid"] = True
        result["identity"] = identity
    except IdentityMissingError as e:
        result["errors"].append(f"Missing: {e}")
    except IdentityCorruptedError as e:
        result["errors"].append(f"Corrupted: {e}")
        if e.details:
            result["errors"].append(f"  Details: {e.details}")
    except IdentityValidationError as e:
        result["errors"].append(f"Validation failed: {e}")
        if e.field:
            result["errors"].append(f"  Field: {e.field}")
    except Exception as e:
        result["errors"].append(f"Unexpected error: {type(e).__name__}: {e}")
    
    return result


def compute_identity_hash(identity: ProjectIdentity) -> str:
    """Compute SHA-256 hash of canonical identity content."""
    return identity.compute_hash()


def recover_identity(
    project_root: Path,
    registry_identity: ProjectIdentity | None = None,
    manifest_project_id: str | None = None,
    confirm: bool = False,
) -> ProjectIdentity:
    """
    Recover identity from available evidence.
    
    Recovery priority:
    1. Valid identity.json (handled by read_identity)
    2. Valid registry evidence (if provided and matches)
    3. Explicit user-confirmed recovery
    4. Explicit user-confirmed new identity
    
    Args:
        project_root: Project root directory
        registry_identity: Identity from registry (if available)
        manifest_project_id: project_id from manifest.json (supporting evidence only)
        confirm: User confirmation for recovery/new identity
    
    Returns:
        Recovered ProjectIdentity
    
    Raises:
        IdentityError: If recovery not possible without user action
    """
    project_root = project_root.expanduser().resolve()
    identity_file = project_root / ".guardian-x" / "identity.json"
    
    # Try to read existing identity (handles valid case)
    try:
        return read_identity(project_root)
    except IdentityMissingError:
        pass  # Continue to recovery
    except IdentityCorruptedError as e:
        # Corrupted identity - need explicit recovery
        if not confirm:
            raise IdentityError(
                f"Identity file is corrupted and cannot be automatically recovered: {e}. "
                f"Run with --confirm to attempt recovery from registry/manifest or create new identity.",
                details={"path": str(identity_file), "corrupted": True}
            )
        # Fall through to recovery logic
    except IdentityValidationError as e:
        if not confirm:
            raise IdentityError(
                f"Identity validation failed: {e}. Use --confirm to attempt recovery.",
                details={"path": str(identity_file), "validation_error": str(e)}
            )
    
    # Recovery: try registry evidence
    if registry_identity:
        # Verify registry identity matches manifest (supporting evidence)
        if manifest_project_id and registry_identity.project_id != manifest_project_id:
            raise IdentityError(
                f"Registry identity project_id ({registry_identity.project_id}) "
                f"does not match manifest project_id ({manifest_project_id}). "
                f"Manual intervention required.",
                details={
                    "registry_project_id": registry_identity.project_id,
                    "manifest_project_id": manifest_project_id
                }
            )
        
        # Recreate identity.json from registry
        recovered = ProjectIdentity(
            schema_version=registry_identity.schema_version,
            project_id=registry_identity.project_id,
            project_name=registry_identity.project_name,
            description=registry_identity.description,
            created_at=registry_identity.created_at,
            created_by=registry_identity.created_by,
            ogx_version=registry_identity.ogx_version,
            _file_path=identity_file,
        ).with_hash()
        
        write_identity_atomic(identity_file, recovered.to_dict())
        return recovered
    
    # Recovery: try manifest project_id only (supporting evidence)
    if manifest_project_id:
        if not confirm:
            raise IdentityError(
                f"Only manifest project_id available ({manifest_project_id}). "
                f"Cannot verify identity integrity. Use --confirm to create new identity "
                f"with this project_id, or provide registry evidence.",
                details={"manifest_project_id": manifest_project_id}
            )
        
        # Create new identity with manifest project_id
        new_identity = ProjectIdentity(
            schema_version=CURRENT_SCHEMA_VERSION,
            project_id=manifest_project_id,
            project_name=project_root.name,
            description="Recovered from manifest project_id",
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            created_by=get_default_created_by(),
            ogx_version="recovery",
            _file_path=identity_file,
        ).with_hash()
        
        write_identity_atomic(identity_file, new_identity.to_dict())
        return new_identity
    
    # No recovery evidence - create new identity (requires confirm)
    if not confirm:
        raise IdentityError(
            "No identity file and no recovery evidence available. "
            "Use --confirm to create a new project identity.",
            details={"path": str(identity_file), "recovery_possible": False}
        )
    
    # Create new identity
    new_identity = create_identity(project_root, confirm=True)
    return new_identity


def update_identity_field(
    project_root: Path,
    field: str,
    value: Any,
    confirm: bool = False,
) -> ProjectIdentity:
    """
    Update a mutable field in identity.json.
    
    Immutable fields (require explicit replacement):
    - schema_version
    - project_id
    - created_at
    - created_by
    - ogx_version
    - identity_hash (computed)
    
    Mutable fields:
    - project_name
    - description
    """
    immutable_fields = {
        "schema_version", "project_id", "created_at", 
        "created_by", "ogx_version", "identity_hash"
    }
    
    if field in immutable_fields:
        raise IdentityImmutableError(
            f"Field '{field}' is immutable and cannot be modified. "
            f"To change this field, you must create a new identity.",
            field=field
        )
    
    if field not in {"project_name", "description"}:
        raise IdentityValidationError(
            f"Unknown or non-mutable field: {field}",
            field=field
        )
    
    identity = read_identity(project_root)
    
    # Update field
    if field == "project_name":
        if not value or not isinstance(value, str):
            raise IdentityValidationError("project_name must be a non-empty string", field=field)
        if len(value) > 128:
            raise IdentityValidationError("project_name exceeds 128 characters", field=field)
        new_identity = ProjectIdentity(
            schema_version=identity.schema_version,
            project_id=identity.project_id,
            project_name=value,
            description=identity.description,
            created_at=identity.created_at,
            created_by=identity.created_by,
            ogx_version=identity.ogx_version,
            _file_path=identity._file_path,
        )
    else:  # description
        if not isinstance(value, str):
            raise IdentityValidationError("description must be a string", field=field)
        if len(value) > 500:
            raise IdentityValidationError("description exceeds 500 characters", field=field)
        new_identity = ProjectIdentity(
            schema_version=identity.schema_version,
            project_id=identity.project_id,
            project_name=identity.project_name,
            description=value,
            created_at=identity.created_at,
            created_by=identity.created_by,
            ogx_version=identity.ogx_version,
            _file_path=identity._file_path,
        )
    
    new_identity = new_identity.with_hash()
    write_identity_atomic(identity._file_path, new_identity.to_dict())
    return new_identity