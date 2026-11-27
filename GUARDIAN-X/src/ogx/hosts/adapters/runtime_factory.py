"""Host-type to lifecycle adapter construction."""

from __future__ import annotations

from ..lifecycle import HostAdapter, HostSessionConfig, UnsupportedHostTypeError
from .filesystem_runtime import FilesystemHostAdapter
from .git_runtime import GitHostAdapter
from .github_runtime import GitHubHostAdapter
from .provider_runtime import PROVIDER_HOST_TYPES, ProviderGitHostAdapter

SUPPORTED_HOST_TYPES = ("local_filesystem", "local_git", "github") + PROVIDER_HOST_TYPES


def create_host_adapter(host_type: str, config: HostSessionConfig) -> HostAdapter:
    if host_type == "local_filesystem":
        return FilesystemHostAdapter(config)
    if host_type == "local_git":
        return GitHostAdapter(config)
    if host_type == "github":
        return GitHubHostAdapter(config)
    if host_type in PROVIDER_HOST_TYPES:
        return ProviderGitHostAdapter(config, provider_id=host_type)
    raise UnsupportedHostTypeError(host_type)
