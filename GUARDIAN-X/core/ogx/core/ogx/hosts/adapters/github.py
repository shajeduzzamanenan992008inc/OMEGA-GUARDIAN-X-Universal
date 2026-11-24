"""Compatibility shim.

The canonical GitHub implementation is :class:`ogx.platform.adapters.github.GitHubAdapter`.
This module exists only so historical ``ogx.hosts`` imports keep working. It
contains no API logic and must never grow any.
"""

from __future__ import annotations

from ...platform.adapters.github import GitHubAdapter

GitHubProvider = GitHubAdapter

__all__ = ["GitHubProvider", "GitHubAdapter"]
