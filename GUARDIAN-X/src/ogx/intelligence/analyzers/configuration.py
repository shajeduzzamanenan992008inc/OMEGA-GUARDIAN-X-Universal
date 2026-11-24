"""Configuration intelligence. Sensitive files are reported by path only."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from ..models import AnalysisEvidence, Confidence, ConfigurationFile, ConfigurationProfile
from ..options import AnalysisContext
from .common import is_vendored

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("packaging", (
        "pyproject.toml", "setup.py", "setup.cfg", "package.json", "cargo.toml",
        "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
        "gemfile", "pubspec.yaml", "package.swift", "*.csproj", "*.sln", "*.fsproj",
        "*.vbproj", "requirements.txt", "pipfile", "uv.lock", "poetry.lock",
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "cargo.lock", "go.sum",
    )),
    ("build", (
        "cmakelists.txt", "makefile", "meson.build", "build.zig", "build.sbt",
        "settings.gradle", "settings.gradle.kts", "module.bazel", "workspace", "build",
        "*.bazel", "foundry.toml",
    )),
    ("containers", (
        "dockerfile", "containerfile", "docker-compose.yml", "docker-compose.yaml",
        "compose.yml", "compose.yaml", ".dockerignore",
    )),
    ("infrastructure", (
        "*.tf", "*.tfvars", "chart.yaml", "values.yaml", "kustomization.yaml",
        "ansible.cfg", "serverless.yml", "fly.toml", "render.yaml", "app.yaml",
        "vercel.json", "netlify.toml", "procfile",
    )),
    ("environment", (
        ".env.example", ".env.sample", ".env.template", "environment.yml",
        "environment.yaml",
    )),
    ("linting", (
        ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
        "eslint.config.js", "eslint.config.mjs", ".prettierrc", ".prettierrc.json",
        "ruff.toml", ".ruff.toml", ".flake8", ".pylintrc", ".rubocop.yml",
        "phpstan.neon", ".golangci.yml", ".golangci.yaml", "clippy.toml",
        "detekt.yml", "sonar-project.properties", ".stylelintrc", ".stylelintrc.json",
        ".editorconfig",
    )),
    ("typing", (
        "mypy.ini", ".mypy.ini", "pyrightconfig.json", "tsconfig.json",
        "jsconfig.json",
    )),
    ("testing", (
        "pytest.ini", "tox.ini", "noxfile.py", "conftest.py", "jest.config.js",
        "jest.config.ts", "vitest.config.js", "vitest.config.ts",
        "playwright.config.ts", "playwright.config.js", "cypress.config.ts",
        "cypress.config.js", "phpunit.xml", ".rspec", ".mocharc.json",
        ".mocharc.js", ".mocharc.yml", "codecov.yml", ".codecov.yml",
    )),
    ("governance", (
        ".github/dependabot.yml", "renovate.json", ".renovaterc", "codeowners",
        ".github/pull_request_template.md",
    )),
    ("vcs", (".gitignore", ".gitattributes", ".gitmodules", ".hgignore")),
)

SENSITIVE_PATTERNS = (
    (".env", "environment file may contain credentials"),
    (".env.local", "local environment file may contain credentials"),
    ("*.pem", "private key material"),
    ("*.key", "private key material"),
    ("*.p12", "certificate/private key bundle"),
    ("*.pfx", "certificate/private key bundle"),
    ("*.keystore", "signing keystore"),
    ("*.jks", "signing keystore"),
    ("id_rsa", "SSH private key"),
    ("id_ed25519", "SSH private key"),
    (".npmrc", "package registry credentials may be present"),
    (".pypirc", "package registry credentials may be present"),
    ("*.tfstate", "infrastructure state may contain secrets"),
    ("*.tfstate.backup", "infrastructure state may contain secrets"),
    ("credentials.json", "credential file"),
    ("service-account*.json", "service account credentials"),
    ("secrets.*", "secret-bearing file name"),
    ("secret.*", "secret-bearing file name"),
    (".htpasswd", "password file"),
)

SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}


def _match(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _sensitive_reason(name: str) -> str | None:
    folded = name.casefold()
    if folded in SAFE_ENV_NAMES:
        return None
    if folded.startswith(".env") and folded not in SAFE_ENV_NAMES:
        return "environment file may contain credentials"
    for pattern, reason in SENSITIVE_PATTERNS:
        if fnmatch.fnmatchcase(folded, pattern.casefold()):
            return reason
    return None


def analyze_configuration(context: AnalysisContext) -> ConfigurationProfile:
    profile = ConfigurationProfile()
    categories: dict[str, list[str]] = {}
    sensitive: list[ConfigurationFile] = []

    for entry in context.workspace.files:
        if is_vendored(entry.path):
            continue
        name = Path(entry.path).name.casefold()
        reason = _sensitive_reason(name)
        if reason:
            sensitive.append(ConfigurationFile(path=entry.path, category="sensitive",
                                               sensitive=True, reason=reason))
            continue
        matched = False
        for category, patterns in CATEGORY_RULES:
            if _match(name, patterns):
                categories.setdefault(category, []).append(entry.path)
                profile.files.append(ConfigurationFile(path=entry.path, category=category))
                matched = True
                break
        if not matched and entry.path.startswith(".github/workflows/"):
            categories.setdefault("ci", []).append(entry.path)
            profile.files.append(ConfigurationFile(path=entry.path, category="ci"))
    for path in context.profile.ci_files:
        if path not in categories.get("ci", []):
            categories.setdefault("ci", []).append(path)
            profile.files.append(ConfigurationFile(path=path, category="ci"))

    profile.categories = {name: sorted(set(paths))[:100] for name, paths in sorted(categories.items())}
    profile.sensitive_candidates = sorted(sensitive, key=lambda item: item.path)[:100]
    for candidate in profile.sensitive_candidates[:20]:
        context.finding(
            "OGX-INTEL-CONFIG-SENSITIVE", "Possible sensitive configuration file", "configuration",
            Confidence.CONFIRMED, "Filename and path heuristic; file contents were not read or reported",
            evidence=[AnalysisEvidence(kind="sensitive-path", location=candidate.path,
                                       detail=candidate.reason)],
            limitations=[f"{candidate.path}: {candidate.reason}"],
        )
        context.recommend(
            "configuration", f"Review version control exposure: {candidate.path}",
            f"A possible sensitive file was detected by name ({candidate.reason}). "
            "Its contents were not read; rotate credentials if they were exposed.",
            "high")
    if not profile.files and not profile.sensitive_candidates:
        context.add_limitation("configuration", "No recognized configuration file was detected.")
    profile.limitations = [
        "Configuration classification is filename and path based; file contents are not interpreted.",
        "Sensitive-file detection is name based and does not prove that a credential is present.",
        "Sensitive file contents were never read, printed or transmitted.",
    ]
    for limitation in profile.limitations:
        context.add_limitation("configuration", limitation)
    return profile
