from __future__ import annotations

from collections import Counter
import json
import os
import re
import tomllib
from pathlib import Path

from ogx.kernel.models import ProjectProfile
from ogx.kernel.config import DEFAULT_EXCLUDES
from ogx.kernel.paths import LEGACY_LOCAL_DIRS

MANIFEST_MAP = {
    "pyproject.toml": ("Python", "Python packaging"),
    "setup.py": ("Python", "setuptools"),
    "requirements.txt": ("Python", "pip"),
    "requirements-dev.txt": ("Python", "pip"),
    "Pipfile": ("Python", "Pipenv"),
    "poetry.lock": ("Python", "Poetry"),
    "uv.lock": ("Python", "uv"),
    "environment.yml": ("Python", "Conda"),
    "package.json": ("JavaScript/TypeScript", "npm-compatible"),
    "package-lock.json": ("JavaScript/TypeScript", "npm"),
    "pnpm-lock.yaml": ("JavaScript/TypeScript", "pnpm"),
    "yarn.lock": ("JavaScript/TypeScript", "Yarn"),
    "bun.lock": ("JavaScript/TypeScript", "Bun"),
    "bun.lockb": ("JavaScript/TypeScript", "Bun"),
    "Cargo.toml": ("Rust", "Cargo"),
    "Cargo.lock": ("Rust", "Cargo"),
    "go.mod": ("Go", "Go Modules"),
    "go.sum": ("Go", "Go Modules"),
    "pom.xml": ("Java", "Maven"),
    "build.gradle": ("Java/JVM", "Gradle"),
    "build.gradle.kts": ("Kotlin/JVM", "Gradle"),
    "composer.json": ("PHP", "Composer"),
    "composer.lock": ("PHP", "Composer"),
    "Gemfile": ("Ruby", "Bundler"),
    "Gemfile.lock": ("Ruby", "Bundler"),
    "Package.swift": ("Swift", "Swift Package Manager"),
    "CMakeLists.txt": ("C/C++", "CMake"),
    "conanfile.py": ("C/C++", "Conan"),
    "conanfile.txt": ("C/C++", "Conan"),
    "vcpkg.json": ("C/C++", "vcpkg"),
    "pubspec.yaml": ("Dart", "Pub"),
    "mix.exs": ("Elixir", "Mix"),
    "Package.swift": ("Swift", "Swift Package Manager"),
    "Gemfile": ("Ruby", "Bundler"),
    "Pipfile": ("Python", "Pipenv"),
    "deno.json": ("JavaScript/TypeScript", "Deno"),
    "deno.jsonc": ("JavaScript/TypeScript", "Deno"),
    "shard.yml": ("Crystal", "Shards"),
    "rebar.config": ("Erlang", "Rebar3"),
    "stack.yaml": ("Haskell", "Stack"),
    "cabal.project": ("Haskell", "Cabal"),
    "Package.resolved": ("Swift", "Swift Package Manager"),
    "build.sbt": ("Scala", "sbt"),
    "pubspec.lock": ("Dart", "Pub"),
}
LOCK_NAMES = {
    "poetry.lock", "uv.lock", "Pipfile.lock", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "bun.lock", "bun.lockb", "Cargo.lock", "go.sum", "composer.lock",
    "Gemfile.lock", "packages.lock.json", "Package.resolved",
}
CI_PARTS = (".github/workflows/", ".gitlab-ci", ".circleci/", "azure-pipelines", ".buildkite/")


def detect_project(root: Path, files: list[Path] | None = None) -> ProjectProfile:
    root = root.resolve()
    if files is None:
        files = []
        excluded = set(DEFAULT_EXCLUDES) | set(LEGACY_LOCAL_DIRS)
        for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
            dirs[:] = [d for d in dirs if d not in excluded and not (Path(current) / d).is_symlink()]
            for name in names:
                candidate = Path(current) / name
                if not candidate.is_symlink() and candidate.is_file():
                    files.append(candidate)
                    if len(files) >= 50000:
                        break
            if len(files) >= 50000:
                break
    rels = sorted(p.relative_to(root).as_posix() for p in files)
    names = {Path(p).name for p in rels}
    languages: set[str] = set()
    ecosystems: set[str] = set()
    manifests: list[str] = []
    lockfiles: list[str] = []
    ci_files = [p for p in rels if any(part in p for part in CI_PARTS)]

    for rel in rels:
        name = Path(rel).name
        if name in MANIFEST_MAP:
            language, ecosystem = MANIFEST_MAP[name]
            languages.add(language)
            ecosystems.add(ecosystem)
            if name not in LOCK_NAMES:
                manifests.append(rel)
            else:
                lockfiles.append(rel)
        elif name.startswith("requirements") and name.endswith(".txt"):
            languages.add("Python")
            ecosystems.add("pip")
            manifests.append(rel)
        elif name.endswith((".csproj", ".vbproj", ".fsproj")) or name == "packages.config":
            languages.add("C# / .NET")
            ecosystems.add("NuGet")
            manifests.append(rel)
        elif name in LOCK_NAMES:
            lockfiles.append(rel)

    extension_counts: Counter[str] = Counter(Path(p).suffix.lower() for p in rels)
    extension_languages = {
        ".py": "Python", ".js": "JavaScript/TypeScript", ".jsx": "JavaScript/TypeScript",
        ".ts": "JavaScript/TypeScript", ".tsx": "JavaScript/TypeScript", ".vue": "JavaScript/TypeScript", ".svelte": "JavaScript/TypeScript", ".java": "Java",
        ".kt": "Kotlin", ".go": "Go", ".rs": "Rust", ".php": "PHP", ".rb": "Ruby",
        ".cs": "C#", ".swift": "Swift", ".c": "C/C++", ".h": "C/C++",
        ".cpp": "C/C++", ".hpp": "C/C++", ".dart": "Dart", ".scala": "Scala", ".sc": "Scala",
        ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hrl": "Erlang", ".lua": "Lua",
        ".pl": "Perl", ".r": "R", ".R": "R", ".jl": "Julia", ".hs": "Haskell", ".lhs": "Haskell",
        ".sol": "Solidity", ".clj": "Clojure", ".cljs": "ClojureScript", ".cr": "Crystal",
        ".sql": "SQL", ".tf": "Terraform", ".proto": "Protocol Buffers", ".vue": "Vue", ".svelte": "Svelte",
    }
    for ext, count in extension_counts.items():
        if count and ext in extension_languages:
            languages.add(extension_languages[ext])

    frameworks: set[str] = set()
    if "manage.py" in names:
        frameworks.add("Django (indicator)")
    if "next.config.js" in names or "next.config.ts" in names or "next.config.mjs" in names:
        frameworks.add("Next.js")
    if "vite.config.ts" in names or "vite.config.js" in names:
        frameworks.add("Vite")
    if "angular.json" in names:
        frameworks.add("Angular")
    if "Cargo.toml" in names:
        frameworks.add("Rust project")
    if "pom.xml" in names:
        frameworks.add("Maven project")
    if "pubspec.yaml" in names:
        frameworks.add("Dart / Flutter (indicator)")
    if "angular.json" in names:
        frameworks.add("Angular")
    if "nuxt.config.ts" in names or "nuxt.config.js" in names:
        frameworks.add("Nuxt")
    if "svelte.config.js" in names or "svelte.config.ts" in names:
        frameworks.add("SvelteKit (indicator)")
    if "manage.py" in names or "settings.py" in names:
        frameworks.add("Django (indicator)")
    if "artisan" in names:
        frameworks.add("Laravel (indicator)")
    if "artisan" in names or "composer.json" in names:
        if "artisan" in names:
            frameworks.add("Laravel")
    if "Gemfile" in names and "config/application.rb" in rels:
        frameworks.add("Rails (indicator)")
    if "Dockerfile" in names or "docker-compose.yml" in names or "compose.yaml" in names:
        frameworks.add("Docker")
    if "Chart.yaml" in names:
        frameworks.add("Helm")
    if "terraform.tf" in names or any(p.endswith(".tf") for p in rels):
        frameworks.add("Terraform")
    if "Chart.yaml" in names:
        frameworks.add("Helm")

    # Framework indicators from manifests are intentionally shallow; no package code is loaded.
    for rel in rels:
        path = root / rel
        try:
            if path.name == "package.json" and path.stat().st_size <= 500_000:
                data = json.loads(path.read_text(encoding="utf-8"))
                deps = {}
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    if isinstance(data.get(section), dict): deps.update(data[section])
                if "react" in deps: frameworks.add("React")
                if "vue" in deps: frameworks.add("Vue")
                if "express" in deps: frameworks.add("Express")
                if "fastify" in deps: frameworks.add("Fastify")
                if "@nestjs/core" in deps: frameworks.add("NestJS")
                if "next" in deps: frameworks.add("Next.js")
                if "@angular/core" in deps: frameworks.add("Angular")
                if "svelte" in deps: frameworks.add("Svelte")
            elif path.name == "pyproject.toml" and path.stat().st_size <= 500_000:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                text = str(data).lower()
                for package, label in (("fastapi", "FastAPI"), ("flask", "Flask"), ("django", "Django"),
                                        ("pytest", "pytest"), ("sqlalchemy", "SQLAlchemy"), ("torch", "PyTorch"),
                                        ("tensorflow", "TensorFlow")):
                    if package in text: frameworks.add(label)
            elif path.name.startswith("requirements") and path.suffix == ".txt" and path.stat().st_size <= 500_000:
                text = path.read_text(encoding="utf-8", errors="replace").lower()
                for package, label in (("fastapi", "FastAPI"), ("flask", "Flask"), ("django", "Django"),
                                        ("sqlalchemy", "SQLAlchemy"), ("torch", "PyTorch"), ("tensorflow", "TensorFlow")):
                    if re.search(rf"(?m)^\s*{package}(?:[<=>~!;\[]|\s|$)", text): frameworks.add(label)
        except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
            pass

    return ProjectProfile(
        root=str(root), name=root.name or str(root), languages=sorted(languages),
        ecosystems=sorted(ecosystems), frameworks=sorted(frameworks),
        manifests=sorted(manifests), lockfiles=sorted(set(lockfiles)), ci_files=sorted(ci_files),
    )
