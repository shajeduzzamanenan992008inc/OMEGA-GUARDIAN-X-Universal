"""Typed host lifecycle: states, sessions, adapters and the session manager.

The manager is synchronous on purpose: local host operations are bounded OS
calls, and a sync contract keeps CLI, tests and adapters deterministic. Live
session state is process-local; only coarse status is persisted.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from .capabilities import CAPABILITY_MATRIX
from .registry import get_host_registry


class HostLifecycleState(str, Enum):
    UNREGISTERED = "unregistered"
    REGISTERED = "registered"
    CONFIGURED = "configured"
    VALIDATING = "validating"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"
    RECOVERING = "recovering"
    FAILED = "failed"
    CLOSED = "closed"


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


ACTIVE_STATES = frozenset({
    HostLifecycleState.REGISTERED,
    HostLifecycleState.CONFIGURED,
    HostLifecycleState.VALIDATING,
    HostLifecycleState.CONNECTING,
    HostLifecycleState.CONNECTED,
    HostLifecycleState.DEGRADED,
    HostLifecycleState.DISCONNECTING,
    HostLifecycleState.RECOVERING,
})

TERMINAL_STATES = frozenset({
    HostLifecycleState.DISCONNECTED,
    HostLifecycleState.FAILED,
    HostLifecycleState.CLOSED,
})

ALLOWED_TRANSITIONS: Dict[HostLifecycleState, frozenset[HostLifecycleState]] = {
    HostLifecycleState.UNREGISTERED: frozenset({HostLifecycleState.REGISTERED}),
    HostLifecycleState.REGISTERED: frozenset({
        HostLifecycleState.CONFIGURED,
        HostLifecycleState.FAILED,
        HostLifecycleState.CLOSED,
    }),
    HostLifecycleState.CONFIGURED: frozenset({
        HostLifecycleState.VALIDATING,
        HostLifecycleState.FAILED,
        HostLifecycleState.CLOSED,
    }),
    HostLifecycleState.VALIDATING: frozenset({
        HostLifecycleState.CONNECTING,
        HostLifecycleState.FAILED,
    }),
    HostLifecycleState.CONNECTING: frozenset({
        HostLifecycleState.CONNECTED,
        HostLifecycleState.FAILED,
    }),
    HostLifecycleState.CONNECTED: frozenset({
        HostLifecycleState.DEGRADED,
        HostLifecycleState.DISCONNECTING,
        HostLifecycleState.FAILED,
    }),
    HostLifecycleState.DEGRADED: frozenset({
        HostLifecycleState.CONNECTED,
        HostLifecycleState.DISCONNECTING,
        HostLifecycleState.RECOVERING,
        HostLifecycleState.FAILED,
    }),
    HostLifecycleState.DISCONNECTING: frozenset({
        HostLifecycleState.DISCONNECTED,
        HostLifecycleState.FAILED,
    }),
    HostLifecycleState.DISCONNECTED: frozenset({
        HostLifecycleState.CONNECTING,
        HostLifecycleState.RECOVERING,
        HostLifecycleState.CLOSED,
    }),
    HostLifecycleState.RECOVERING: frozenset({
        HostLifecycleState.CONNECTED,
        HostLifecycleState.DEGRADED,
        HostLifecycleState.FAILED,
    }),
    HostLifecycleState.FAILED: frozenset({
        HostLifecycleState.RECOVERING,
        HostLifecycleState.DISCONNECTING,
        HostLifecycleState.CLOSED,
    }),
    HostLifecycleState.CLOSED: frozenset(),
}


class HostLifecycleError(RuntimeError):
    """Base error with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str = "host_error") -> None:
        super().__init__(message)
        self.code = code


class InvalidTransitionError(HostLifecycleError):
    def __init__(self, current: HostLifecycleState, target: HostLifecycleState) -> None:
        super().__init__(
            f"Invalid host lifecycle transition: {current.value} -> {target.value}",
            code="invalid_transition",
        )
        self.current = current
        self.target = target


class SessionNotFoundError(HostLifecycleError):
    def __init__(self, session_id: UUID | str) -> None:
        super().__init__(f"Host session not found: {session_id}", code="session_not_found")


class DuplicateSessionError(HostLifecycleError):
    def __init__(self, host_id: UUID | str) -> None:
        super().__init__(
            f"An active session already exists for host {host_id}",
            code="duplicate_session",
        )


class UnsupportedHostTypeError(HostLifecycleError):
    def __init__(self, host_type: str) -> None:
        super().__init__(
            f"Host type is not supported by a lifecycle adapter: {host_type}",
            code="unsupported_host_type",
        )


class UnsupportedCapabilityError(HostLifecycleError):
    def __init__(self, capability: str) -> None:
        super().__init__(
            f"Capability is not supported by this adapter: {capability}",
            code="unsupported_capability",
        )


@dataclass(frozen=True, slots=True)
class HostSessionConfig:
    root: str
    allowed_roots: tuple[str, ...] = ()
    read_only: bool = True
    max_file_bytes: int = 256 * 1024
    max_directory_entries: int = 500
    timeout_seconds: float = 10.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.root or "").strip():
            raise HostLifecycleError("A host session requires a root location", code="invalid_config")
        if self.max_file_bytes <= 0:
            raise HostLifecycleError("max_file_bytes must be positive", code="invalid_config")
        if self.max_directory_entries <= 0:
            raise HostLifecycleError("max_directory_entries must be positive", code="invalid_config")
        if self.timeout_seconds <= 0:
            raise HostLifecycleError("timeout_seconds must be positive", code="invalid_config")


@dataclass(frozen=True, slots=True)
class HostHealth:
    state: HealthState
    checked_at: datetime
    issues: tuple[str, ...] = ()
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "checked_at": self.checked_at.isoformat(),
            "issues": list(self.issues),
            "details": dict(self.details),
        }


@dataclass
class HostSession:
    session_id: UUID
    host_id: UUID
    host_type: str
    provider: str
    state: HostLifecycleState
    config: HostSessionConfig
    audit_correlation: UUID
    capabilities: Dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    connected_at: Optional[datetime] = None
    disconnected_at: Optional[datetime] = None
    last_health_at: Optional[datetime] = None
    last_health: HealthState = HealthState.UNKNOWN
    last_error: Optional[str] = None
    adapter: Optional["HostAdapter"] = field(default=None, repr=False)

    def to_dict(self, *, include_config: bool = True) -> dict:
        payload = {
            "session_id": str(self.session_id),
            "host_id": str(self.host_id),
            "host_type": self.host_type,
            "provider": self.provider,
            "state": self.state.value,
            "capabilities": dict(self.capabilities),
            "created_at": self.created_at.isoformat(),
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "disconnected_at": self.disconnected_at.isoformat() if self.disconnected_at else None,
            "last_health_at": self.last_health_at.isoformat() if self.last_health_at else None,
            "last_health": self.last_health.value,
            "last_error": self.last_error,
            "audit_correlation": str(self.audit_correlation),
        }
        if include_config:
            payload["config"] = {
                "root": self.config.root,
                "read_only": self.config.read_only,
                "allowed_roots": list(self.config.allowed_roots),
                "max_file_bytes": self.config.max_file_bytes,
                "max_directory_entries": self.config.max_directory_entries,
                "timeout_seconds": self.config.timeout_seconds,
            }
        return payload


class HostAdapter(ABC):
    """Adapter contract for one connected host session."""

    host_type: str = "unknown"

    def __init__(self, config: HostSessionConfig) -> None:
        self.config = config

    @abstractmethod
    def validate(self) -> None:
        """Validate configuration and reachability without opening a session."""

    @abstractmethod
    def connect(self) -> None:
        """Open the session; raise on any failure."""

    @abstractmethod
    def disconnect(self) -> None:
        """Release resources; must be safe to call more than once."""

    @abstractmethod
    def health(self) -> HostHealth:
        """Return an adapter-specific health observation."""

    @abstractmethod
    def capabilities(self) -> Dict[str, str]:
        """Return effective capabilities for this connected adapter."""

    def read_file(self, path: str, max_bytes: Optional[int] = None) -> dict:
        raise UnsupportedCapabilityError("file_read")

    def list_directory(self, path: str = "") -> dict:
        raise UnsupportedCapabilityError("repository_structure_read")

    def metadata(self) -> dict:
        raise UnsupportedCapabilityError("repository_metadata_read")

    def describe(self) -> dict:
        return {
            "host_type": self.host_type,
            "root": self.config.root,
            "capabilities": self.capabilities(),
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redact_error(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return message[:700]


class HostSessionManager:
    """Owns host sessions and enforces lifecycle transitions."""

    def __init__(
        self,
        registry: Optional[Any] = None,
        adapter_factory: Optional[Callable[[str, HostSessionConfig], HostAdapter]] = None,
        audit_sink: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._registry = registry or get_host_registry()
        self._factory = adapter_factory or self._default_factory
        self._audit_sink = audit_sink
        self._sessions: Dict[UUID, HostSession] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ factory
    @staticmethod
    def _default_factory(host_type: str, config: HostSessionConfig) -> HostAdapter:
        from .adapters.runtime_factory import create_host_adapter
        return create_host_adapter(host_type, config)

    # ------------------------------------------------------------------- audit
    def _audit(self, action: str, session: HostSession, **extra: Any) -> None:
        if self._audit_sink is None:
            return
        event = {
            "action": action,
            "host_id": str(session.host_id),
            "session_id": str(session.session_id),
            "host_type": session.host_type,
            "state": session.state.value,
            "correlation_id": str(session.audit_correlation),
            **extra,
        }
        self._audit_sink(event)

    # ----------------------------------------------------------------- helpers
    def _get(self, session_id: UUID | str) -> HostSession:
        try:
            sid = session_id if isinstance(session_id, UUID) else UUID(str(session_id))
        except (ValueError, AttributeError) as exc:
            raise SessionNotFoundError(session_id) from exc
        session = self._sessions.get(sid)
        if session is None:
            raise SessionNotFoundError(sid)
        return session

    def _transition(self, session: HostSession, target: HostLifecycleState) -> None:
        if session.state is target:
            return
        if target not in ALLOWED_TRANSITIONS[session.state]:
            raise InvalidTransitionError(session.state, target)
        session.state = target

    def _fail(self, session: HostSession, exc: BaseException) -> None:
        session.last_error = _redact_error(exc)
        session.state = HostLifecycleState.FAILED
        session.capabilities = self._unavailable_capabilities(session)
        self._audit("host.session.failed", session, error=session.last_error)

    def _adapter_capabilities(self, session: HostSession) -> Dict[str, str]:
        if session.adapter is None:
            return {}
        return dict(session.adapter.capabilities())

    def _unavailable_capabilities(self, session: HostSession) -> Dict[str, str]:
        declared = CAPABILITY_MATRIX.get_all_capabilities(session.host_type)
        if session.adapter is not None:
            declared.update(self._adapter_capabilities(session))
        return {capability: "unavailable" for capability in declared}

    # --------------------------------------------------------------- operations
    def create_session(
        self,
        host_id: UUID | str,
        *,
        root: Optional[str] = None,
        allowed_roots: Optional[List[str]] = None,
        read_only: bool = True,
        max_file_bytes: int = 256 * 1024,
        max_directory_entries: int = 500,
        timeout_seconds: float = 10.0,
    ) -> HostSession:
        with self._lock:
            try:
                hid = host_id if isinstance(host_id, UUID) else UUID(str(host_id))
            except (ValueError, AttributeError) as exc:
                raise HostLifecycleError(f"Invalid host id: {host_id}", code="invalid_host_id") from exc

            host = self._registry.get_host(hid)
            host_type = str(host.get("host_type", ""))
            for existing in self._sessions.values():
                if existing.host_id == hid and existing.state in ACTIVE_STATES:
                    raise DuplicateSessionError(hid)

            root_value = root if root is not None else host.get("normalized_identifier")
            config = HostSessionConfig(
                root=str(root_value or ""),
                allowed_roots=tuple(allowed_roots or ()),
                read_only=read_only,
                max_file_bytes=max_file_bytes,
                max_directory_entries=max_directory_entries,
                timeout_seconds=timeout_seconds,
            )
            adapter = self._factory(host_type, config)
            session = HostSession(
                session_id=uuid4(),
                host_id=hid,
                host_type=host_type,
                provider=str(host.get("provider", "")),
                state=HostLifecycleState.REGISTERED,
                config=config,
                audit_correlation=uuid4(),
                adapter=adapter,
            )
            self._transition(session, HostLifecycleState.CONFIGURED)
            self._sessions[session.session_id] = session
            self._audit("host.session.created", session)
            try:
                self._registry.update_host_status(hid, HostLifecycleState.REGISTERED.value)
            except Exception:
                self._sessions.pop(session.session_id, None)
                raise
            return session

    def connect(self, session_id: UUID | str) -> HostSession:
        with self._lock:
            session = self._get(session_id)
            if session.state is HostLifecycleState.CONNECTED:
                return session
            if session.state not in {
                HostLifecycleState.CONFIGURED,
                HostLifecycleState.DISCONNECTED,
            }:
                raise InvalidTransitionError(session.state, HostLifecycleState.CONNECTING)
            adapter = session.adapter
            if adapter is None:
                raise HostLifecycleError("Session has no adapter", code="missing_adapter")
            try:
                self._transition(session, HostLifecycleState.VALIDATING)
                adapter.validate()
                self._transition(session, HostLifecycleState.CONNECTING)
                adapter.connect()
                session.connected_at = _now()
                session.disconnected_at = None
                session.capabilities = self._adapter_capabilities(session)
                self._transition(session, HostLifecycleState.CONNECTED)
                session.last_error = None
                health = self._safe_health(adapter)
                self._record_health(session, health)
                if health.state is not HealthState.HEALTHY:
                    self._transition(session, HostLifecycleState.DEGRADED)
                self._audit("host.session.connected", session)
                return session
            except Exception as exc:
                self._fail(session, exc)
                raise HostLifecycleError(
                    f"Host connection failed: {_redact_error(exc)}",
                    code="connect_failed",
                ) from exc

    def disconnect(self, session_id: UUID | str) -> HostSession:
        with self._lock:
            session = self._get(session_id)
            if session.state is HostLifecycleState.DISCONNECTED:
                return session
            if session.state is HostLifecycleState.CLOSED:
                return session
            if session.state not in {
                HostLifecycleState.CONNECTED,
                HostLifecycleState.DEGRADED,
                HostLifecycleState.FAILED,
            }:
                raise InvalidTransitionError(session.state, HostLifecycleState.DISCONNECTING)

            self._transition(session, HostLifecycleState.DISCONNECTING)
            try:
                if session.adapter is not None:
                    session.adapter.disconnect()
            except Exception as exc:
                self._fail(session, exc)
                raise HostLifecycleError(
                    f"Host disconnect failed: {_redact_error(exc)}",
                    code="disconnect_failed",
                ) from exc
            self._transition(session, HostLifecycleState.DISCONNECTED)
            session.disconnected_at = _now()
            session.capabilities = self._unavailable_capabilities(session)
            self._audit("host.session.disconnected", session)
            try:
                self._registry.update_host_status(
                    session.host_id, HostLifecycleState.DISCONNECTED.value
                )
            except Exception as exc:
                session.last_error = _redact_error(exc)
                raise HostLifecycleError(
                    f"Host disconnected but status persistence failed: {_redact_error(exc)}",
                    code="persistence_failed",
                ) from exc
            return session

    def health(self, session_id: UUID | str) -> dict:
        with self._lock:
            session = self._get(session_id)
            if session.state not in {
                HostLifecycleState.CONNECTED,
                HostLifecycleState.DEGRADED,
            }:
                raise InvalidTransitionError(session.state, HostLifecycleState.DEGRADED)
            adapter = session.adapter
            if adapter is None:
                raise HostLifecycleError("Session has no adapter", code="missing_adapter")
            health = self._safe_health(adapter)
            self._record_health(session, health)
            if health.state is HealthState.HEALTHY:
                if session.state is HostLifecycleState.DEGRADED:
                    self._transition(session, HostLifecycleState.CONNECTED)
                session.last_error = None
            else:
                if health.issues:
                    session.last_error = "; ".join(health.issues)[:700]
                if session.state is HostLifecycleState.CONNECTED:
                    self._transition(session, HostLifecycleState.DEGRADED)
            self._audit("host.health.checked", session, health=health.state.value)
            return {"session": session.to_dict(), "health": health.to_dict()}

    def _safe_health(self, adapter: HostAdapter) -> HostHealth:
        try:
            return adapter.health()
        except Exception as exc:
            return HostHealth(
                state=HealthState.UNREACHABLE,
                checked_at=_now(),
                issues=(f"health check raised: {_redact_error(exc)}",),
            )

    @staticmethod
    def _record_health(session: HostSession, health: HostHealth) -> None:
        session.last_health = health.state
        session.last_health_at = health.checked_at

    def recover(
        self,
        session_id: UUID | str,
        *,
        attempts: int = 2,
        backoff_seconds: float = 0.0,
    ) -> HostSession:
        with self._lock:
            session = self._get(session_id)
            if session.state is HostLifecycleState.CONNECTED and session.last_health is HealthState.HEALTHY:
                return session
            if session.state not in {
                HostLifecycleState.FAILED,
                HostLifecycleState.DEGRADED,
                HostLifecycleState.DISCONNECTED,
            }:
                raise InvalidTransitionError(session.state, HostLifecycleState.RECOVERING)
            adapter = session.adapter
            if adapter is None:
                raise HostLifecycleError("Session has no adapter", code="missing_adapter")

            attempts = max(1, min(int(attempts), 5))
            self._transition(session, HostLifecycleState.RECOVERING)
            last_exc: Optional[BaseException] = None
            for index in range(attempts):
                try:
                    try:
                        adapter.disconnect()
                    except Exception:
                        # Cleanup before reconnect is best effort; the recovery
                        # result below still determines success or failure.
                        pass
                    adapter.validate()
                    adapter.connect()
                    session.connected_at = _now()
                    session.disconnected_at = None
                    session.capabilities = self._adapter_capabilities(session)
                    health = self._safe_health(adapter)
                    self._record_health(session, health)
                    session.last_error = None
                    if health.state is HealthState.UNREACHABLE:
                        raise HostLifecycleError(
                            "Recovered connection reported unreachable health",
                            code="recovery_unhealthy",
                        )
                    self._transition(
                        session,
                        HostLifecycleState.CONNECTED
                        if health.state is HealthState.HEALTHY
                        else HostLifecycleState.DEGRADED,
                    )
                    self._audit("host.session.recovered", session, attempts=index + 1)
                    return session
                except Exception as exc:
                    last_exc = exc
                    if backoff_seconds > 0 and index + 1 < attempts:
                        time.sleep(backoff_seconds * (2 ** index))
            self._fail(session, last_exc or RuntimeError("recovery failed"))
            raise HostLifecycleError(
                f"Host recovery failed after {attempts} attempt(s): {session.last_error}",
                code="recovery_failed",
            ) from last_exc

    def shutdown(self) -> dict:
        with self._lock:
            closed: List[str] = []
            errors: List[dict] = []
            for session in list(self._sessions.values()):
                if session.state is HostLifecycleState.CLOSED:
                    continue
                try:
                    if session.state in {
                        HostLifecycleState.CONNECTED,
                        HostLifecycleState.DEGRADED,
                        HostLifecycleState.FAILED,
                    }:
                        self.disconnect(session.session_id)
                    if session.state is not HostLifecycleState.CLOSED:
                        self._transition(session, HostLifecycleState.CLOSED)
                    session.capabilities = self._unavailable_capabilities(session)
                    self._audit("host.session.closed", session)
                    closed.append(str(session.session_id))
                except Exception as exc:
                    errors.append({
                        "session_id": str(session.session_id),
                        "error": _redact_error(exc),
                    })
            return {"closed": closed, "errors": errors}

    def dispose(self, session_id: UUID | str) -> None:
        with self._lock:
            session = self._get(session_id)
            if session.state not in TERMINAL_STATES:
                raise InvalidTransitionError(session.state, HostLifecycleState.CLOSED)
            self._sessions.pop(session.session_id, None)

    def get_session(self, session_id: UUID | str) -> HostSession:
        with self._lock:
            return self._get(session_id)

    def list_sessions(self, host_id: Optional[UUID | str] = None) -> List[HostSession]:
        with self._lock:
            sessions = list(self._sessions.values())
            if host_id is not None:
                try:
                    hid = host_id if isinstance(host_id, UUID) else UUID(str(host_id))
                except (ValueError, AttributeError):
                    return []
                sessions = [session for session in sessions if session.host_id == hid]
            return sorted(sessions, key=lambda session: session.created_at)

    def effective_capabilities(self, session_id: UUID | str) -> dict:
        with self._lock:
            session = self._get(session_id)
            if session.state not in {
                HostLifecycleState.CONNECTED,
                HostLifecycleState.DEGRADED,
            }:
                return {
                    "state": session.state.value,
                    "capabilities": self._unavailable_capabilities(session),
                }
            return {
                "state": session.state.value,
                "capabilities": self._adapter_capabilities(session),
            }


_session_manager: Optional[HostSessionManager] = None


def get_session_manager() -> HostSessionManager:
    """Return the process-wide session manager (sessions are process-local)."""
    global _session_manager
    if _session_manager is None:
        from ..platform.operations import append_audit
        _session_manager = HostSessionManager(audit_sink=append_audit)
    return _session_manager


def reset_session_manager() -> None:
    """Drop the cached manager; intended for tests and process teardown."""
    global _session_manager
    _session_manager = None
