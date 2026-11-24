"""SourceHut adapter declaration.

SourceHut exposes a GraphQL API at ``https://git.sr.ht/query``. This build does
not ship a validated GraphQL client: the schema and operation documents were not
verified against a live instance, and the project rule is to never invent API
contracts. The provider is therefore registered with an explicit
``descriptor_only`` status so the capability matrix reports every operation as
unsupported instead of pretending it works.
"""

from __future__ import annotations

from ..base import ProviderAdapter
from ..capabilities import ProviderKind


class SourceHutAdapter(ProviderAdapter):
    provider_id = "sourcehut"
    display_name = "SourceHut"
    kind = ProviderKind.GIT_HOSTING
    version = "0.1.0"
    api_version = "graphql"
    default_api_base = "https://git.sr.ht"
    credential_env = ("OGX_SOURCEHUT_TOKEN",)
    self_hosted = True
    docs_url = "https://man.sr.ht/git.sr.ht/"
    implementation_status = "descriptor_only"
    required_permissions = ("repo:read",)
    limitations = (
        "Descriptor only: no GraphQL operation document has been validated against a live SourceHut instance.",
        "Every repository, branch, commit, file and pull-request capability is reported unsupported.",
        "Identification and credential discovery work; no network operation is implemented.",
    )
