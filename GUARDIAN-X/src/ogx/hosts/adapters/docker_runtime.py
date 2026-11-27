"""Docker host adapter (HOST-09).

Drives the locally installed Docker CLI. Conservative by design: read-only
inspection by default, argument arrays (never shell), bounded commands and
output, redacted errors, fail-closed daemon validation. Mutating operations
(start/stop/rm/build/pull/exec) are intentionally NOT implemented.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..lifecycle import (
    HealthState,
    HostAdapter,
    HostHealth,
    HostLifecycleError,
)

_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{1,255}$")
_MAX_OUTPUT_DEFAULT = 256 * 1024


class DockerHostError(HostLifecycleError):
    def __init__(self, message: str, *, code: str = "docker_error") -> None:
        super().__init__(message, code=code)


class DockerUnavailableError(DockerHostError):
    def __init__(self, message: str = "docker executable was not found on PATH") -> None:
        super().__init__(message, code="docker_unavailable")


class DockerDaemonError(DockerHostError):
    def __init__(self, message: str = "Docker daemon is not reachable") -> None:
        super().__init__(message, code="docker_daemon_unavailable")


class DockerPermissionError(DockerHostError):
    def __init__(self, message: str = "Docker permission denied") -> None:
        super().__init__(message, code="docker_permission_denied")


class DockerCommandError(DockerHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="docker_command_failed")


class DockerResponseError(DockerHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="docker_response_invalid")


class DockerTimeoutError(DockerHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="docker_timeout")


class DockerNotFoundError(DockerHostError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="docker_not_found")


def _redact(text: str) -> str:
    return (text or "").strip()[:700]


class DockerHostAdapter(HostAdapter):
    host_type = "docker"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._connected = False
        self._container: Optional[str] = None
        self._base_path: str = "/"
        self._docker_host: Optional[str] = None
        self._max_output_bytes: int = min(
            getattr(config, "max_file_bytes", _MAX_OUTPUT_DEFAULT) or _MAX_OUTPUT_DEFAULT,
            _MAX_OUTPUT_DEFAULT,
        )
        if self._max_output_bytes <= 0:
            self._max_output_bytes = _MAX_OUTPUT_DEFAULT

    # ------------------------------------------------------------------ config
    def _parse_config(self) -> None:
        metadata = dict(self.config.metadata or {})
        self._docker_host = metadata.get("docker_host") or metadata.get("DOCKER_HOST")
        container = metadata.get("container") or metadata.get("docker_container")
        base_path = metadata.get("base_path") or metadata.get("container_path")
        root = str(self.config.root or "").strip()

        if ":" in root and not root.startswith("/"):
            left, _, right = root.partition(":")
            if self._is_container_id(left):
                container = container or left
                if right:
                    base_path = base_path or right
                root = right or root

        if container is None and self._is_container_id(root):
            container = root
            base_path = base_path or "/"

        self._container = container
        self._base_path = self._normalize_base(base_path or "/")

    @staticmethod
    def _is_container_id(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        v = value.strip()
        return bool(v) and bool(_CONTAINER_RE.fullmatch(v)) and not v.startswith("-")

    @staticmethod
    def _normalize_base(value: str) -> str:
        raw = (value or "").strip() or "/"
        if raw == "/":
            return "/"
        parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise DockerHostError("Base path traversal is not accepted", code="path_traversal")
        return "/" + "/".join(parts)

    def _validate_container(self, value: Any) -> str:
        v = str(value or "").strip()
        if not v or not _CONTAINER_RE.fullmatch(v) or v.startswith("-"):
            raise DockerHostError(f"Invalid container identifier: {value!r}", code="invalid_container")
        return v

    def _validate_image(self, value: Any) -> str:
        v = str(value or "").strip()
        if not v or not _IMAGE_RE.fullmatch(v) or v.startswith("-") or " " in v:
            raise DockerHostError(f"Invalid image reference: {value!r}", code="invalid_image")
        return v

    def _container_required(self) -> str:
        if not self._container:
            raise DockerHostError("No container specified for this Docker host session", code="container_required")
        return self._validate_container(self._container)

    def _normalize_remote_path(self, value: Any) -> str:
        raw = "" if value is None else str(value)
        if "\x00" in raw:
            raise DockerHostError("Path contains a NUL byte", code="path_rejected")
        if raw.strip() == "":
            base_parts = [p for p in self._base_path.split("/") if p not in ("", ".")]
            return "/" + "/".join(base_parts) if base_parts else "/"
        if raw.startswith("/"):
            parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
            if any(p == ".." for p in parts):
                raise DockerHostError("Path traversal is not accepted", code="path_traversal")
            return "/" + "/".join(parts) if parts else "/"
        base_parts = [p for p in self._base_path.split("/") if p not in ("", ".")]
        parts_rel = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts_rel):
            raise DockerHostError("Path traversal is not accepted", code="path_traversal")
        joined = base_parts + parts_rel
        return "/" + "/".join(joined) if joined else "/"

    # --------------------------------------------------------------- binary
    def _docker_binary(self) -> str:
        binary = shutil.which("docker")
        if not binary:
            raise DockerUnavailableError()
        return binary

    def _run(self, args: List[str], *, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
        if not isinstance(args, (list, tuple)) or not args:
            raise DockerHostError("Docker arguments must be a non-empty list", code="invalid_args")
        if any(not isinstance(a, str) or "\x00" in a for a in args):
            raise DockerHostError("Docker arguments must be strings without NUL bytes", code="invalid_args")
        effective = timeout if timeout is not None else self.config.timeout_seconds
        if effective <= 0:
            effective = self.config.timeout_seconds
        cmd = [self._docker_binary()] + [str(a) for a in args]
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=effective,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerTimeoutError(f"Docker command timed out after {effective}s") from exc
        except FileNotFoundError as exc:
            raise DockerUnavailableError() from exc
        except OSError as exc:
            raise DockerHostError(f"Docker invocation failed: {_redact(str(exc))}", code="docker_io") from exc

        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        if len(stdout) > self._max_output_bytes:
            stdout = stdout[: self._max_output_bytes]
        completed.stdout = stdout
        completed.stderr = stderr

        text_err = stderr.decode("utf-8", "replace")
        lowered = text_err.lower()
        if "permission denied" in lowered:
            raise DockerPermissionError(_redact(text_err or "Docker permission denied"))
        if (
            "cannot connect to the docker daemon" in lowered
            or "is the docker daemon running" in lowered
            or "error during connect" in lowered
            or "connection refused" in lowered
        ):
            raise DockerDaemonError(_redact(text_err or "Docker daemon is not reachable"))
        return completed

    # --------------------------------------------------------------- parsing
    def _parse_json_lines(self, data: bytes) -> List[dict]:
        items: List[dict] = []
        text = (data or b"").decode("utf-8", "replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError) as exc:
                raise DockerResponseError(f"Malformed Docker JSON output: {_redact(line)}") from exc
            if isinstance(obj, dict):
                items.append(obj)
            elif isinstance(obj, list):
                items.extend(x for x in obj if isinstance(x, dict))
        return items

    def _parse_json(self, data: bytes) -> Any:
        text = (data or b"").decode("utf-8", "replace").strip()
        if not text:
            raise DockerResponseError("Empty Docker JSON response")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise DockerResponseError(f"Malformed Docker JSON response: {_redact(text)}") from exc

    def _redact_inspect(self, item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        result = {}
        for key, value in item.items():
            if key in ("Env", "EnvFiles") and isinstance(value, list):
                result[key] = [f"<redacted:{len(value)} entries>"]
            elif key.lower() in ("env", "environment") and isinstance(value, (list, dict)):
                if isinstance(value, list):
                    result[key] = [f"<redacted:{len(value)} entries>"]
                else:
                    result[key] = {k: "<redacted>" for k in value}
            elif isinstance(value, dict):
                result[key] = self._redact_inspect(value)
            elif isinstance(value, list):
                result[key] = [self._redact_inspect(v) for v in value]
            else:
                result[key] = value
        return result

    # --------------------------------------------------------------- daemon
    def _docker_info(self) -> dict:
        result = self._run(["info", "--format", "{{json .}}"])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            lowered = text.lower()
            if "permission denied" in lowered:
                raise DockerPermissionError(_redact(text))
            if "cannot connect" in lowered or "is the docker daemon running" in lowered:
                raise DockerDaemonError(_redact(text))
            raise DockerCommandError(_redact(text or f"docker info failed (exit {result.returncode})"))
        data = self._parse_json(result.stdout)
        if not isinstance(data, dict):
            raise DockerResponseError("Docker info did not return an object")
        return data

    def _docker_version(self) -> dict:
        result = self._run(["version", "--format", "{{json .}}"])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            raise DockerCommandError(_redact(text or f"docker version failed (exit {result.returncode})"))
        data = self._parse_json(result.stdout)
        if not isinstance(data, dict):
            raise DockerResponseError("Docker version did not return an object")
        return data

    # --------------------------------------------------------------- lifecycle
    def validate(self) -> None:
        self._parse_config()
        self._docker_binary()
        info = self._docker_info()
        if not isinstance(info, dict) or not info:
            raise DockerResponseError("Docker daemon returned empty info")

    def connect(self) -> None:
        self.validate()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def _require_connected(self) -> None:
        if not self._connected:
            raise DockerHostError("Adapter is not connected", code="not_connected")

    def health(self) -> HostHealth:
        if not self._connected:
            return HostHealth(
                state=HealthState.UNKNOWN,
                checked_at=datetime.now(timezone.utc),
                issues=("session is not connected",),
            )
        try:
            info = self._docker_info()
        except DockerHostError as exc:
            return HostHealth(
                state=HealthState.UNREACHABLE,
                checked_at=datetime.now(timezone.utc),
                issues=(str(exc),),
            )
        details = {}
        server_version = None
        if isinstance(info.get("ServerVersion"), str):
            server_version = info.get("ServerVersion")
        elif isinstance(info.get("Server"), dict):
            server_version = info["Server"].get("Version")
        if server_version:
            details["server_version"] = server_version
        return HostHealth(
            state=HealthState.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            issues=(),
            details=details,
        )

    def capabilities(self) -> Dict[str, str]:
        return {
            "repository_metadata_read": "unsupported",
            "repository_structure_read": "unsupported",
            "branch_read": "unsupported",
            "commit_read": "unsupported",
            "file_read": "supported",
            "file_system_access": "supported",
            "workspace_read": "supported",
            "workspace_create": "unsupported",
            "workspace_delete": "unsupported",
            "workspace_start": "unsupported",
            "workspace_stop": "unsupported",
            "workspace_exec": "requires_auth",
            "workspace_execute": "requires_auth",
            "file_write": "requires_auth",
            "branch_create": "unsupported",
            "branch_delete": "unsupported",
            "commit_create": "unsupported",
            "push": "unsupported",
            "fetch": "unsupported",
            "clone": "unsupported",
            "repository_write": "requires_auth",
            "pull_request_create": "unsupported",
            "pull_request_merge": "unsupported",
            "testing": "unsupported",
            "deployment": "unsupported",
        }

    # ----------------------------------------------------------- metadata
    def metadata(self) -> dict:
        return {
            "host_type": self.host_type,
            "docker_host": self._docker_host,
            "container": self._container,
            "base_path": self._base_path,
            "read_only": True,
            "docker_cli": True,
        }

    def describe(self) -> dict:
        return {
            "host_type": self.host_type,
            "root": self.config.root,
            "capabilities": self.capabilities(),
            "metadata": self.metadata(),
        }

    # ----------------------------------------------------------- inspection
    def server_info(self) -> dict:
        self._require_connected()
        info = self._docker_info()
        info = dict(info)
        if "SecurityOptions" in info:
            info["SecurityOptions"] = ["<redacted>"]
        return info

    def version(self) -> dict:
        self._require_connected()
        return self._docker_version()

    def list_containers(self, *, all_containers: bool = True) -> List[dict]:
        self._require_connected()
        args = ["ps"]
        if all_containers:
            args.append("-a")
        args += ["--format", "{{json .}}"]
        result = self._run(args)
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            raise DockerCommandError(_redact(text or f"docker ps failed (exit {result.returncode})"))
        return self._parse_json_lines(result.stdout)

    def inspect_container(self, container: Any) -> dict:
        self._require_connected()
        cid = self._validate_container(container)
        result = self._run(["inspect", cid])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            lowered = text.lower()
            if "no such" in lowered or "not found" in lowered:
                raise DockerNotFoundError(_redact(text or f"container not found: {cid}"))
            raise DockerCommandError(_redact(text or f"docker inspect failed (exit {result.returncode})"))
        data = self._parse_json(result.stdout)
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise DockerResponseError("docker inspect did not return a container object")
        return self._redact_inspect(data[0])

    def list_images(self) -> List[dict]:
        self._require_connected()
        result = self._run(["images", "--format", "{{json .}}"])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            raise DockerCommandError(_redact(text or f"docker images failed (exit {result.returncode})"))
        return self._parse_json_lines(result.stdout)

    def inspect_image(self, image: Any) -> dict:
        self._require_connected()
        ref = self._validate_image(image)
        result = self._run(["image", "inspect", ref])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            lowered = text.lower()
            if "no such" in lowered or "not found" in lowered:
                raise DockerNotFoundError(_redact(text or f"image not found: {ref}"))
            raise DockerCommandError(_redact(text or f"docker image inspect failed (exit {result.returncode})"))
        data = self._parse_json(result.stdout)
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise DockerResponseError("docker image inspect did not return an image object")
        return self._redact_inspect(data[0])

    # ----------------------------------------------------------- container files
    def _extract_tar_file(self, data: bytes, limit: int) -> tuple[bytes, str]:
        try:
            archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise DockerResponseError(f"Invalid tar stream from docker cp: {exc}") from exc
        members = archive.getmembers()
        regular = [m for m in members if m.isfile()]
        if not regular:
            archive.close()
            raise DockerHostError("docker cp did not return a regular file", code="not_a_file")
        member = regular[0]
        fobj = archive.extractfile(member)
        if fobj is None:
            archive.close()
            raise DockerHostError("docker cp could not extract file", code="read_failed")
        chunk = fobj.read(limit + 1)
        fobj.close()
        archive.close()
        return chunk, member.name

    def read_container_file(self, container: Any, path: Any, max_bytes: Optional[int] = None) -> dict:
        self._require_connected()
        cid = self._validate_container(container)
        remote = self._normalize_remote_path(path)
        limit = self.config.max_file_bytes if max_bytes is None else max(0, int(max_bytes))
        if limit <= 0 or limit > self.config.max_file_bytes:
            limit = self.config.max_file_bytes
        result = self._run(["cp", f"{cid}:{remote}", "-"])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            lowered = text.lower()
            if "no such" in lowered or "not found" in lowered:
                raise DockerNotFoundError(_redact(text or f"file not found: {remote}"))
            raise DockerCommandError(_redact(text or f"docker cp failed (exit {result.returncode})"))
        chunk, member_name = self._extract_tar_file(result.stdout or b"", limit)
        truncated = len(chunk) > limit
        if truncated:
            chunk = chunk[:limit]
        is_binary = b"\x00" in chunk
        text_out = None if is_binary else chunk.decode("utf-8", errors="replace")
        return {
            "container": cid,
            "path": path,
            "remote_path": remote,
            "member": member_name,
            "size": len(chunk),
            "bytes_read": len(chunk),
            "truncated": truncated,
            "binary": is_binary,
            "text": text_out,
            "sha256": hashlib.sha256(chunk).hexdigest(),
            "read_only": True,
        }

    def list_container_directory(self, container: Any, path: Any = "") -> dict:
        self._require_connected()
        cid = self._validate_container(container)
        remote = self._normalize_remote_path(path) if str(path).strip() != "" else self._base_path
        if remote == "":
            remote = "/"
        result = self._run(["cp", f"{cid}:{remote}", "-"])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", "replace")
            lowered = text.lower()
            if "no such" in lowered or "not found" in lowered:
                raise DockerNotFoundError(_redact(text or f"path not found: {remote}"))
            raise DockerCommandError(_redact(text or f"docker cp failed (exit {result.returncode})"))
        try:
            archive = tarfile.open(fileobj=io.BytesIO(result.stdout or b""), mode="r:")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise DockerResponseError(f"Invalid tar stream from docker cp: {exc}") from exc
        members = archive.getmembers()
        archive.close()
        entries: List[dict] = []
        seen = set()
        for m in members:
            name = m.name
            if name.startswith("./"):
                name = name[2:]
            if name in ("", "."):
                continue
            first = name.split("/")[0]
            if first in seen:
                continue
            seen.add(first)
            is_dir = ("/" in name.rstrip("/")) or m.isdir()
            entries.append(
                {"name": first, "type": "directory" if is_dir else ("symlink" if m.issym() else "file"), "size": m.size}
            )
            if len(entries) >= self.config.max_directory_entries:
                break
        deduped = []
        names_seen = set()
        for e in entries:
            if e["name"] in names_seen:
                continue
            names_seen.add(e["name"])
            deduped.append(e)
        truncated = len(deduped) >= self.config.max_directory_entries
        deduped.sort(key=lambda e: e["name"])
        return {
            "container": cid,
            "path": path,
            "remote_path": remote,
            "entries": deduped,
            "truncated": truncated,
        }

    # ----------------------------------------------------------- compat shims
    def _split_container_path(self, raw: str) -> tuple[Optional[str], str]:
        container = self._container
        remainder = raw
        if ":" in raw and not raw.startswith("/"):
            left, _, right = raw.partition(":")
            if self._is_container_id(left):
                container = left
                remainder = right
        return container, remainder

    def read_file(self, path: str, max_bytes: Optional[int] = None) -> dict:
        container, remainder = self._split_container_path("" if path is None else str(path))
        if not container:
            raise DockerHostError("No container specified for file read", code="container_required")
        cid = self._validate_container(container)
        remote = self._normalize_remote_path(remainder) if remainder.strip() != "" else self._base_path
        if remote == "":
            remote = "/"
        res = self.read_container_file(cid, remote, max_bytes)
        # keep original 'path' in result
        res["path"] = path
        return res

    def list_directory(self, path: str = "") -> dict:
        container, remainder = self._split_container_path("" if path is None else str(path))
        if not container:
            raise DockerHostError("No container specified for directory listing", code="container_required")
        cid = self._validate_container(container)
        remote = self._normalize_remote_path(remainder) if remainder.strip() != "" else self._base_path
        if remote == "":
            remote = "/"
        res = self.list_container_directory(cid, remote)
        res["path"] = path
        return res
