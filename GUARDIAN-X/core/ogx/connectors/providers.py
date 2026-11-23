from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlparse


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    token_env: str
    api_base: str


PROVIDERS = {
    "github": ProviderConfig("github", "OGX_GITHUB_TOKEN", "https://api.github.com"),
    "gitlab": ProviderConfig("gitlab", "OGX_GITLAB_TOKEN", "https://gitlab.com/api/v4"),
    "bitbucket": ProviderConfig("bitbucket", "OGX_BITBUCKET_TOKEN", "https://api.bitbucket.org/2.0"),
    "gitea": ProviderConfig("gitea", "OGX_GITEA_TOKEN", ""),
    "forgejo": ProviderConfig("forgejo", "OGX_FORGEJO_TOKEN", ""),
}


def _request(url: str, token: str, provider: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "OMEGA-GUARDIAN-X"}
    if provider == "github":
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    elif provider == "gitlab":
        headers["PRIVATE-TOKEN"] = token
    elif provider in {"gitea", "forgejo"}:
        headers["Authorization"] = f"token {token}"
    elif provider == "bitbucket":
        if ":" in token:
            headers["Authorization"] = "Basic " + base64.b64encode(token.encode("utf-8")).decode("ascii")
        else:
            headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(2_000_000)
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise RuntimeError(f"{provider} API returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{provider} API request failed: {type(exc).__name__}") from exc


def _validate_api_base(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    local = host in {"localhost", "127.0.0.1", "::1"}
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API base URL must have a hostname and no embedded credentials/query/fragment.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("Provider API credentials may only be sent over HTTPS (HTTP is allowed for loopback development).")
    return value.rstrip("/")


def repository_coordinates(remote_url: str) -> tuple[str, str, str]:
    value = remote_url.strip()
    # Support HTTPS, SSH URLs and scp-like Git remotes (git@host:owner/repo.git).
    if re.match(r"^[^/@:]+@[^/:]+:.+$", value):
        user_host, path = value.split(":", 1)
        host = user_host.rsplit("@", 1)[-1].lower()
    else:
        parsed = urlparse(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower()
        path = parsed.path
    path = path.strip("/")
    path = re.sub(r"\.git$", "", path)
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError("Remote URL must identify an owner/workspace and repository.")
    provider = ("github" if host == "github.com" or host.endswith(".github.com") else
                "gitlab" if host == "gitlab.com" or host.endswith(".gitlab.com") else
                "bitbucket" if host == "bitbucket.org" else
                "forgejo" if "forgejo" in host else
                "gitea" if "gitea" in host else "generic-git")
    return provider, unquote("/".join(parts[:-1])), unquote(parts[-1])


class ProviderClient:
    """Small, token-from-environment API client; never persists credentials."""

    def __init__(self, provider: str, remote_url: str):
        detected, self.namespace, self.repo = repository_coordinates(remote_url)
        self.remote_url = remote_url
        if provider == "gitea-or-forgejo" and detected in {"gitea", "forgejo", "generic-git"}:
            self.provider = "gitea" if os.environ.get("OGX_GITEA_TOKEN") else "forgejo"
        elif provider in {"github", "gitlab", "bitbucket", "gitea", "forgejo"} and detected in {provider, "generic-git"}:
            self.provider = provider  # explicit provider enables supported self-hosted domains
        else:
            raise ValueError(f"Remote provider is {detected}, not {provider}.")
        self.config = PROVIDERS[self.provider]
        self.token = os.environ.get(self.config.token_env, "")
        if self.provider in {"github", "gitlab", "bitbucket"}:
            override = os.environ.get(f"OGX_{self.provider.upper()}_API_BASE")
            if detected == "generic-git" and not override:
                raise ValueError(f"This appears to be a self-hosted {self.provider} host; set OGX_{self.provider.upper()}_API_BASE explicitly.")
            if override:
                self.config = ProviderConfig(self.provider, self.config.token_env, _validate_api_base(override))
        if not self.token:
            raise PermissionError(f"Set {self.config.token_env} in your environment; OGX never stores provider tokens.")
        if self.provider == "gitlab":
            self.project_key = quote(f"{self.namespace}/{self.repo}", safe="")
        else:
            self.project_key = f"{self.namespace}/{self.repo}"
        if self.provider in {"gitea", "forgejo"}:
            custom_base = os.environ.get(f"OGX_{self.provider.upper()}_API_BASE")
            if custom_base:
                api_base = _validate_api_base(custom_base)
            elif re.match(r"^[^/@:]+@[^/:]+:.+$", remote_url.strip()):
                host = remote_url.strip().split(":", 1)[0].rsplit("@", 1)[-1]
                api_base = _validate_api_base(f"https://{host}/api/v1")
            else:
                parsed = urlparse(remote_url if "://" in remote_url else "https://" + remote_url)
                authority = parsed.hostname or ""
                if parsed.port:
                    authority += f":{parsed.port}"
                api_base = _validate_api_base(f"{parsed.scheme}://{authority}/api/v1")
            self.config = ProviderConfig(self.provider, self.config.token_env, api_base)

    def get_repository(self) -> dict[str, Any]:
        if self.provider == "github":
            url = f"{self.config.api_base}/repos/{self.project_key}"
        elif self.provider == "gitlab":
            url = f"{self.config.api_base}/projects/{self.project_key}"
        elif self.provider == "bitbucket":
            url = f"{self.config.api_base}/repositories/{self.project_key}"
        else:
            url = f"{self.config.api_base}/repos/{self.project_key}"
        return _request(url, self.token, self.provider)

    def create_pull_request(self, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        if not all(isinstance(v, str) and v.strip() for v in (title, head, base)):
            raise ValueError("title, head and base are required.")
        branch_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
        for branch in (head, base):
            if (not branch_pattern.fullmatch(branch) or ".." in branch or "//" in branch
                    or branch.endswith(("/", ".")) or "@{" in branch):
                raise ValueError("Invalid branch name for remote Pull/Merge Request.")
        if head in {"main", "master", base}:
            raise ValueError("Head must be a dedicated feature branch, not the protected/base branch.")
        if self.provider == "github":
            url = f"{self.config.api_base}/repos/{self.project_key}/pulls"
            payload = {"title": title, "body": body, "head": head, "base": base, "draft": True}
        elif self.provider == "gitlab":
            url = f"{self.config.api_base}/projects/{self.project_key}/merge_requests"
            payload = {"title": title, "description": body, "source_branch": head, "target_branch": base, "draft": True}
        elif self.provider == "bitbucket":
            url = f"{self.config.api_base}/repositories/{self.project_key}/pullrequests"
            payload = {"title": title, "description": body, "source": {"branch": {"name": head}}, "destination": {"branch": {"name": base}}, "close_source_branch": False}
        else:
            url = f"{self.config.api_base}/repos/{self.project_key}/pulls"
            payload = {"title": title, "body": body, "head": head, "base": base}
        return _request(url, self.token, self.provider, payload)
