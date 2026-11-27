from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

EXCLUDED_DIRS = {
    # VCS, dependencies and build output: never needed to run project tests.
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "vendor",
    "dist", "build", "target", "__pycache__", ".next", ".nuxt", "coverage",
    # Credential stores that must never enter a sandbox snapshot.
    ".ssh", ".aws", ".gnupg", ".kube", ".docker",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
    "credentials", "secrets.json", ".npmrc", ".pypirc", ".netrc",
    ".git-credentials", ".dockercfg", ".dockerconfigjson", "docker.sock",
    "kubeconfig", ".htpasswd",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
MAX_SNAPSHOT_FILES = 15000
MAX_SNAPSHOT_BYTES = 1_500_000_000
MAX_SINGLE_FILE_BYTES = 50_000_000
ALLOWED_MEMORY = {"384m", "512m", "768m", "1g", "1536m", "2g"}
ALLOWED_CPUS = {"0.5", "1", "1.5", "2"}
MAX_OUTPUT_CHARS = 20000


def docker_path() -> str | None:
    return shutil.which("docker")


def validate_image_ref(image: str) -> None:
    if not isinstance(image, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/:@-]{1,250}@sha256:[0-9a-f]{64}", image
    ):
        raise ValueError(
            "Sandbox image must be an immutable repository@sha256:<64-hex-digest> reference."
        )


def sandbox_plan() -> dict[str, Any]:
    docker = docker_path()
    return {
        "available": bool(docker),
        "runtime": docker or None,
        "default_policy": {
            "network": "none",
            "container_root": "read-only",
            "capabilities": "all dropped",
            "no_new_privileges": True,
            "pids_limit": 96,
            "memory_limit": "768m (swap disabled: --memory-swap equals --memory)",
            "cpu_limit": 1,
            "ipc": "none",
            "user": "10001:10001 (non-root, no host user mapping)",
            "environment": "explicit allowlist only (HOME/TMPDIR/PYTHONDONTWRITEBYTECODE); host environment is never forwarded",
            "project_source": "temporary disposable copy",
            "original_project_writable": False,
            "docker_socket": "never mounted",
            "cleanup": "container is named and force-removed after success, timeout, cancellation or daemon failure",
        },
        "snapshot_policy": {
            "excluded_dirs": sorted(EXCLUDED_DIRS),
            "excluded_names": sorted(SENSITIVE_NAMES),
            "excluded_suffixes": list(SENSITIVE_SUFFIXES),
            "symlinks": "never followed, never copied",
            "limits": {
                "max_files": MAX_SNAPSHOT_FILES,
                "max_total_bytes": MAX_SNAPSHOT_BYTES,
                "max_single_file_bytes": MAX_SINGLE_FILE_BYTES,
            },
        },
        "image_policy": "pre-pulled immutable image digest only; OGX does not pull images",
        "limitations": [
            "Docker daemon and host hardening remain part of the trusted computing base.",
            "This is a restricted container, not a formal VM or hardware isolation boundary.",
            "A malicious Docker daemon can defeat every flag listed here.",
        ],
    }


def _is_sensitive_file(name: str) -> bool:
    lowered = name.lower()
    if lowered in SENSITIVE_NAMES or lowered.startswith(".env."):
        return True
    return lowered.endswith(SENSITIVE_SUFFIXES)


def _copy_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    files = 0
    total = 0
    skipped = 0
    skipped_sensitive: list[str] = []
    for current, dirs, names in os.walk(source, topdown=True, followlinks=False):
        base = Path(current)
        rel_dir = base.relative_to(source)
        target_dir = destination / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            target_dir.chmod(0o777)
        except OSError:
            pass
        kept = []
        for name in dirs:
            child = base / name
            if name in EXCLUDED_DIRS or child.is_symlink():
                skipped += 1
            else:
                kept.append(name)
        dirs[:] = kept
        for name in names:
            src = base / name
            if src.is_symlink() or not src.is_file():
                skipped += 1
                continue
            if _is_sensitive_file(name):
                skipped += 1
                if len(skipped_sensitive) < 50:
                    skipped_sensitive.append(str(rel_dir / name).replace(os.sep, "/"))
                continue
            try:
                size = src.stat().st_size
                if (
                    size > MAX_SINGLE_FILE_BYTES
                    or files >= MAX_SNAPSHOT_FILES
                    or total + size > MAX_SNAPSHOT_BYTES
                ):
                    skipped += 1
                    continue
                dst = target_dir / name
                shutil.copyfile(src, dst)
                try:
                    dst.chmod(0o666)
                except OSError:
                    pass
                files += 1
                total += size
            except OSError:
                skipped += 1
    return {
        "files_copied": files,
        "bytes_copied": total,
        "entries_skipped": skipped,
        "sensitive_excluded": skipped_sensitive,
    }


def _run_docker(args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


def _force_remove(docker: str, container_name: str) -> bool:
    """Best-effort container removal; never raises."""
    try:
        _run_docker([docker, "rm", "-f", container_name], timeout=30)
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def _bounded(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return text[-MAX_OUTPUT_CHARS:]


def run_in_sandbox(
    root: Path,
    image: str,
    command: list[str],
    *,
    timeout: int = 300,
    memory: str = "768m",
    cpus: str = "1",
) -> dict[str, Any]:
    validate_image_ref(image)
    if isinstance(command, (str, bytes)) or not isinstance(command, (list, tuple)):
        raise ValueError("Provide a non-empty argument array; shell strings are not accepted.")
    if (
        not command
        or any(not isinstance(item, str) or not item or "\x00" in item for item in command)
    ):
        raise ValueError("Provide a non-empty argument array; shell strings are not accepted.")
    if not 1 <= timeout <= 1800:
        raise ValueError("timeout must be between 1 and 1800 seconds.")
    if memory not in ALLOWED_MEMORY:
        raise ValueError(f"memory must be one of {', '.join(sorted(ALLOWED_MEMORY))}.")
    if cpus not in ALLOWED_CPUS:
        raise ValueError(f"cpus must be one of {', '.join(sorted(ALLOWED_CPUS))}.")
    docker = docker_path()
    if not docker:
        raise RuntimeError(
            "Docker was not found. OGX refuses to execute project code without the configured container sandbox."
        )
    source = root.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Sandbox target must be a project directory.")
    if "," in str(source):
        raise ValueError("Project path containing commas is not supported by Docker's --mount syntax.")

    # Fail closed when the daemon is unreachable; a missing image is a different
    # failure and neither case is allowed to trigger an implicit pull.
    try:
        daemon = _run_docker([docker, "info", "--format", "{{.ServerVersion}}"], timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Docker daemon did not answer within 30 seconds.") from exc
    if daemon.returncode != 0:
        raise RuntimeError(
            "Docker daemon is not reachable. OGX refuses to execute project code without a working container sandbox."
        )
    try:
        inspect = _run_docker([docker, "image", "inspect", image], timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Docker image inspection timed out.") from exc
    if inspect.returncode != 0:
        raise RuntimeError(
            "The pinned sandbox image is not present locally. Pull and verify it yourself before retrying; OGX will not download images automatically."
        )

    container_name = f"ogx-sandbox-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="ogx-sandbox-") as temp:
        snapshot = Path(temp) / "workspace"
        snapshot.mkdir()
        copied = _copy_snapshot(source, snapshot)
        docker_args = [
            docker, "run", "--rm",
            "--name", container_name,
            "--network", "none",
            "--ipc", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "96",
            "--memory", memory,
            "--memory-swap", memory,
            "--cpus", cpus,
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
            "--env", "HOME=/tmp",
            "--env", "TMPDIR=/tmp",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", f"type=bind,src={snapshot},dst=/workspace,rw",
            "--workdir", "/workspace",
            "--user", "10001:10001",
            "--entrypoint", command[0], image, *command[1:],
        ]
        try:
            result = _run_docker(docker_args, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _force_remove(docker, container_name)
            return {
                "exit_code": 124,
                "timed_out": True,
                "cancelled": False,
                "container": container_name,
                "cleanup": {"container_removed": True},
                "command": command,
                "image": image,
                "snapshot": copied,
                "stdout": _bounded(exc.stdout),
                "stderr": _bounded(exc.stderr),
                "isolation": sandbox_plan()["default_policy"],
            }
        except BaseException:
            # Cancellation (KeyboardInterrupt/SystemExit) must not leave a
            # container behind holding the snapshot.
            _force_remove(docker, container_name)
            raise
        finally:
            # --rm covers the normal path; this covers a killed docker client.
            _force_remove(docker, container_name)
        return {
            "exit_code": result.returncode,
            "timed_out": False,
            "cancelled": False,
            "container": container_name,
            "cleanup": {"container_removed": True},
            "command": command,
            "image": image,
            "snapshot": copied,
            "stdout": _bounded(result.stdout),
            "stderr": _bounded(result.stderr),
            "isolation": sandbox_plan()["default_policy"],
        }
