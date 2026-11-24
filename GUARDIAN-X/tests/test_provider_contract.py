"""Shared provider contract tests for the seven verified Git adapters.

No network I/O is permitted: ``_no_network`` raises if a contract violation
causes an adapter to attempt a request before its capability/auth checks.
"""

from __future__ import annotations

import pytest

from ogx.platform import Capability, CapabilityState, get_provider_registry
from ogx.platform.adapters import (
    AzureDevOpsAdapter,
    BitbucketAdapter,
    CodeCommitAdapter,
    ForgejoAdapter,
    GiteaAdapter,
    GitHubAdapter,
    GitLabAdapter,
)
from ogx.platform.errors import ApprovalRequiredError, CapabilityUnavailableError
from ogx.platform.operations import execute_pull_request, plan_pull_request


def _no_network(*args, **kwargs):
    raise AssertionError("provider contract test attempted network I/O")


PROVIDERS = [
    ("github", lambda: GitHubAdapter(request_fn=_no_network, environ={}, credential=None), "acme/demo"),
    ("gitlab", lambda: GitLabAdapter(request_fn=_no_network, environ={}, credential=None), "acme/demo"),
    ("bitbucket", lambda: BitbucketAdapter(request_fn=_no_network, environ={}, credential=None), "acme/demo"),
    ("gitea", lambda: GiteaAdapter(request_fn=_no_network, environ={}, credential=None), "acme/demo"),
    ("forgejo", lambda: ForgejoAdapter(request_fn=_no_network, environ={}, credential=None), "acme/demo"),
    (
        "azure_devops",
        lambda: AzureDevOpsAdapter(
            request_fn=_no_network, environ={}, credential=None, organization="acme"
        ),
        "project/repo",
    ),
    ("aws_codecommit", lambda: CodeCommitAdapter(request_fn=_no_network, environ={}), "demo"),
]

IDS = [entry[0] for entry in PROVIDERS]


@pytest.mark.parametrize("provider_id,factory,repository", PROVIDERS, ids=IDS)
def test_identity_contract(provider_id, factory, repository):
    adapter = factory()
    identity = adapter.identify()
    assert identity["provider_id"] == provider_id
    assert identity["implementation_status"] == "verified"
    assert identity["api_base"].startswith("https://")
    assert identity["kind"] == "git_hosting"


@pytest.mark.parametrize("provider_id,factory,repository", PROVIDERS, ids=IDS)
def test_unauthenticated_state_is_truthful(provider_id, factory, repository):
    adapter = factory()
    status = adapter.auth_status()
    assert status.configured is False
    assert status.authenticated is False
    assert status.required_env, "a verified adapter must name its credential source"


@pytest.mark.parametrize("provider_id,factory,repository", PROVIDERS, ids=IDS)
def test_read_requires_auth_before_network(provider_id, factory, repository):
    adapter = factory()
    with pytest.raises(CapabilityUnavailableError):
        adapter.get_repository(repository)


@pytest.mark.parametrize("provider_id,factory,repository", PROVIDERS, ids=IDS)
def test_registry_descriptor_matches_adapter(provider_id, factory, repository):
    registry = get_provider_registry()
    descriptor = next(
        item for item in registry.descriptors() if item.provider_id == provider_id
    )
    assert descriptor.implementation_status == "verified"
    assert (
        descriptor.capability_state(Capability.REPOSITORY_METADATA, authenticated=False)
        is CapabilityState.REQUIRES_AUTH
    )


def test_descriptor_only_providers_are_not_operational():
    registry = get_provider_registry()
    for descriptor in registry.descriptors():
        if descriptor.implementation_status != "descriptor_only":
            continue
        assert (
            descriptor.capability_state(Capability.REPOSITORY_METADATA, authenticated=True)
            is CapabilityState.UNSUPPORTED
        )


def test_write_requires_explicit_approval():
    adapter = GitHubAdapter(request_fn=_no_network, environ={}, credential="test-token")
    plan = plan_pull_request(
        adapter,
        "acme/demo",
        title="Test change",
        body="Body",
        head="feature/x",
        base="main",
    )
    with pytest.raises(ApprovalRequiredError):
        execute_pull_request(plan, adapter, approved=False)
