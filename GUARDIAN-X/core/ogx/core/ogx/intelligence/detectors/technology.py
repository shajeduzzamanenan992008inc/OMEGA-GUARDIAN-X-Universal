"""Evidence-based technology and framework detection.

Detection sources are declared dependencies, configuration files and explicit
file markers.  Directory names alone are never treated as technology evidence.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..models import Confidence, FrameworkProfile, TechnologyProfile, strongest
from ..options import AnalysisContext

MAX_CONTENT_FILES = 200
MAX_CONTENT_BYTES = 512_000


@dataclass(frozen=True, slots=True)
class TechnologySignature:
    name: str
    category: str
    ecosystem: str
    packages: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    file_confidence: Confidence = Confidence.LIKELY
    note: str = ""


def _norm(value: str) -> str:
    return value.strip().lower().replace("_", "-")


SIGNATURES: tuple[TechnologySignature, ...] = (
    # Python
    TechnologySignature("Django", "framework", "pip", ("django",), files=("manage.py",)),
    TechnologySignature("Flask", "framework", "pip", ("flask",)),
    TechnologySignature("FastAPI", "framework", "pip", ("fastapi",)),
    TechnologySignature("Pydantic", "library", "pip", ("pydantic",)),
    TechnologySignature("SQLAlchemy", "library", "pip", ("sqlalchemy",)),
    TechnologySignature("PyTorch", "library", "pip", ("torch", "pytorch", "torchvision")),
    TechnologySignature("TensorFlow", "library", "pip", ("tensorflow", "tensorflow-cpu")),
    TechnologySignature("scikit-learn", "library", "pip", ("scikit-learn", "sklearn")),
    TechnologySignature("pandas", "library", "pip", ("pandas",)),
    TechnologySignature("NumPy", "library", "pip", ("numpy",)),
    TechnologySignature("Jupyter", "tooling", "pip", ("jupyter", "notebook", "ipykernel")),
    TechnologySignature("pytest", "testing", "pip", ("pytest",), files=("pytest.ini", "conftest.py")),
    TechnologySignature("Click", "library", "pip", ("click",)),
    TechnologySignature("Typer", "library", "pip", ("typer",)),
    TechnologySignature("Celery", "library", "pip", ("celery",)),
    TechnologySignature("Streamlit", "framework", "pip", ("streamlit",)),
    TechnologySignature("Apache Airflow", "framework", "pip", ("apache-airflow",)),
    # JavaScript / TypeScript
    TechnologySignature("React", "framework", "npm", ("react",)),
    TechnologySignature("Next.js", "framework", "npm", ("next",), files=("next.config.js", "next.config.mjs", "next.config.ts")),
    TechnologySignature("Vue", "framework", "npm", ("vue",)),
    TechnologySignature("Angular", "framework", "npm", ("@angular/core",), files=("angular.json",)),
    TechnologySignature("Svelte", "framework", "npm", ("svelte",)),
    TechnologySignature("SvelteKit", "framework", "npm", ("@sveltejs/kit",), files=("svelte.config.js", "svelte.config.ts")),
    TechnologySignature("Express", "framework", "npm", ("express",)),
    TechnologySignature("Fastify", "framework", "npm", ("fastify",)),
    TechnologySignature("NestJS", "framework", "npm", ("@nestjs/core",)),
    TechnologySignature("React Native", "framework", "npm", ("react-native",)),
    TechnologySignature("Electron", "framework", "npm", ("electron",), files=("electron-builder.yml", "electron-builder.json")),
    TechnologySignature("Tauri", "framework", "npm", ("@tauri-apps/api",), files=("tauri.conf.json",)),
    TechnologySignature("Vite", "tooling", "npm", ("vite",), files=("vite.config.js", "vite.config.ts", "vite.config.mjs")),
    TechnologySignature("Jest", "testing", "npm", ("jest",), files=("jest.config.js", "jest.config.ts", "jest.config.mjs")),
    TechnologySignature("Vitest", "testing", "npm", ("vitest",), files=("vitest.config.js", "vitest.config.ts")),
    TechnologySignature("Mocha", "testing", "npm", ("mocha",), files=(".mocharc.json", ".mocharc.js", ".mocharc.yml")),
    TechnologySignature("Playwright", "testing", "npm", ("@playwright/test", "playwright"), files=("playwright.config.ts", "playwright.config.js")),
    TechnologySignature("Cypress", "testing", "npm", ("cypress",), files=("cypress.config.ts", "cypress.config.js")),
    TechnologySignature("TypeScript", "tooling", "npm", ("typescript",), files=("tsconfig.json",), file_confidence=Confidence.CONFIRMED),
    # JVM
    TechnologySignature("Spring Boot", "framework", "maven", prefixes=("org.springframework.boot:",)),
    TechnologySignature("Spring Boot", "framework", "gradle", prefixes=("org.springframework.boot:",)),
    TechnologySignature("Spring Framework", "framework", "maven", prefixes=("org.springframework:",)),
    TechnologySignature("Spring Framework", "framework", "gradle", prefixes=("org.springframework:",)),
    TechnologySignature("JUnit", "testing", "maven", ("junit:junit",), prefixes=("org.junit:", "org.junit.jupiter:")),
    TechnologySignature("JUnit", "testing", "gradle", prefixes=("org.junit:", "org.junit.jupiter:")),
    TechnologySignature("Jetpack Compose", "library", "gradle", prefixes=("androidx.compose:",)),
    TechnologySignature("Hibernate", "library", "maven", prefixes=("org.hibernate:",)),
    TechnologySignature("Hibernate", "library", "gradle", prefixes=("org.hibernate:",)),
    # C# / .NET
    TechnologySignature("ASP.NET Core", "framework", "nuget", prefixes=("microsoft.aspnetcore.",)),
    TechnologySignature("Entity Framework Core", "library", "nuget", prefixes=("microsoft.entityframeworkcore",)),
    TechnologySignature("xUnit", "testing", "nuget", ("xunit", "xunit.v3")),
    TechnologySignature("NUnit", "testing", "nuget", ("nunit",)),
    TechnologySignature("MSTest", "testing", "nuget", prefixes=("mstest.",)),
    # Go
    TechnologySignature("Gin", "framework", "go", ("github.com/gin-gonic/gin",)),
    TechnologySignature("Echo", "framework", "go", prefixes=("github.com/labstack/echo",)),
    TechnologySignature("Fiber", "framework", "go", prefixes=("github.com/gofiber/fiber",)),
    TechnologySignature("Cobra", "library", "go", ("github.com/spf13/cobra",)),
    TechnologySignature("GORM", "library", "go", ("gorm.io/gorm",)),
    # Rust
    TechnologySignature("Tokio", "library", "cargo", ("tokio",)),
    TechnologySignature("Axum", "framework", "cargo", ("axum",)),
    TechnologySignature("Actix Web", "framework", "cargo", ("actix-web",)),
    TechnologySignature("Clap", "library", "cargo", ("clap",)),
    TechnologySignature("Serde", "library", "cargo", ("serde",)),
    # PHP / Ruby / Dart
    TechnologySignature("Laravel", "framework", "composer", ("laravel/framework", "laravel/laravel"), files=("artisan",)),
    TechnologySignature("Symfony", "framework", "composer", ("symfony/framework-bundle", "symfony/symfony")),
    TechnologySignature("Rails", "framework", "rubygems", ("rails",), globs=("config/application.rb",)),
    TechnologySignature("RSpec", "testing", "rubygems", ("rspec", "rspec-rails"), files=(".rspec",)),
    TechnologySignature("Flutter", "framework", "pub", ("flutter",)),
    # Infrastructure and delivery
    TechnologySignature("Docker", "infrastructure", "container",
                        files=("Dockerfile", "Containerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")),
    TechnologySignature("Helm", "infrastructure", "kubernetes", files=("Chart.yaml",)),
    TechnologySignature("Terraform", "infrastructure", "terraform", globs=("**/*.tf", "*.tf")),
    TechnologySignature("GitHub Actions", "infrastructure", "ci", globs=(".github/workflows/*.yml", ".github/workflows/*.yaml")),
    TechnologySignature("GitLab CI", "infrastructure", "ci", files=(".gitlab-ci.yml",)),
    TechnologySignature("Jenkins", "infrastructure", "ci", files=("Jenkinsfile",)),
    TechnologySignature("Ansible", "infrastructure", "ansible", files=("ansible.cfg",), globs=("**/playbook*.yml", "**/playbook*.yaml")),
    # Databases and datastores (client declarations, not server detection)
    TechnologySignature("PostgreSQL (client)", "database", "pip", ("psycopg2", "psycopg2-binary", "psycopg", "asyncpg")),
    TechnologySignature("PostgreSQL (client)", "database", "npm", ("pg", "postgres")),
    TechnologySignature("PostgreSQL (client)", "database", "go", ("github.com/lib/pq", "github.com/jackc/pgx")),
    TechnologySignature("PostgreSQL (client)", "database", "cargo", ("postgres", "tokio-postgres")),
    TechnologySignature("PostgreSQL (client)", "database", "nuget", ("npgsql",)),
    TechnologySignature("Redis (client)", "database", "pip", ("redis", "aioredis")),
    TechnologySignature("Redis (client)", "database", "npm", ("redis", "ioredis")),
    TechnologySignature("Redis (client)", "database", "go", prefixes=("github.com/redis/go-redis", "github.com/go-redis/redis")),
    TechnologySignature("Redis (client)", "database", "cargo", ("redis",)),
    TechnologySignature("MongoDB (client)", "database", "pip", ("pymongo", "motor")),
    TechnologySignature("MongoDB (client)", "database", "npm", ("mongoose", "mongodb")),
    TechnologySignature("MongoDB (client)", "database", "go", ("go.mongodb.org/mongo-driver",)),
    TechnologySignature("MongoDB (client)", "database", "cargo", ("mongodb",)),
    TechnologySignature("MySQL (client)", "database", "pip", ("pymysql", "mysqlclient", "aiomysql")),
    TechnologySignature("MySQL (client)", "database", "npm", ("mysql2", "mysql")),
    TechnologySignature("MySQL (client)", "database", "go", ("github.com/go-sql-driver/mysql",)),
    TechnologySignature("SQLite (client)", "database", "npm", ("better-sqlite3", "sqlite3")),
    TechnologySignature("SQLite (client)", "database", "cargo", ("rusqlite",)),
    TechnologySignature("SQLite (client)", "database", "go", ("github.com/mattn/go-sqlite3",)),
)

COMPOSE_IMAGE_TECHNOLOGIES = {
    "postgres": ("PostgreSQL (container image)", "database"),
    "postgresql": ("PostgreSQL (container image)", "database"),
    "redis": ("Redis (container image)", "database"),
    "mysql": ("MySQL (container image)", "database"),
    "mariadb": ("MariaDB (container image)", "database"),
    "mongo": ("MongoDB (container image)", "database"),
    "mongodb": ("MongoDB (container image)", "database"),
    "minio": ("MinIO (container image)", "infrastructure"),
    "nginx": ("nginx (container image)", "infrastructure"),
    "rabbitmq": ("RabbitMQ (container image)", "infrastructure"),
    "kafka": ("Kafka (container image)", "infrastructure"),
    "localstack": ("LocalStack (container image)", "infrastructure"),
}

ECOSYSTEM_ALIASES = {
    "nuget": "nuget", "pip": "pip", "npm": "npm", "maven": "maven",
    "gradle": "gradle", "cargo": "cargo", "go": "go", "composer": "composer",
    "rubygems": "rubygems", "pub": "pub", "swiftpm": "swiftpm",
}


def _dependency_index(context: AnalysisContext) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for declaration in context.dependencies.declarations:
        ecosystem = ECOSYSTEM_ALIASES.get(declaration.ecosystem.casefold(), declaration.ecosystem.casefold())
        index.setdefault(ecosystem, set()).add(_norm(declaration.name))
    return index


def _file_index(context: AnalysisContext) -> tuple[dict[str, str], list[str]]:
    names: dict[str, str] = {}
    paths: list[str] = []
    for entry in context.workspace.files:
        paths.append(entry.path)
        names.setdefault(Path(entry.path).name.casefold(), entry.path)
    return names, paths


def _add(store: dict[str, FrameworkProfile | TechnologyProfile], name: str, category: str,
         ecosystem: str, confidence: Confidence, evidence: list[str],
         cls: type[FrameworkProfile] | type[TechnologyProfile]) -> None:
    key = name.casefold()
    existing = store.get(key)
    if existing is None:
        store[key] = cls(name=name, category=category, ecosystem=ecosystem,
                         confidence=confidence, evidence=sorted(set(evidence)))
        return
    existing.confidence = strongest(existing.confidence, confidence)
    existing.evidence = sorted(set(existing.evidence) | set(evidence))


def _special_detections(context: AnalysisContext,
                        frameworks: dict[str, FrameworkProfile | TechnologyProfile],
                        technologies: dict[str, FrameworkProfile | TechnologyProfile]) -> None:
    names, paths = _file_index(context)
    # Android
    manifest = names.get("androidmanifest.xml")
    if manifest:
        _add(frameworks, "Android platform", "framework", "android",
             Confidence.CONFIRMED, [f"AndroidManifest.xml: {manifest}"], FrameworkProfile)
    # Unity
    if "projectsettings/projectversion.txt" in {path.casefold() for path in paths} or (
            names.get("projectversion.txt") and any(path.casefold().startswith("assets/") for path in paths)):
        _add(frameworks, "Unity", "framework", "unity", Confidence.CONFIRMED,
             ["ProjectSettings/ProjectVersion.txt and Assets/ detected"], FrameworkProfile)
    # .NET project SDK / desktop UI frameworks
    for entry in context.workspace.files:
        if Path(entry.path).suffix.casefold() != ".csproj":
            continue
        text = context.read_text(entry.path, max_bytes=MAX_CONTENT_BYTES) or ""
        if 'Sdk="Microsoft.NET.Sdk.Web"' in text:
            _add(frameworks, "ASP.NET Core", "framework", "nuget", Confidence.CONFIRMED,
                 [f"csproj Web SDK: {entry.path}"], FrameworkProfile)
        if "<UseWPF>true</UseWPF>" in text:
            _add(frameworks, "WPF", "framework", "nuget", Confidence.CONFIRMED,
                 [f"UseWPF: {entry.path}"], FrameworkProfile)
        if "<UseWindowsForms>true</UseWindowsForms>" in text:
            _add(frameworks, "WinForms", "framework", "nuget", Confidence.CONFIRMED,
                 [f"UseWindowsForms: {entry.path}"], FrameworkProfile)
        if "<TargetFramework>" in text:
            _add(technologies, ".NET", "runtime", "nuget", Confidence.CONFIRMED,
                 [f"TargetFramework: {entry.path}"], TechnologyProfile)
    # Gradle Android plugin
    for entry in context.workspace.files:
        if Path(entry.path).name.casefold() not in {"build.gradle", "build.gradle.kts"}:
            continue
        text = context.read_text(entry.path, max_bytes=MAX_CONTENT_BYTES) or ""
        if "com.android.application" in text or "com.android.library" in text:
            _add(frameworks, "Android platform", "framework", "android", Confidence.CONFIRMED,
                 [f"Gradle Android plugin: {entry.path}"], FrameworkProfile)
        if "org.jetbrains.kotlin" in text:
            _add(technologies, "Kotlin JVM tooling", "tooling", "gradle", Confidence.CONFIRMED,
                 [f"Kotlin Gradle plugin: {entry.path}"], TechnologyProfile)
    # Flutter
    pubspec = names.get("pubspec.yaml")
    if pubspec:
        text = context.read_text(pubspec, max_bytes=MAX_CONTENT_BYTES) or ""
        if re.search(r"(?m)^\s+sdk:\s*flutter\s*$", text) or re.search(r"(?m)^\s+flutter:\s*$", text):
            _add(frameworks, "Flutter", "framework", "pub", Confidence.CONFIRMED,
                 [f"pubspec.yaml Flutter SDK declaration: {pubspec}"], FrameworkProfile)
    # Compose images and service count
    service_images: list[str] = []
    compose_files = [entry.path for entry in context.workspace.files
                     if Path(entry.path).name.casefold() in
                     {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}]
    for rel in compose_files[:20]:
        text = context.read_text(rel, max_bytes=MAX_CONTENT_BYTES) or ""
        images = re.findall(r"(?mi)^\s*image:\s*['\"]?([A-Za-z0-9._/-]+)", text)
        service_images.extend(images)
        for image in images:
            base = image.rsplit("/", 1)[-1].split(":", 1)[0].casefold()
            match = COMPOSE_IMAGE_TECHNOLOGIES.get(base)
            if match:
                name, category = match
                _add(technologies, name, category, "container", Confidence.LIKELY,
                     [f"Docker Compose image declaration: {image} ({rel})"], TechnologyProfile)
    if service_images:
        context.metrics["compose_service_images"] = len(service_images)
    # Kubernetes manifests (content-based; workflows do not contain apiVersion+kind)
    k8s_files: list[str] = []
    scanned = 0
    for entry in context.workspace.files:
        if scanned >= MAX_CONTENT_FILES:
            break
        if Path(entry.path).suffix.casefold() not in {".yaml", ".yml"}:
            continue
        scanned += 1
        text = context.read_text(entry.path, max_bytes=MAX_CONTENT_BYTES) or ""
        head = text[:8_192]
        if re.search(r"(?m)^\s*apiVersion\s*:", head) and re.search(r"(?m)^\s*kind\s*:", head):
            k8s_files.append(entry.path)
    if k8s_files:
        _add(technologies, "Kubernetes manifests", "infrastructure", "kubernetes",
             Confidence.CONFIRMED, [f"apiVersion/kind manifest: {path}" for path in k8s_files[:5]],
             TechnologyProfile)
    context.metrics["kubernetes_manifest_files"] = k8s_files


def detect_profiles(context: AnalysisContext) -> tuple[list[FrameworkProfile], list[TechnologyProfile]]:
    dependencies = _dependency_index(context)
    names, paths = _file_index(context)
    frameworks: dict[str, FrameworkProfile | TechnologyProfile] = {}
    technologies: dict[str, FrameworkProfile | TechnologyProfile] = {}

    for signature in SIGNATURES:
        evidence: list[str] = []
        confidence = Confidence.UNKNOWN
        ecosystem_dependencies = dependencies.get(signature.ecosystem, set())
        for package in signature.packages:
            if _norm(package) in ecosystem_dependencies:
                evidence.append(f"dependency declaration: {package}")
                confidence = Confidence.CONFIRMED
        for prefix in signature.prefixes:
            for package in ecosystem_dependencies:
                if package.startswith(_norm(prefix)):
                    evidence.append(f"dependency declaration: {package}")
                    confidence = Confidence.CONFIRMED
                    break
        for filename in signature.files:
            rel = names.get(filename.casefold())
            if rel:
                evidence.append(f"configuration file: {rel}")
                confidence = strongest(confidence, signature.file_confidence)
        for pattern in signature.globs:
            candidates = [pattern]
            if pattern.startswith("**/"):
                candidates.append(pattern[3:])
            match = next((path for path in paths
                          if any(fnmatch.fnmatchcase(path.casefold(), item.casefold())
                                 for item in candidates)), None)
            if match:
                evidence.append(f"file: {match}")
                confidence = strongest(confidence, signature.file_confidence)
        if confidence is not Confidence.UNKNOWN:
            store = frameworks if signature.category == "framework" else technologies
            cls = FrameworkProfile if signature.category == "framework" else TechnologyProfile
            _add(store, signature.name, signature.category, signature.ecosystem,
                 confidence, evidence, cls)

    _special_detections(context, frameworks, technologies)
    framework_profiles = sorted(
        (item for item in frameworks.values() if isinstance(item, FrameworkProfile)),
        key=lambda item: item.name.casefold())
    technology_profiles = sorted(
        (item for item in technologies.values() if isinstance(item, TechnologyProfile)),
        key=lambda item: item.name.casefold())
    return framework_profiles, technology_profiles


def dependency_names(context: AnalysisContext) -> Iterable[str]:
    """Expose normalized declared names for analyzers that recommend tooling."""
    for declaration in context.dependencies.declarations:
        yield _norm(declaration.name)
