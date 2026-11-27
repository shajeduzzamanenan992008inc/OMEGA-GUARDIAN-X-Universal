"""Plugin execution boundaries.

Two backends exist and neither runs plugin code inside the OGX process:

``docker``
    Preferred. Uses the project's pinned-image container sandbox
    (:func:`ogx.sandbox.run_in_sandbox`): no network, read-only root, dropped
    capabilities, bounded memory/CPU/pids, disposable snapshot. Fails closed
    when Docker or the pinned image is unavailable.

``process``
    Fallback for machines without Docker. Runs the entrypoint as a separate
    Python process in a throwaway working directory with a scrubbed
    environment, wall-clock timeout, bounded output and, on POSIX, CPU,
    memory, file-size, descriptor and process limits. **It cannot block
    outbound network or arbitrary host filesystem reads.** It is therefore
    disabled by default, requires an explicit opt-in, and refuses plugins that
    request ``network.outbound``. GUI/CI policy should prefer Docker.

Capabilities are checked before execution; a plugin that declares a
capability it was not granted never starts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .manifest import PluginManifest
from .permissions import Grant, PermissionDenied, PermissionPolicy

DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024


class RunnerError(RuntimeError):
    """Raised when a plugin cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    timeout_seconds: float = 60.0
    cpu_seconds: int = 30
    memory_bytes: int = 256 * 1024 * 1024
    max_processes: int = 32
    max_open_files: int = 64
    max_file_bytes: int = 16 * 1024 * 1024
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES

    def validate(self) -> None:
        if not 0 < self.timeout_seconds <= 1800:
            raise RunnerError("timeout_seconds must be between 0 and 1800.")
        if not 1 <= self.cpu_seconds <= 1800:
            raise RunnerError("cpu_seconds must be between 1 and 1800.")
        if not 16 * 1024 * 1024 <= self.memory_bytes <= 8 * 1024 * 1024 * 1024:
            raise RunnerError("memory_bytes must be between 16 MiB and 8 GiB.")
        if not 1 <= self.max_processes <= 512:
            raise RunnerError("max_processes must be between 1 and 512.")
        if not 8 <= self.max_open_files <= 4096:
            raise RunnerError("max_open_files must be between 8 and 4096.")
        if not 1024 <= self.max_output_bytes <= 8 * 1024 * 1024:
            raise RunnerError("max_output_bytes must be between 1 KiB and 8 MiB.")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool
    duration_seconds: float
    backend: str
    network_isolated: bool
    filesystem_isolated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
            "duration_seconds": round(self.duration_seconds, 4),
            "backend": self.backend,
            "network_isolated": self.network_isolated,
            "filesystem_isolated": self.filesystem_isolated,
        }


def _bounded_reader(stream: Any, limit: int, sink: list[bytes], state: dict[str, bool]) -> None:
    total = 0
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            remaining = limit - total
            if remaining <= 0:
                state["truncated"] = True
                break
            sink.append(chunk[:remaining])
            total += min(len(chunk), remaining)
            if len(chunk) > remaining:
                state["truncated"] = True
                break
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


class ProcessIsolationBackend:
    """Subprocess boundary with scrubbed env, limits and bounded output."""

    name = "process"
    network_isolated = False
    filesystem_isolated = False

    def __init__(self, *, python_executable: str | None = None) -> None:
        self._python = python_executable or sys.executable

    def available(self) -> bool:
        return bool(self._python) and Path(self._python).exists()

    def _limits_hook(self, limits: ResourceLimits):
        """Return a preexec hook applying POSIX rlimits, or None elsewhere.

        Platforms refuse some limits (macOS rejects ``RLIMIT_NPROC`` and can
        reject ``RLIMIT_AS``). Refusal must not abort the child process, so each
        limit is applied best-effort and the session boundary is created by
        ``start_new_session`` instead of a pre-exec ``setsid``.
        """

        if os.name != "posix":  # pragma: no cover - exercised on Windows
            return None

        def apply_limits() -> None:  # pragma: no cover - POSIX only
            import resource

            for name, value in (
                ("RLIMIT_CPU", limits.cpu_seconds),
                ("RLIMIT_AS", limits.memory_bytes),
                ("RLIMIT_NPROC", limits.max_processes),
                ("RLIMIT_NOFILE", limits.max_open_files),
                ("RLIMIT_FSIZE", limits.max_file_bytes),
            ):
                limit = getattr(resource, name, None)
                if limit is None:
                    continue
                try:
                    resource.setrlimit(limit, (value, value))
                except (OSError, ValueError):
                    continue

        return apply_limits

    def run(
        self,
        plugin_root: Path,
        entrypoint: str,
        args: list[str],
        *,
        limits: ResourceLimits,
        stdin_payload: bytes | None = None,
    ) -> ExecutionResult:
        limits.validate()
        root = Path(plugin_root).resolve(strict=True)
        target = (root / entrypoint).resolve()
        if not str(target).startswith(str(root) + os.sep):
            raise RunnerError("Plugin entrypoint escapes the package root.")
        if not target.is_file():
            raise RunnerError("Plugin entrypoint is missing.")

        workdir = Path(tempfile.mkdtemp(prefix="ogx-plugin-run-"))
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "HOME": str(workdir),
            "TMPDIR": str(workdir),
            "OGX_PLUGIN_SANDBOX": "process",
        }
        started = time.monotonic()
        try:
            process = subprocess.Popen(  # noqa: S603 - argument array, no shell
                [self._python, "-I", str(target), *args],
                cwd=str(workdir),
                env=environment,
                stdin=subprocess.PIPE if stdin_payload is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                preexec_fn=self._limits_hook(limits),
            )
            out_chunks: list[bytes] = []
            err_chunks: list[bytes] = []
            state = {"truncated": False}
            out_thread = threading.Thread(
                target=_bounded_reader,
                args=(process.stdout, limits.max_output_bytes, out_chunks, state),
                daemon=True,
            )
            err_thread = threading.Thread(
                target=_bounded_reader,
                args=(process.stderr, limits.max_output_bytes, err_chunks, state),
                daemon=True,
            )
            out_thread.start()
            err_thread.start()
            if stdin_payload is not None and process.stdin is not None:
                try:
                    process.stdin.write(stdin_payload[: limits.max_output_bytes])
                    process.stdin.close()
                except OSError:
                    pass

            timed_out = False
            try:
                process.wait(timeout=limits.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill(process)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            except KeyboardInterrupt:
                self._kill(process)
                raise
            out_thread.join(timeout=5)
            err_thread.join(timeout=5)
            duration = time.monotonic() - started
            stdout = b"".join(out_chunks).decode("utf-8", "replace")
            stderr = b"".join(err_chunks).decode("utf-8", "replace")
            exit_code = process.returncode
            return ExecutionResult(
                ok=(not timed_out and exit_code == 0),
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                output_truncated=bool(state["truncated"]),
                duration_seconds=duration,
                backend=self.name,
                network_isolated=self.network_isolated,
                filesystem_isolated=self.filesystem_isolated,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), 15)
                time.sleep(0.2)
                if process.poll() is None:
                    os.killpg(os.getpgid(process.pid), 9)
            else:  # pragma: no cover - Windows path
                process.kill()
        except (ProcessLookupError, OSError):
            pass


class DockerIsolationBackend:
    """Pinned-image container backend; fails closed without Docker/image."""

    name = "docker"
    network_isolated = True
    filesystem_isolated = True

    def __init__(self, image: str) -> None:
        from ..sandbox import validate_image_ref

        validate_image_ref(image)
        self.image = image

    def available(self) -> bool:
        from ..sandbox import docker_path

        return docker_path() is not None

    def run(
        self,
        plugin_root: Path,
        entrypoint: str,
        args: list[str],
        *,
        limits: ResourceLimits,
        stdin_payload: bytes | None = None,
    ) -> ExecutionResult:
        limits.validate()
        from ..sandbox import run_in_sandbox

        started = time.monotonic()
        try:
            outcome = run_in_sandbox(
                Path(plugin_root),
                self.image,
                ["python", "-I", entrypoint, *args],
                timeout=max(1, int(limits.timeout_seconds)),
            )
        except Exception as exc:  # noqa: BLE001 - normalize to a runner outcome
            raise RunnerError(f"Container sandbox refused the plugin run: {exc}") from exc
        duration = time.monotonic() - started
        return ExecutionResult(
            ok=bool(outcome.get("ok")),
            exit_code=outcome.get("returncode"),
            stdout=str(outcome.get("stdout", ""))[: limits.max_output_bytes],
            stderr=str(outcome.get("stderr", ""))[: limits.max_output_bytes],
            timed_out=bool(outcome.get("timed_out")),
            output_truncated=bool(outcome.get("truncated")),
            duration_seconds=duration,
            backend=self.name,
            network_isolated=True,
            filesystem_isolated=True,
        )


@dataclass(slots=True)
class PluginRunner:
    """Policy-enforcing facade that selects and drives an isolation backend."""

    backend: ProcessIsolationBackend | DockerIsolationBackend | None = None
    allow_process_backend: bool = False
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)

    def select_backend(self, *, require_network_isolation: bool = False):
        if self.backend is not None:
            backend = self.backend
        else:
            backend = None
            candidates = []
            if os.environ.get("OGX_PLUGIN_SANDBOX_IMAGE"):
                candidates.append(DockerIsolationBackend(os.environ["OGX_PLUGIN_SANDBOX_IMAGE"]))
            if self.allow_process_backend:
                candidates.append(ProcessIsolationBackend())
            for candidate in candidates:
                if candidate.available():
                    backend = candidate
                    break
        if backend is None:
            raise RunnerError(
                "No plugin isolation backend is available. Set OGX_PLUGIN_SANDBOX_IMAGE to a"
                " locally present pinned image, or explicitly opt into the process backend."
            )
        if not backend.available():
            raise RunnerError(
                f"The selected plugin isolation backend {backend.name!r} is unavailable."
            )
        if require_network_isolation and not backend.network_isolated:
            raise RunnerError(
                "This plugin requests outbound network access, which the process backend"
                " cannot isolate. Configure the container sandbox image."
            )
        return backend

    def execute(
        self,
        manifest: PluginManifest,
        grant: Grant | None,
        *,
        plugin_root: Path,
        args: list[str] | None = None,
        limits: ResourceLimits | None = None,
        stdin_payload: bytes | None = None,
    ) -> ExecutionResult:
        effective = self.policy.effective(manifest, grant)
        limits = limits or ResourceLimits()
        require_network_isolation = "network.outbound" in effective
        backend = self.select_backend(require_network_isolation=require_network_isolation)
        if isinstance(backend, ProcessIsolationBackend) and "process.spawn" in effective:
            raise RunnerError(
                "Plugins that spawn subprocesses require the container sandbox backend."
            )
        return backend.run(
            plugin_root,
            manifest.entrypoint,
            list(args or []),
            limits=limits,
            stdin_payload=stdin_payload,
        )
