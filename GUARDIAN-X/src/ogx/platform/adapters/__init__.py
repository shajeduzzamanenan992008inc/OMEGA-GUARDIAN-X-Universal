from __future__ import annotations

from .aws_codecommit import CodeCommitAdapter
from .azure_devops import AzureDevOpsAdapter
from .bitbucket import BitbucketAdapter
from .gitea import ForgejoAdapter, GiteaAdapter
from .github import GitHubAdapter
from .gitlab import GitLabAdapter
from .rest import RestAdapter
from .sourcehut import SourceHutAdapter

__all__ = [
    "RestAdapter",
    "GitHubAdapter",
    "GitLabAdapter",
    "BitbucketAdapter",
    "GiteaAdapter",
    "ForgejoAdapter",
    "AzureDevOpsAdapter",
    "SourceHutAdapter",
    "CodeCommitAdapter",
]
