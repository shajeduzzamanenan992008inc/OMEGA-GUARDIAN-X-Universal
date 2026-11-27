"""Remote SSH host adapter (HOST-08).

Drives the locally installed OpenSSH client. Conservative by design:
host keys verified (StrictHostKeyChecking=yes), key-based auth only
(BatchMode=yes), bounded commands, traversal guards, redacted errors.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from ..lifecycle import (
    HealthState,
    HostAdapter,
    HostHealth,
    HostLifecycleError,
)


class SshHostError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "ssh_error") -> None:
        super().__init__(message, code=code)


class SshAuthenticationError(SshHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="ssh_auth_failed")


class SshHostKeyError(SshHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="ssh_host_key")


class SshCommandError(SshHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="ssh_command_failed")


class SshTimeoutError(SshHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="ssh_timeout")


def _redact(text: str) -> str:
    return (text or "").strip()[:700]


class SshHostAdapter(HostAdapter):
    host_type = "remote_ssh"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._connected = False
        self._host: str = ""
        self._user: Optional[str] = None
        self._port: int = 22
        self._remote_root: str = ""
        self._identity_file: Optional[str] = None
        self._known_hosts: Optional[str] = None

    def _parse_config(self) -> None:
        metadata = dict(self.config.metadata or {})
        if metadata.get("strict_host_key_checking") is False:
            raise SshHostError(
                "Disabling SSH host-key verification is not permitted",
                code="host_key_policy",
            )
        root = str(self.config.root or "").strip()
        host = metadata.get("ssh_host")
        user = metadata.get("ssh_user")
        port = metadata.get("ssh_port", 22)
        remote_path = metadata.get("ssh_remote_path")

        if "@" in root and ":" in root.split("@", 1)[-1]:
            userpart, rest = root.split("@", 1)
            if not user:
                user = userpart
            hostpart, pathpart = rest.split(":", 1)
            if not host:
                host = hostpart
            if remote_path is None:
                remote_path = pathpart
        elif ":" in root and not root.startswith("/"):
            hostpart, pathpart = root.split(":", 1)
            if not host:
                host = hostpart
            if remote_path is None:
                remote_path = pathpart
        else:
            if remote_path is None:
                remote_path = root or "."

        if not host:
            raise SshHostError("SSH host is required", code="invalid_config")
        try:
            self._port = int(port)
        except (TypeError, ValueError) as exc:
            raise SshHostError(f"Invalid SSH port: {port}", code="invalid_config") from exc
        if self._port <= 0 or self._port > 65535:
            raise SshHostError("SSH port out of range", code="invalid_config")

        self._host = str(host)
        self._user = str(user) if user else None
        self._remote_root = str(remote_path or ".").strip() or "."
        self._identity_file = metadata.get("identity_file")
        self._known_hosts = metadata.get("known_hosts")

        if self._identity_file:
            p = Path(self._identity_file).expanduser()
            if not p.is_file():
                raise SshHostError(
                    f"SSH identity file not found: {self._identity_file}",
                    code="identity_missing",
                )
            self._identity_file = str(p)

    def _ssh_binary(self) -> str:
        binary = shutil.which("ssh")
        if not binary:
            raise SshHostError("ssh client is not installed", code="ssh_unavailable")
        return binary

    def _target(self) -> str:
        if self._user:
            return f"{self._user}@{self._host}"
        return self._host

    def _base_args(self) -> List[str]:
        args = [
            self._ssh_binary(),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=" + str(int(self.config.timeout_seconds)),
            "-p", str(self._port),
        ]
        if self._known_hosts:
            args += ["-o", "UserKnownHostsFile=" + str(self._known_hosts)]
        if self._identity_file:
            args += ["-i", self._identity_file, "-o", "IdentitiesOnly=yes"]
        args.append(self._target())
        return args

    def _run(self, remote_command: str, *, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        effective_timeout = timeout if timeout is not None else self.config.timeout_seconds
        cmd = self._base_args() + [remote_command]
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SshTimeoutError(
                f"SSH command timed out after {effective_timeout}s"
            ) from exc
        except FileNotFoundError as exc:
            raise SshHostError("ssh client is not installed", code="ssh_unavailable") from exc
        except OSError as exc:
            raise SshHostError(f"SSH invocation failed: {_redact(str(exc))}", code="ssh_io") from exc
        stderr = (completed.stderr or b"").decode("utf-8", "replace")
        lowered = stderr.lower()
        if "host key verification failed" in lowered or "remote host identification has changed" in lowered:
            raise SshHostKeyError(_redact(stderr or "SSH host key verification failed"))
        if completed.returncode == 255 and ("permission denied" in lowered or "authentication" in lowered):
            raise SshAuthenticationError(_redact(stderr or "SSH authentication failed"))
        return completed

    # --------------------------------------------------------------- lifecycle
    def validate(self) -> None:
        self._parse_config()
        self._ssh_binary()
        result = self._run("true")
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", "replace")
            lowered = stderr.lower()
            if "host key" in lowered:
                raise SshHostKeyError(_redact(stderr))
            if "permission denied" in lowered or "authentication" in lowered:
                raise SshAuthenticationError(_redact(stderr))
            raise SshCommandError(_redact(stderr or f"SSH validation failed (exit {result.returncode})"))

    def connect(self) -> None:
        self.validate()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> HostHealth:
        if not self._connected:
            return HostHealth(
                state=HealthState.UNKNOWN,
                checked_at=datetime.now(timezone.utc),
                issues=("not connected",),
            )
        try:
            result = self._run("true", timeout=min(self.config.timeout_seconds, 5.0))
        except SshHostError as exc:
            return HostHealth(
                state=HealthState.UNREACHABLE,
                checked_at=datetime.now(timezone.utc),
                issues=(str(exc),),
            )
        if result.returncode == 0:
            return HostHealth(state=HealthState.HEALTHY, checked_at=datetime.now(timezone.utc))
        stderr = (result.stderr or b"").decode("utf-8", "replace")
        return HostHealth(
            state=HealthState.DEGRADED,
            checked_at=datetime.now(timezone.utc),
            issues=(_redact(stderr or f"exit {result.returncode}"),),
        )

    def capabilities(self) -> Dict[str, str]:
        return {
            "repository_metadata_read": "supported",
            "repository_structure_read": "supported",
            "file_read": "supported",
            "file_system_access": "supported",
            "workspace_read": "supported",
            "workspace_exec": "requires_auth",
            "workspace_execute": "requires_auth",
            "testing": "supported",
            "file_write": "requires_auth",
            "repository_write": "requires_auth",
        }

    # ----------------------------------------------------------- remote paths
    def _remote_path(self, relative: str) -> str:
        raw = "" if relative is None else str(relative)
        if "\x00" in raw:
            raise SshHostError("Path contains a NUL byte", code="path_rejected")
        candidate = raw.replace("\\", "/")
        parts = [p for p in candidate.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise SshHostError("Path traversal is not accepted", code="path_traversal")
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            raise SshHostError("Absolute paths are not accepted", code="path_rejected")
        joined = "/".join([self._remote_root.rstrip("/")] + parts) if parts else self._remote_root
        return joined if joined else "."

    # ----------------------------------------------------------- file ops
    def read_file(self, path: str, max_bytes: Optional[int] = None) -> dict:
        if not self._connected:
            raise SshHostError("Adapter is not connected", code="not_connected")
        limit = max_bytes if max_bytes is not None else self.config.max_file_bytes
        if limit <= 0 or limit > self.config.max_file_bytes:
            limit = self.config.max_file_bytes
        remote = self._remote_path(path)
        command = "head -c {} -- {}".format(int(limit) + 1, shlex.quote(remote))
        result = self._run(command)
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", "replace")
            raise SshCommandError(_redact(stderr or f"remote read failed (exit {result.returncode})"))
        data = result.stdout or b""
        truncated = len(data) > limit
        if truncated:
            data = data[:limit]
        text = data.decode("utf-8", "replace")
        digest = hashlib.sha256(data).hexdigest()
        return {
            "path": path,
            "remote_path": remote,
            "text": text,
            "size": len(data),
            "truncated": truncated,
            "sha256": digest,
            "read_only": True,
        }

    def list_directory(self, path: str = "") -> dict:
        if not self._connected:
            raise SshHostError("Adapter is not connected", code="not_connected")
        remote = self._remote_path(path)
        command = "ls -la -- {}".format(shlex.quote(remote))
        result = self._run(command)
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", "replace")
            raise SshCommandError(_redact(stderr or f"remote listing failed (exit {result.returncode})"))
        lines = (result.stdout or b"").decode("utf-8", "replace").splitlines()
        entries: List[dict] = []
        for line in lines:
            line = line.rstrip("\r")
            if not line or line.startswith("total "):
                continue
            parts = line.split()
            if len(parts) < 9:
                continue
            perms = parts[0]
            size_str = parts[4]
            name = " ".join(parts[8:])
            if name in (".", ".."):
                continue
            try:
                size = int(size_str)
            except ValueError:
                size = 0
            entry_type = "directory" if perms.startswith("d") else ("symlink" if perms.startswith("l") else "file")
            entries.append({"name": name, "type": entry_type, "size": size, "permissions": perms})
            if len(entries) >= self.config.max_directory_entries:
                break
        truncated = len(entries) >= self.config.max_directory_entries
        entries.sort(key=lambda e: e["name"])
        return {
            "path": path,
            "remote_path": remote,
            "entries": entries,
            "truncated": truncated,
        }

    def metadata(self) -> dict:
        return {
            "host_type": self.host_type,
            "host": self._host,
            "user": self._user,
            "port": self._port,
            "remote_root": self._remote_root,
            "read_only": True,
            "strict_host_key_checking": True,
            "identity_file": self._identity_file,
            "known_hosts": self._known_hosts,
        }

    def describe(self) -> dict:
        return {
            "host_type": self.host_type,
            "root": self.config.root,
            "capabilities": self.capabilities(),
            "metadata": self.metadata(),
        }
