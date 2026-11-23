from __future__ import annotations

import shutil
from typing import Any

TOOLS = {
    "git": "Repository operations",
    "python": "Python syntax and ecosystem tooling",
    "py": "Windows Python launcher",
    "pip": "Python package management",
    "node": "JavaScript runtime",
    "npm": "JavaScript package manager and audit adapter",
    "pnpm": "JavaScript package manager",
    "yarn": "JavaScript package manager",
    "bun": "JavaScript runtime and package manager",
    "go": "Go build and module tooling",
    "cargo": "Rust package/build tooling",
    "rustc": "Rust compiler",
    "java": "JVM runtime",
    "mvn": "Maven build tooling",
    "gradle": "Gradle build tooling",
    "dotnet": ".NET SDK",
    "composer": "PHP package manager",
    "php": "PHP runtime",
    "bundle": "Ruby dependency tooling",
    "ruby": "Ruby runtime",
    "swift": "Swift toolchain",
    "cmake": "C/C++ build tooling",
    "docker": "Container tooling",
    "trivy": "Container and dependency security scanner",
    "semgrep": "Static analysis adapter",
    "gitleaks": "Secret scanning adapter",
    "osv-scanner": "Vulnerability scanner adapter",
    "syft": "SBOM generator adapter",
    "grype": "Vulnerability scanner adapter",
    "ollama": "Optional local model runtime",
    "copilot": "GitHub Copilot CLI",
    "codex": "Codex-compatible CLI",
    "claude": "Claude Code CLI",
    "gemini": "Gemini CLI",
}


def discover_toolchains() -> list[dict[str, Any]]:
    return [{"name": name, "purpose": purpose, "path": shutil.which(name), "available": shutil.which(name) is not None}
            for name, purpose in TOOLS.items()]
