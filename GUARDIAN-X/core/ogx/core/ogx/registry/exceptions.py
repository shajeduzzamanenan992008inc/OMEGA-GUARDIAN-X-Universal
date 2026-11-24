from __future__ import annotations

from typing import Any


class RegistryError(Exception):
    """Base exception for registry errors."""
    
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class RegistryNotFoundError(RegistryError):
    """Raised when a registry record is not found."""
    
    def __init__(self, message: str, entity_type: str, entity_id: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.entity_type = entity_type
        self.entity_id = entity_id


class RegistryCorruptedError(RegistryError):
    """Raised when registry database is corrupted."""
    
    def __init__(self, message: str, path: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.path = path


class MigrationError(RegistryError):
    """Raised when migration fails."""
    
    def __init__(self, message: str, stage: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.stage = stage


class RegistryIntegrityError(RegistryError):
    """Raised when registry integrity check fails."""
    
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)