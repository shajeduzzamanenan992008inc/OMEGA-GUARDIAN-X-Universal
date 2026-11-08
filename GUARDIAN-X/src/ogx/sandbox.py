from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

EXCLUDED_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "vendor", "dist", "build", "target", "__pycache__", ".next", ".nuxt", "coverage"}
SENSITIVE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "credentials", "secrets.json"}
MAX_SNAPSHOT_FILES = 15000
MAX_SNAPSHOT_BYTES = 1_500_000_000


def docker_path() -> str | None:
    return shutil.which("docker")


def validate_image_ref(image: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{1,250}@sha256:[0-9a-f]{64}", image):
        raise ValueError("Sandbox image must be an immutable repository@sha256:<64-hex-digest> reference.")


def sandbox_plan() -> dict[str, Any]:
    docker = docker_path()
    return {"available": bool(docker), "runtime": docker or None,
            "default_policy": {"network": "none", "container_root": "read-only", "capabilities": "all dropped",
                               "no_new_privileges": True, "pids_limit": 96, "memory_limit": "768m", "cpu_limit": 1,
                               "project_source": "temporary disposable copy", "original_project_writable": False},
            "image_policy": "pre-pulled immutable image digest only; OGX does not pull images",
            "limitations": ["Docker daemon and host hardening remain part of the trusted computing base.",
                            "This is a restricted container, not a formal VM or hardware isolation boundary."]}


def _copy_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    files = 0
    total = 0
    skipped = 0
    for current, dirs, names in os.walk(source, topdown=True, followlinks=False):
        base = Path(current)
        rel_dir = base.relative_to(source)
        target_dir = destination / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        try: target_dir.chmod(0o777)
        except OSError: pass
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
            if src.is_symlink() or not src.is_file() or name.lower() in SENSITIVE_NAMES or name.lower().startswith(".env."):
                skipped += 1
                continue
            try:
                size = src.stat().st_size
                if size > 50_000_000 or files >= MAX_SNAPSHOT_FILES or total + size > MAX_SNAPSHOT_BYTES:
                    skipped += 1
                    continue
                dst = target_dir / name
                shutil.copyfile(src, dst)
                try: dst.chmod(0o666)
                except OSError: pass
                files += 1
                total += size
            except OSError:
                skipped += 1
    return {"files_copied": files, "bytes_copied": total, "entries_skipped": skipped}


def run_in_sandbox(root: Path, image: str, command: list[str], *, timeout: int = 300,
                   memory: str = "768m", cpus: str = "1") -> dict[str, Any]:
    validate_image_ref(image)
    if not command or any(not isinstance(item, str) or "\x00" in item for item in command):
        raise ValueError("Provide a non-empty argument array; shell strings are not accepted.")
    if not 1 <= timeout <= 1800:
        raise ValueError("timeout must be between 1 and 1800 seconds.")
    if memory not in {"384m", "512m", "768m", "1g", "1536m", "2g"}:
        raise ValueError("memory must be one of 384m, 512m, 768m, 1g, 1536m, 2g.")
    if cpus not in {"0.5", "1", "1.5", "2"}:
        raise ValueError("cpus must be one of 0.5, 1, 1.5, 2.")
    docker = docker_path()
    if not docker:
        raise RuntimeError("Docker was not found. OGX refuses to execute project code without the configured container sandbox.")
    source = root.expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Sandbox target must be a project directory.")
    if "," in str(source):
        raise ValueError("Project path containing commas is not supported by Docker's --mount syntax.")
    # Confirm that the exact immutable image is already available; never pull automatically.
    try:
        inspect = subprocess.run([docker, "image", "inspect", image], capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Docker image inspection timed out.") from exc
    if inspect.returncode != 0:
        raise RuntimeError("The pinned sandbox image is not present locally. Pull and verify it yourself before retrying; OGX will not download images automatically.")
    with tempfile.TemporaryDirectory(prefix="ogx-sandbox-") as temp:
        snapshot = Path(temp) / "workspace"
        snapshot.mkdir()
        copied = _copy_snapshot(source, snapshot)
        docker_args = [docker, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges:true", "--pids-limit", "96", "--memory", memory,
                       "--cpus", cpus, "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
                       "--mount", f"type=bind,src={snapshot},dst=/workspace,rw", "--workdir", "/workspace",
                       "--user", "10001:10001", "--entrypoint", command[0], image, *command[1:]]
        try:
            result = subprocess.run(docker_args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, check=False, shell=False)
            return {"exit_code": result.returncode, "timed_out": False, "command": command,
                    "image": image, "snapshot": copied, "stdout": result.stdout[-20000:], "stderr": result.stderr[-20000:],
                    "isolation": sandbox_plan()["default_policy"]}
        except subprocess.TimeoutExpired as exc:
            return {"exit_code": 124, "timed_out": True, "command": command, "image": image,
                    "snapshot": copied, "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "")[-20000:] if isinstance(exc.stderr, str) else "",
                    "isolation": sandbox_plan()["default_policy"]}
