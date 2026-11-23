"""Build and test discovery adapters.

This module discovers local toolchains and proposes commands for review.  It
does not execute builds, tests, package managers, wrappers, or project scripts.
Execution belongs to a separately reviewed isolated worker; no unsandboxed
fallback is provided here.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    purpose: str
    check_names: tuple[str, ...] = ()


@dataclass(slots=True)
class BuildAdapter:
    adapter_id: str
    ecosystem: str
    manifest: str
    tools: list[ToolSpec]
    planned_build: list[str]
    planned_test: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self, workspace: Workspace) -> dict[str, Any]:
        tools = []
        for spec in self.tools:
            found = next((name for name in (spec.name, *spec.check_names) if shutil.which(name)), None)
            tools.append({
                "name": spec.name,
                "purpose": spec.purpose,
                "available": found is not None,
                "executable": found,
                "version": None,
                "version_check": "not_executed",
            })
        available = all(item["available"] for item in tools) if tools else False
        return {
            "adapter_id": self.adapter_id,
            "ecosystem": self.ecosystem,
            "manifest_present": workspace.exists(self.manifest),
            "tools": tools,
            "toolchain_available": available,
            "planned_build": list(self.planned_build),
            "planned_test": list(self.planned_test),
            "execution": "not_executed",
            "execution_status": "not_configured",
            "execution_note": "Commands are discovery proposals only. OGX did not execute project code or install dependencies.",
            "notes": list(self.notes),
        }


ADAPTERS: tuple[BuildAdapter, ...] = (
    BuildAdapter("python-pip", "Python", "requirements.txt",
                 [ToolSpec("python", "Python runtime"), ToolSpec("pip", "pip package manager"),
                  ToolSpec("pytest", "pytest test runner")],
                 ["python -m pip install -r requirements.txt", "python -m compileall -q ."],
                 ["python -m pytest"],
                 ["Dependency installation requires network/package index access and is never automatic."]),
    BuildAdapter("python-poetry", "Python", "pyproject.toml",
                 [ToolSpec("python", "Python runtime"), ToolSpec("poetry", "Poetry package manager")],
                 ["poetry install", "poetry build"], ["poetry run pytest"],
                 ["Detected as a proposal even when the manifest is not Poetry-specific; verify the build backend."]),
    BuildAdapter("python-uv", "Python", "uv.lock",
                 [ToolSpec("uv", "uv package manager"), ToolSpec("python", "Python runtime")],
                 ["uv sync", "uv build"], ["uv run pytest"]),
    BuildAdapter("javascript-npm", "JavaScript/TypeScript", "package.json",
                 [ToolSpec("node", "Node.js runtime"), ToolSpec("npm", "npm package manager")],
                 ["npm ci", "npm run build"], ["npm test"],
                 ["`npm ci` requires a compatible lockfile and network unless dependencies are already cached."]),
    BuildAdapter("javascript-pnpm", "JavaScript/TypeScript", "pnpm-lock.yaml",
                 [ToolSpec("pnpm", "pnpm package manager"), ToolSpec("node", "Node.js runtime")],
                 ["pnpm install --frozen-lockfile", "pnpm run build"], ["pnpm test"]),
    BuildAdapter("javascript-yarn", "JavaScript/TypeScript", "yarn.lock",
                 [ToolSpec("yarn", "Yarn package manager"), ToolSpec("node", "Node.js runtime")],
                 ["yarn install --immutable", "yarn build"], ["yarn test"]),
    BuildAdapter("javascript-bun", "JavaScript/TypeScript", "bun.lock",
                 [ToolSpec("bun", "Bun runtime")],
                 ["bun install --frozen-lockfile", "bun run build"], ["bun test"]),
    BuildAdapter("jvm-gradle", "JVM/Android", "build.gradle",
                 [ToolSpec("java", "JVM runtime"), ToolSpec("gradle", "Gradle build tool")],
                 ["gradle build"], ["gradle test"]),
    BuildAdapter("jvm-gradle-kts", "JVM/Android", "build.gradle.kts",
                 [ToolSpec("java", "JVM runtime"), ToolSpec("gradle", "Gradle build tool")],
                 ["gradle build"], ["gradle test"]),
    BuildAdapter("jvm-maven", "JVM", "pom.xml",
                 [ToolSpec("java", "JVM runtime"), ToolSpec("mvn", "Maven build tool")],
                 ["mvn -B verify"], ["mvn -B test"]),
    BuildAdapter("android-gradle", "Android", "settings.gradle",
                 [ToolSpec("java", "JDK runtime"), ToolSpec("gradle", "Gradle build tool"),
                  ToolSpec("adb", "Android Debug Bridge (optional device operations)")],
                 ["gradlew.bat assembleDebug", "gradlew assembleDebug"], ["gradlew.bat testDebugUnitTest", "gradlew testDebugUnitTest"],
                 ["The Android SDK path must be configured; OGX never runs the Gradle wrapper automatically.",
                  "Device/emulator tests and APK installation are not attempted."]),
    BuildAdapter("cpp-cmake", "C/C++", "CMakeLists.txt",
                 [ToolSpec("cmake", "CMake build system"), ToolSpec("ninja", "Ninja generator"),
                  ToolSpec("make", "Make build tool")],
                 ["cmake -S . -B build", "cmake --build build"], ["ctest --test-dir build"]),
    BuildAdapter("cpp-bazel", "C/C++", "WORKSPACE",
                 [ToolSpec("bazel", "Bazel build system")],
                 ["bazel build //..."], ["bazel test //..."]),
    BuildAdapter("dotnet", "C#/.NET", "Directory.Build.props",
                 [ToolSpec("dotnet", ".NET SDK")],
                 ["dotnet build"], ["dotnet test"],
                 ["A project may also be detected through .csproj/.sln files."]),
    BuildAdapter("go", "Go", "go.mod",
                 [ToolSpec("go", "Go toolchain")],
                 ["go build ./..."], ["go test ./..."]),
    BuildAdapter("rust", "Rust", "Cargo.toml",
                 [ToolSpec("cargo", "Cargo package manager"), ToolSpec("rustc", "Rust compiler")],
                 ["cargo build --locked"], ["cargo test --locked"]),
    BuildAdapter("ruby", "Ruby", "Gemfile",
                 [ToolSpec("ruby", "Ruby runtime"), ToolSpec("bundle", "Bundler")],
                 ["bundle install", "bundle exec rake build"], ["bundle exec rspec"]),
    BuildAdapter("php", "PHP", "composer.json",
                 [ToolSpec("php", "PHP runtime"), ToolSpec("composer", "Composer package manager")],
                 ["composer install --no-interaction", "composer run-script build"], ["composer test"]),
    BuildAdapter("dart", "Dart/Flutter", "pubspec.yaml",
                 [ToolSpec("dart", "Dart SDK"), ToolSpec("flutter", "Flutter SDK")],
                 ["dart pub get", "flutter pub get"], ["dart test", "flutter test"]),
    BuildAdapter("swift", "Swift", "Package.swift",
                 [ToolSpec("swift", "Swift toolchain")],
                 ["swift build"], ["swift test"]),
    BuildAdapter("haskell", "Haskell", "stack.yaml",
                 [ToolSpec("stack", "Haskell Stack"), ToolSpec("ghc", "Glasgow Haskell Compiler")],
                 ["stack build"], ["stack test"]),
    BuildAdapter("elixir", "Elixir", "mix.exs",
                 [ToolSpec("mix", "Elixir Mix"), ToolSpec("elixir", "Elixir runtime")],
                 ["mix deps.get", "mix compile"], ["mix test"]),
    BuildAdapter("erlang", "Erlang", "rebar.config",
                 [ToolSpec("rebar3", "Rebar3 build tool"), ToolSpec("erl", "Erlang runtime")],
                 ["rebar3 compile"], ["rebar3 eunit"]),
    BuildAdapter("scala", "Scala", "build.sbt",
                 [ToolSpec("sbt", "sbt build tool"), ToolSpec("java", "JVM runtime")],
                 ["sbt compile"], ["sbt test"]),
    BuildAdapter("solidity", "Solidity", "foundry.toml",
                 [ToolSpec("forge", "Foundry build/test tool")],
                 ["forge build"], ["forge test"]),
    BuildAdapter("terraform", "Terraform", "main.tf",
                 [ToolSpec("terraform", "Terraform CLI"), ToolSpec("tflint", "Terraform linter")],
                 ["terraform init", "terraform validate"], ["terraform test"],
                 ["`terraform init` may download providers; it is never automatic."]),
    BuildAdapter("helm", "Helm", "Chart.yaml",
                 [ToolSpec("helm", "Helm CLI")],
                 ["helm lint .", "helm template ."], []),
)


TEST_PATTERNS: dict[str, dict[str, Any]] = {
    "Python": {"dirs": ["tests", "test"], "globs": ["test_*.py", "*_test.py", "conftest.py"],
               "frameworks": ["pytest", "unittest", "tox", "nox"]},
    "JavaScript/TypeScript": {"dirs": ["__tests__", "test", "tests", "cypress", "e2e"],
                              "globs": ["*.test.js", "*.test.ts", "*.spec.js", "*.spec.ts", "*.test.jsx", "*.test.tsx"],
                              "frameworks": ["jest", "vitest", "mocha", "playwright", "cypress"]},
    "Java": {"dirs": ["src/test", "src/androidTest"], "globs": ["*Test.java", "*Tests.java", "*IT.java"],
             "frameworks": ["JUnit"]},
    "Kotlin": {"dirs": ["src/test", "src/androidTest"], "globs": ["*Test.kt", "*Tests.kt"],
               "frameworks": ["JUnit", "Kotlin Test"]},
    "Go": {"dirs": [], "globs": ["*_test.go"], "frameworks": ["go test"]},
    "Rust": {"dirs": ["tests"], "globs": ["*_test.rs"], "frameworks": ["cargo test", "libtest"]},
    "C#": {"dirs": ["tests", "test"], "globs": ["*Tests.cs", "*Test.cs"], "frameworks": ["xUnit", "NUnit", "MSTest"]},
    "Ruby": {"dirs": ["spec", "test"], "globs": ["*_spec.rb", "test_*.rb"], "frameworks": ["RSpec", "Minitest"]},
    "PHP": {"dirs": ["tests", "test"], "globs": ["*Test.php"], "frameworks": ["PHPUnit", "Pest"]},
    "Swift": {"dirs": ["Tests", "tests"], "globs": ["*Tests.swift"], "frameworks": ["XCTest", "Swift Testing"]},
    "Dart": {"dirs": ["test", "integration_test"], "globs": ["*_test.dart"], "frameworks": ["package:test", "flutter_test"]},
    "Scala": {"dirs": ["src/test"], "globs": ["*Spec.scala", "*Test.scala"], "frameworks": ["ScalaTest", "MUnit"]},
    "Elixir": {"dirs": ["test"], "globs": ["*_test.exs"], "frameworks": ["ExUnit"]},
    "Erlang": {"dirs": ["test"], "globs": ["*_tests.erl"], "frameworks": ["EUnit", "Common Test"]},
    "Haskell": {"dirs": ["test", "tests"], "globs": ["*Spec.hs", "*Test.hs"], "frameworks": ["Hspec", "Tasty"]},
}


def discover_builds(workspace: Workspace) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for adapter in ADAPTERS:
        if not workspace.exists(adapter.manifest):
            if adapter.adapter_id == "dotnet":
                if not any(entry.path.lower().endswith((".csproj", ".sln")) for entry in workspace.files):
                    continue
            else:
                continue
        if adapter.adapter_id in seen:
            continue
        seen.add(adapter.adapter_id)
        found.append(adapter.to_dict(workspace))
    return found


def discover_tests(workspace: Workspace, languages: list[str]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    total = 0
    for language in languages:
        spec = TEST_PATTERNS.get(language)
        if not spec:
            results[language] = {"discovered": False, "status": "unsupported",
                                 "detail": "No test-discovery pattern is registered for this language."}
            continue
        dirs = [name for name in spec["dirs"] if (workspace.root / name).is_dir()]
        glob_matches: list[str] = []
        for pattern in spec["globs"]:
            glob_matches.extend(entry.path for entry in workspace.glob(f"**/{pattern}", limit=1000))
            glob_matches.extend(entry.path for entry in workspace.glob(pattern, limit=1000))
        glob_matches = sorted(set(glob_matches))
        count = len(glob_matches)
        total += count
        detected = bool(dirs or count)
        results[language] = {
            "discovered": detected,
            "status": "partial" if detected else "not_configured",
            "test_directories": dirs,
            "test_file_count": count,
            "sample_test_files": glob_matches[:20],
            "candidate_frameworks": list(spec["frameworks"]),
            "executed": False,
            "execution_status": "not_configured",
            "detail": ("Test files/directories detected; no test was executed by OGX."
                       if detected else "No conventional test files or directories were detected."),
        }
    return {"languages": results, "total_test_files": total, "executed": False,
            "execution_status": "not_configured",
            "note": "Test execution requires an approved isolated worker; this discovery is static."}


def tool_availability(names: list[str]) -> dict[str, Any]:
    return {name: {"available": shutil.which(name) is not None, "path": shutil.which(name)}
            for name in names}


def android_environment() -> dict[str, Any]:
    sdk_candidates = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(variable)
        if value:
            sdk_candidates.append({"variable": variable, "path": value, "exists": Path(value).expanduser().is_dir()})
    default_sdk = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Android" / "Sdk"
    sdk_present = any(item["exists"] for item in sdk_candidates) or default_sdk.is_dir()
    tools = tool_availability(["java", "javac", "gradle", "adb", "sdkmanager", "emulator", "kotlinc"])
    wrapper = None
    return {
        "sdk_variables": sdk_candidates,
        "default_sdk_path": str(default_sdk),
        "default_sdk_present": default_sdk.is_dir(),
        "sdk_detected": sdk_present,
        "tools": tools,
        "jdk_present": tools["java"]["available"],
        "gradle_present": tools["gradle"]["available"],
        "adb_present": tools["adb"]["available"],
        "wrapper": wrapper,
        "device_operations": "not_attempted",
        "build_execution": "not_configured",
        "limitations": [
            "SDK path detection is filesystem-based; it does not prove a complete or licensed SDK installation.",
            "No Gradle wrapper, build, emulator, device or APK installation was executed.",
            "Android build execution requires a separately approved isolated worker/SDK configuration.",
        ],
    }
