"""Host-type to lifecycle adapter construction."""

from __future__ import annotations

from ..lifecycle import HostAdapter, HostSessionConfig, UnsupportedHostTypeError
from .filesystem_runtime import FilesystemHostAdapter
from .git_runtime import GitHostAdapter

SUPPORTED_HOST_TYPES = ("local_filesystem", "local_git")


def create_host_adapter(host_type: str, config: HostSessionConfig) -> HostAdapter:
    if host_type == "local_filesystem":
        return FilesystemHostAdapter(config)
    if host_type == "local_git":
        return GitHostAdapter(config)
    raise UnsupportedHostTypeError(host_type)
