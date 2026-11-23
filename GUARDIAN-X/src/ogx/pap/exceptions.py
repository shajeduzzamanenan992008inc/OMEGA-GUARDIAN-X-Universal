from __future__ import annotations

from typing import Any


class IdentityError(Exception):
    """Base exception for identity-related errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class IdentityCorruptedError(IdentityError):
    """Raised when identity.json is corrupted (invalid JSON, hash mismatch, etc.)."""

    def __init__(self, message: str, path: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.path = path


class IdentityMissingError(IdentityError):
    """Raised when identity.json is missing and cannot be recovered."""

    def __init__(self, message: str, path: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.path = path


class IdentityValidationError(IdentityError):
    """Raised when identity validation fails (schema, UUID, timestamp, etc.)."""

    def __init__(self, message: str, field: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.field = field


class IdentityImmutableError(IdentityError):
    """Raised when attempting to modify an immutable identity field."""

    def __init__(self, message: str, field: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.field = field


class IdentityFileTooLargeError(IdentityError):
    """Raised when identity file exceeds maximum allowed size."""

    def __init__(self, message: str, size: int, max_size: int, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.size = size
        self.max_size = max_size