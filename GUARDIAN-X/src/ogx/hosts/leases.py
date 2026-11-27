"""Durable cross-process host session leases with fencing tokens (HOST-13).

A host session is owned by exactly one process at a time. The ownership record
lives in a small SQLite database under ``OGX_HOME`` so independent OS processes
(CLI invocations, workers) can coordinate without a daemon:

* ``acquire`` takes the lease for a host, or fails with ``LeaseHeldError`` while
  another live owner holds an unexpired lease.
* every acquisition increments a monotonically increasing **fencing token** for
  that host. Tokens are never reused, so a holder that lost its lease (expired,
  stolen, or machine restarted) can be rejected even if it still believes it is
  the owner.
* ``renew`` extends an unexpired lease only for the exact holder and token.
* expiry is the recovery path: a crashed process stops renewing, the lease
  expires, and the next process steals it with a higher fencing token.

Limitations, stated explicitly rather than implied:

* The clock is the local system clock; a large clock jump forward can expire a
  live lease early and a jump backward can extend one. Callers must treat a
  lease as an optimisation for availability, not as a security boundary.
* SQLite serialises writers with ``BEGIN IMMEDIATE`` and a busy timeout. On a
  network filesystem without working POSIX/Windows locks, coordination is not
  guaranteed; the store refuses non-local-looking paths only when it cannot
  create the database.
* Nothing here is a distributed consensus protocol: two machines sharing one
  state directory rely entirely on the filesystem's locking. ``docs/
  HOST_CROSS_PROCESS_LEASES.md`` records this limitation for operators.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

LEASE_SCHEMA_VERSION = 1
DEFAULT_LEASE_TTL_SECONDS = 120.0
MAX_LEASE_TTL_SECONDS = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS host_session_leases (
    host_id       TEXT PRIMARY KEY,
    lease_id      TEXT NOT NULL,
    owner         TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    acquired_at   REAL NOT NULL,
    expires_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS host_fencing_tokens (
    host_id       TEXT PRIMARY KEY,
    fencing_token INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lease_schema (
    version INTEGER NOT NULL
);
"""


class LeaseError(RuntimeError):
    """Base class for lease coordination failures."""

    code = "lease_error"

    def __init__(self, message: str, *, code: Optional[str] = None, **details: object) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.details = details


class LeaseHeldError(LeaseError):
    """Another live owner currently holds the host lease."""

    code = "lease_held"

    def __init__(self, host_id: str, holder: "HostLease") -> None:
        super().__init__(
            f"Host {host_id} is leased by {holder.owner} until "
            f"{holder.expires_at:.0f} (fencing token {holder.fencing_token})",
            code="lease_held",
            host_id=host_id,
            holder=holder.to_dict(),
        )
        self.holder = holder


class LeaseLostError(LeaseError):
    """The caller no longer owns the lease it is trying to use."""

    code = "lease_lost"


class LeaseNotFoundError(LeaseError):
    """No lease exists for the host."""

    code = "lease_not_found"


def default_owner() -> str:
    """Stable owner identity for this process: ``hostname:pid``."""

    try:
        hostname = socket.gethostname() or "unknown-host"
    except OSError:  # pragma: no cover - defensive on odd platforms
        hostname = "unknown-host"
    return f"{hostname}:{os.getpid()}"


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness probe for a local process.

    Windows must not use ``os.kill(pid, 0)``: on Windows that calls
    ``TerminateProcess`` and would kill the process being inspected. A
    ``OpenProcess`` handle check is the safe equivalent.
    """

    if pid <= 0:
        return True
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI only
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            # A terminated process can still own open handles, so the exit code
            # is the reliable signal, not the handle itself.
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # pragma: no cover - platform specific errno
        return True
    return True


def owner_is_alive(owner: str) -> bool:
    """True unless the owner is a dead process on *this* machine.

    A remote owner (different hostname) or an unparseable owner string is
    treated as alive: liveness for another machine cannot be judged from here,
    so its lease is only recoverable by TTL expiry.
    """

    hostname, separator, raw_pid = str(owner).rpartition(":")
    if not separator:
        return True
    try:
        local_hostname = socket.gethostname()
    except OSError:  # pragma: no cover
        return True
    if hostname != local_hostname:
        return True
    try:
        pid = int(raw_pid)
    except ValueError:
        return True
    return _pid_alive(pid)


@dataclass(frozen=True)
class HostLease:
    host_id: str
    lease_id: str
    owner: str
    fencing_token: int
    acquired_at: float
    expires_at: float

    def to_dict(self) -> dict:
        return {
            "host_id": self.host_id,
            "lease_id": self.lease_id,
            "owner": self.owner,
            "fencing_token": self.fencing_token,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }

    def expired(self, now: float) -> bool:
        return self.expires_at <= now

    def recoverable(self, now: float) -> bool:
        """Expired, or held by a local process that no longer exists."""
        return self.expired(now) or not owner_is_alive(self.owner)


def _clamp_ttl(ttl_seconds: float) -> float:
    try:
        ttl = float(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise LeaseError("Lease TTL must be numeric", code="invalid_ttl") from exc
    if ttl != ttl or ttl <= 0:  # NaN or non-positive
        raise LeaseError("Lease TTL must be positive", code="invalid_ttl")
    return min(ttl, MAX_LEASE_TTL_SECONDS)


class HostLeaseStore:
    """SQLite-backed lease store shared by every process using one state dir."""

    def __init__(self, path: Path | str, *, clock: Callable[[], float] = time.time) -> None:
        candidate = Path(path)
        if candidate.exists() and candidate.is_symlink():
            raise LeaseError(
                "Lease database must not be a symlink", code="unsafe_lease_path"
            )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        self._path = candidate
        self._clock = clock
        self._initialize()

    @classmethod
    def default(cls, state_directory: Path | str, *, clock: Callable[[], float] = time.time) -> "HostLeaseStore":
        return cls(Path(state_directory) / "hosts" / "leases.db", clock=clock)

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------- internals
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)
            row = connection.execute("SELECT version FROM lease_schema").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO lease_schema (version) VALUES (?)", (LEASE_SCHEMA_VERSION,)
                )
            elif int(row["version"]) != LEASE_SCHEMA_VERSION:
                raise LeaseError(
                    f"Unsupported lease schema version {row['version']}",
                    code="lease_schema_mismatch",
                )

    @staticmethod
    def _row_to_lease(row: sqlite3.Row) -> HostLease:
        return HostLease(
            host_id=str(row["host_id"]),
            lease_id=str(row["lease_id"]),
            owner=str(row["owner"]),
            fencing_token=int(row["fencing_token"]),
            acquired_at=float(row["acquired_at"]),
            expires_at=float(row["expires_at"]),
        )

    @staticmethod
    def _normalize_host_id(host_id: object) -> str:
        value = str(host_id or "").strip()
        if not value:
            raise LeaseError("A lease requires a host id", code="invalid_host_id")
        if len(value) > 128 or any(character in value for character in "\r\n\x00"):
            raise LeaseError("Invalid host id for a lease", code="invalid_host_id")
        return value

    @staticmethod
    def _next_fencing_token(connection: sqlite3.Connection, host_id: str) -> int:
        row = connection.execute(
            "SELECT fencing_token FROM host_fencing_tokens WHERE host_id = ?", (host_id,)
        ).fetchone()
        token = int(row["fencing_token"]) + 1 if row else 1
        connection.execute(
            "INSERT INTO host_fencing_tokens (host_id, fencing_token) VALUES (?, ?) "
            "ON CONFLICT(host_id) DO UPDATE SET fencing_token = excluded.fencing_token",
            (host_id, token),
        )
        return token

    # ------------------------------------------------------------------- API
    def acquire(
        self,
        host_id: object,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        owner: Optional[str] = None,
    ) -> HostLease:
        """Acquire or steal an expired lease, returning the new lease."""

        normalized = self._normalize_host_id(host_id)
        ttl = _clamp_ttl(ttl_seconds)
        owner_id = (owner or default_owner()).strip()
        if not owner_id:
            raise LeaseError("A lease requires an owner", code="invalid_owner")
        now = self._clock()

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM host_session_leases WHERE host_id = ?", (normalized,)
            ).fetchone()
            if row is not None:
                existing = self._row_to_lease(row)
                if not existing.recoverable(now):
                    connection.execute("ROLLBACK")
                    raise LeaseHeldError(normalized, existing)
            token = self._next_fencing_token(connection, normalized)
            lease = HostLease(
                host_id=normalized,
                lease_id=str(uuid.uuid4()),
                owner=owner_id,
                fencing_token=token,
                acquired_at=now,
                expires_at=now + ttl,
            )
            connection.execute(
                "INSERT INTO host_session_leases "
                "(host_id, lease_id, owner, fencing_token, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(host_id) DO UPDATE SET "
                "lease_id = excluded.lease_id, owner = excluded.owner, "
                "fencing_token = excluded.fencing_token, "
                "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at",
                (
                    lease.host_id,
                    lease.lease_id,
                    lease.owner,
                    lease.fencing_token,
                    lease.acquired_at,
                    lease.expires_at,
                ),
            )
            connection.execute("COMMIT")
            return lease
        except LeaseHeldError:
            raise
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def renew(
        self,
        host_id: object,
        lease_id: str,
        fencing_token: int,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> HostLease:
        """Extend an unexpired lease owned by ``lease_id``/``fencing_token``."""

        normalized = self._normalize_host_id(host_id)
        ttl = _clamp_ttl(ttl_seconds)
        now = self._clock()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM host_session_leases WHERE host_id = ?", (normalized,)
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise LeaseNotFoundError(f"No lease exists for host {normalized}")
            existing = self._row_to_lease(row)
            if existing.lease_id != str(lease_id) or existing.fencing_token != int(fencing_token):
                connection.execute("ROLLBACK")
                raise LeaseLostError(
                    f"Lease for host {normalized} is held by another owner "
                    f"(token {existing.fencing_token})",
                    host_id=normalized,
                    expected_token=int(fencing_token),
                    current_token=existing.fencing_token,
                )
            if existing.expired(now):
                connection.execute("ROLLBACK")
                raise LeaseLostError(
                    f"Lease for host {normalized} expired at {existing.expires_at:.0f}",
                    host_id=normalized,
                    fencing_token=existing.fencing_token,
                )
            expires_at = now + ttl
            connection.execute(
                "UPDATE host_session_leases SET expires_at = ? WHERE host_id = ?",
                (expires_at, normalized),
            )
            connection.execute("COMMIT")
            return HostLease(
                host_id=existing.host_id,
                lease_id=existing.lease_id,
                owner=existing.owner,
                fencing_token=existing.fencing_token,
                acquired_at=existing.acquired_at,
                expires_at=expires_at,
            )
        except LeaseError:
            raise
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def release(self, host_id: object, lease_id: str, fencing_token: int) -> bool:
        """Release a lease. Returns ``False`` when the caller no longer owns it."""

        normalized = self._normalize_host_id(host_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM host_session_leases "
                "WHERE host_id = ? AND lease_id = ? AND fencing_token = ?",
                (normalized, str(lease_id), int(fencing_token)),
            )
            connection.execute("COMMIT")
            return cursor.rowcount > 0
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def inspect(self, host_id: object) -> Optional[HostLease]:
        """Return the current lease, or ``None`` when the host is unleased."""

        normalized = self._normalize_host_id(host_id)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM host_session_leases WHERE host_id = ?", (normalized,)
            ).fetchone()
        finally:
            connection.close()
        return self._row_to_lease(row) if row is not None else None

    def validate(self, host_id: object, lease_id: str, fencing_token: int) -> bool:
        """True only when the caller still owns an unexpired lease."""

        lease = self.inspect(host_id)
        if lease is None:
            return False
        if lease.lease_id != str(lease_id) or lease.fencing_token != int(fencing_token):
            return False
        return not lease.expired(self._clock())

    def current_fencing_token(self, host_id: object) -> int:
        """Highest fencing token ever issued for the host (0 when never leased)."""

        normalized = self._normalize_host_id(host_id)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT fencing_token FROM host_fencing_tokens WHERE host_id = ?",
                (normalized,),
            ).fetchone()
        finally:
            connection.close()
        return int(row["fencing_token"]) if row is not None else 0

    def sweep_expired(self) -> int:
        """Delete expired leases; returns the number removed."""

        now = self._clock()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM host_session_leases WHERE expires_at <= ?", (now,)
            )
            connection.execute("COMMIT")
            return cursor.rowcount
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()
