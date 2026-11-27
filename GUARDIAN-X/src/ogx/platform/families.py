"""Independent adapter families beyond Git hosting.

These providers are declared, not faked: each generated class overrides no
operation, so its capability matrix reports every repository/workspace/CI/CD
operation as ``unsupported`` and the descriptor records
``implementation_status = "descriptor_only"``. The contracts below define what a
future adapter must implement without changing the universal core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .base import ProviderAdapter
from .capabilities import ProviderKind


@dataclass(frozen=True, slots=True)
class FamilySpec:
    provider_id: str
    display_name: str
    kind: ProviderKind
    default_api_base: str
    credential_env: tuple[str, ...]
    docs_url: str
    limitation: str
    api_version: str = "unknown"
    self_hosted: bool = False


FAMILY_SPECS: tuple[FamilySpec, ...] = (
    # ---------------------------------------------------------- workspaces
    FamilySpec(
        "github_codespaces", "GitHub Codespaces", ProviderKind.CLOUD_WORKSPACE,
        "https://api.github.com", ("OGX_GITHUB_CODESPACES_TOKEN",),
        "https://docs.github.com/en/rest/codespaces",
        "Contract declared only; Codespaces lifecycle operations are not implemented in this build.",
    ),
    FamilySpec(
        "gitpod", "Gitpod", ProviderKind.CLOUD_WORKSPACE,
        "https://api.gitpod.io", ("OGX_GITPOD_TOKEN",),
        "https://www.gitpod.io/docs/references/gitpod-public-api",
        "Contract declared only; the Gitpod public API version is not pinned in this build.",
        api_version="v1", self_hosted=True,
    ),
    FamilySpec(
        "docker_workspace", "Docker workspace", ProviderKind.CLOUD_WORKSPACE,
        "", ("OGX_DOCKER_TOKEN",),
        "https://docs.docker.com/engine/api/",
        "Container lifecycle requires a reviewed local engine boundary; not implemented.",
        api_version="v1.43",
    ),
    FamilySpec(
        "remote_ssh_workspace", "Remote SSH workspace", ProviderKind.CLOUD_WORKSPACE,
        "", ("OGX_SSH_AGENT_SOCKET",),
        "",
        "Remote shell execution is intentionally out of scope; only a contract exists.",
    ),
    FamilySpec(
        "kubernetes_workspace", "Kubernetes workspace", ProviderKind.CLOUD_WORKSPACE,
        "", ("OGX_KUBERNETES_TOKEN",),
        "https://kubernetes.io/docs/reference/using-api/",
        "Read-only, namespace-scoped inspection only. No mutations, exec, port-forward, Helm or Secret values. TLS verification is enforced; insecure-skip-tls-verify is not permitted. Live cluster operations require kubectl and an authorized context; this descriptor records the contract only.",
        api_version="v1",
    ),
    # ---------------------------------------------------------------- CI/CD
    FamilySpec(
        "github_actions", "GitHub Actions", ProviderKind.CICD,
        "https://api.github.com", ("OGX_GITHUB_ACTIONS_TOKEN",),
        "https://docs.github.com/en/rest/actions",
        "Pipeline listing/triggering is not implemented; workflow execution is out of scope.",
        api_version="2022-11-28",
    ),
    FamilySpec(
        "gitlab_ci", "GitLab CI", ProviderKind.CICD,
        "https://gitlab.com/api/v4", ("OGX_GITLAB_CI_TOKEN",),
        "https://docs.gitlab.com/ee/api/pipelines.html",
        "Pipeline listing/triggering is not implemented.", api_version="v4", self_hosted=True,
    ),
    FamilySpec(
        "jenkins", "Jenkins", ProviderKind.CICD, "", ("OGX_JENKINS_TOKEN",),
        "https://www.jenkins.io/doc/book/using/remote-access-api/",
        "Jenkins requires an instance-specific base URL and is not implemented.",
    ),
    FamilySpec(
        "azure_pipelines", "Azure Pipelines", ProviderKind.CICD,
        "https://dev.azure.com", ("OGX_AZURE_PIPELINES_TOKEN",),
        "https://learn.microsoft.com/en-us/rest/api/azure/devops/pipelines/",
        "Pipeline operations are not implemented.", api_version="7.1", self_hosted=True,
    ),
    # -------------------------------------------------- project management
    FamilySpec(
        "jira", "Jira", ProviderKind.PROJECT_MANAGEMENT, "", ("OGX_JIRA_TOKEN",),
        "https://developer.atlassian.com/cloud/jira/platform/rest/v3/",
        "Work-item operations are not implemented; Jira requires a site base URL.",
    ),
    FamilySpec(
        "linear", "Linear", ProviderKind.PROJECT_MANAGEMENT,
        "https://api.linear.app", ("OGX_LINEAR_TOKEN",),
        "https://developers.linear.app/docs/graphql/working-with-the-graphql-api",
        "GraphQL work-item operations are not implemented.", api_version="graphql",
    ),
    FamilySpec(
        "trello", "Trello", ProviderKind.PROJECT_MANAGEMENT,
        "https://api.trello.com/1", ("OGX_TRELLO_TOKEN",),
        "https://developer.atlassian.com/cloud/trello/rest/",
        "Board/card operations are not implemented.", api_version="1",
    ),
    # ---------------------------------------------------- package registries
    FamilySpec(
        "pypi", "PyPI", ProviderKind.PACKAGE_REGISTRY,
        "https://pypi.org/pypi", ("OGX_PYPI_TOKEN",),
        "https://docs.pypi.org/api/",
        "Package lookup and publishing are not implemented.", api_version="json",
    ),
    FamilySpec(
        "npm", "npm registry", ProviderKind.PACKAGE_REGISTRY,
        "https://registry.npmjs.org", ("OGX_NPM_TOKEN",),
        "https://github.com/npm/registry/blob/master/docs/REGISTRY-API.md",
        "Package lookup and publishing are not implemented.",
    ),
    FamilySpec(
        "maven_central", "Maven Central", ProviderKind.PACKAGE_REGISTRY,
        "https://repo1.maven.org/maven2", ("OGX_MAVEN_TOKEN",),
        "https://central.sonatype.org/publish/",
        "Artifact lookup and publishing are not implemented.",
    ),
    FamilySpec(
        "nuget", "NuGet", ProviderKind.PACKAGE_REGISTRY,
        "https://api.nuget.org/v3", ("OGX_NUGET_TOKEN",),
        "https://learn.microsoft.com/en-us/nuget/api/overview",
        "Package lookup and publishing are not implemented.", api_version="v3",
    ),
    FamilySpec(
        "crates_io", "crates.io", ProviderKind.PACKAGE_REGISTRY,
        "https://crates.io/api/v1", ("OGX_CRATES_TOKEN",),
        "https://crates.io/data-access",
        "Crate lookup and publishing are not implemented.", api_version="v1",
    ),
    # ------------------------------------------------- cloud infrastructure
    FamilySpec(
        "aws", "Amazon Web Services", ProviderKind.CLOUD_INFRASTRUCTURE,
        "", ("OGX_AWS_ACCESS_KEY_ID",),
        "https://docs.aws.amazon.com/",
        "Cloud resource operations are not implemented; no account is contacted.",
    ),
    FamilySpec(
        "azure", "Microsoft Azure", ProviderKind.CLOUD_INFRASTRUCTURE,
        "https://management.azure.com", ("OGX_AZURE_TOKEN",),
        "https://learn.microsoft.com/en-us/rest/api/azure/",
        "Cloud resource operations are not implemented.",
    ),
    FamilySpec(
        "gcp", "Google Cloud", ProviderKind.CLOUD_INFRASTRUCTURE,
        "https://cloudresourcemanager.googleapis.com", ("OGX_GCP_TOKEN",),
        "https://cloud.google.com/apis/docs/overview",
        "Cloud resource operations are not implemented.", api_version="v3",
    ),
)


def build_family_class(spec: FamilySpec) -> type[ProviderAdapter]:
    """Create a descriptor-only adapter class for a declared family provider."""

    class_name = "".join(part.capitalize() for part in spec.provider_id.split("_")) + "Adapter"
    attrs = {
        "provider_id": spec.provider_id,
        "display_name": spec.display_name,
        "kind": spec.kind,
        "version": "0.1.0",
        "api_version": spec.api_version,
        "default_api_base": spec.default_api_base,
        "credential_env": spec.credential_env,
        "self_hosted": spec.self_hosted,
        "docs_url": spec.docs_url,
        "implementation_status": "descriptor_only",
        "limitations": (spec.limitation,),
        "__doc__": f"Descriptor-only adapter for {spec.display_name}.",
    }
    return type(class_name, (ProviderAdapter,), attrs)


FAMILY_ADAPTERS: tuple[type[ProviderAdapter], ...] = tuple(
    build_family_class(spec) for spec in FAMILY_SPECS
)


def family_classes(kind: ProviderKind | None = None) -> Iterable[type[ProviderAdapter]]:
    if kind is None:
        return FAMILY_ADAPTERS
    return tuple(cls for cls in FAMILY_ADAPTERS if cls.kind is kind)
