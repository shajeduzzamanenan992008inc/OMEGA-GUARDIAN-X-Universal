"""Testing and quality infrastructure intelligence. Nothing is executed."""

from __future__ import annotations

import re
from pathlib import Path

from ...engines.build import discover_tests
from ..models import AnalysisEvidence, Confidence, FrameworkProfile, TestingProfile
from ..options import AnalysisContext

TEST_CONFIG_NAMES = {
    "pytest.ini", "tox.ini", "noxfile.py", "conftest.py", "phpunit.xml",
    ".rspec", "jest.config.js", "jest.config.ts", "jest.config.mjs",
    "vitest.config.js", "vitest.config.ts", "playwright.config.ts",
    "playwright.config.js", "cypress.config.ts", "cypress.config.js",
    ".mocharc.json", ".mocharc.js", ".mocharc.yml",
}

COVERAGE_NAMES = {
    ".coveragerc", "coverage.xml", "codecov.yml", ".codecov.yml",
    "tarpaulin.toml", "jacoco.xml",
}

LINT_NAMES = {
    ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json",
    "eslint.config.js", "eslint.config.mjs", ".prettierrc", ".prettierrc.json",
    "ruff.toml", ".ruff.toml", ".flake8", ".pylintrc", ".rubocop.yml",
    "phpstan.neon", ".golangci.yml", ".golangci.yaml", "clippy.toml", "detekt.yml",
    "sonar-project.properties", ".stylelintrc", ".stylelintrc.json",
}

TYPE_NAMES = {
    "mypy.ini", ".mypy.ini", "pyrightconfig.json", "tsconfig.json",
    "jsconfig.json",
}

TEST_FRAMEWORK_NAMES = {
    "pytest", "unittest", "jest", "vitest", "mocha", "playwright", "cypress",
    "junit", "junit-jupiter", "testng", "nunit", "xunit", "mstest", "rspec",
    "phpunit", "pest", "go test", "cargo test", "swift testing", "xctest",
}

TEST_COMMAND = re.compile(
    r"(?i)\b(?:pytest|py\.test|tox|nox|unittest|npm\s+(?:run\s+)?test|"
    r"pnpm\s+(?:run\s+)?test|yarn\s+test|bun\s+test|jest|vitest|mocha|"
    r"playwright\s+test|cypress\s+run|mvn\b[^\n]*\btest\b|gradle(?:w)?\b[^\n]*\btest\b|"
    r"go\s+test|cargo\s+test|dotnet\s+test|ctest|swift\s+test|flutter\s+test|"
    r"dart\s+test|phpunit|rspec)\b"
)

COMPILED_LANGUAGES = {
    "Java", "Kotlin", "C", "C++", "C#", "Go", "Rust", "Swift", "Dart",
    "Scala", "Haskell", "Elixir", "Erlang", "Zig",
}

LANGUAGE_KEY_MAP = {
    "javascript": "JavaScript/TypeScript",
    "typescript": "JavaScript/TypeScript",
}


def _names(context: AnalysisContext) -> tuple[dict[str, str], set[str]]:
    exact = {Path(entry.path).name.casefold(): entry.path for entry in context.workspace.files}
    suffixes = {Path(entry.path).suffix.casefold() for entry in context.workspace.files}
    return exact, suffixes


def analyze_testing(context: AnalysisContext) -> TestingProfile:
    profile = TestingProfile(executed=False)
    exact, _ = _names(context)
    language_labels: list[str] = []
    for language in context.languages:
        label = LANGUAGE_KEY_MAP.get(language.key, language.name)
        if label not in language_labels:
            language_labels.append(label)
    if "JavaScript/TypeScript" in language_labels:
        language_labels = [label for label in language_labels if label not in {"JavaScript", "TypeScript"}]
    result = discover_tests(context.workspace, language_labels)

    total = 0
    dirs: set[str] = set()
    samples: set[str] = set()
    for payload in result.get("languages", {}).values():
        if not isinstance(payload, dict):
            continue
        total += int(payload.get("test_file_count", 0) or 0)
        dirs.update(payload.get("test_directories", []) or [])
        samples.update(payload.get("sample_test_files", []) or [])
    profile.test_file_count = total
    profile.test_directories = sorted(
        path for path in dirs if not (context.root / path).is_symlink())[:50]
    profile.sample_test_files = sorted(samples)[:40]

    framework_evidence: dict[str, list[str]] = {}
    for framework in context.frameworks + context.technologies:
        if framework.category.casefold() == "testing":
            framework_evidence.setdefault(framework.name, []).extend(framework.evidence)
    dependency_names = {declaration.name.casefold().replace("_", "-")
                        for declaration in context.dependencies.declarations}
    for name in TEST_FRAMEWORK_NAMES:
        if name in dependency_names:
            framework_evidence.setdefault(name.title(), []).append(f"dependency declaration: {name}")
    config_files = sorted(set(
        rel for name, rel in exact.items() if name in TEST_CONFIG_NAMES))
    for name, rel in exact.items():
        if name.startswith("jest.config") or name.startswith("vitest.config") or name.startswith("playwright.config"):
            config_files.append(rel)
    profile.config_files = sorted(set(config_files))[:30]
    if not framework_evidence and profile.test_file_count:
        framework_evidence["Unspecified test framework"] = ["test files detected without a recognized test framework declaration"]
    profile.frameworks = [
        FrameworkProfile(name=name, category="testing", ecosystem="declared",
                         confidence=Confidence.CONFIRMED if evidence else Confidence.LIKELY,
                         evidence=sorted(set(evidence)))
        for name, evidence in sorted(framework_evidence.items())
    ]
    profile.evidence = ([f"{profile.test_file_count} test file(s) discovered"]
                        + [f"config: {path}" for path in profile.config_files[:5]]
                        + [f"framework: {item.name}" for item in profile.frameworks[:5]])

    coverage_evidence: list[str] = []
    for name, rel in exact.items():
        if name in COVERAGE_NAMES:
            coverage_evidence.append(f"coverage configuration: {rel}")
    pyproject = exact.get("pyproject.toml")
    if pyproject:
        text = context.read_text(pyproject, max_bytes=512_000) or ""
        if "[tool.coverage" in text:
            coverage_evidence.append(f"coverage configuration: {pyproject} [tool.coverage]")
    if dependency_names & {"coverage", "pytest-cov", "nyc", "c8"}:
        coverage_evidence.append("coverage tool declared as a dependency")
    profile.coverage_evidence = sorted(set(coverage_evidence))[:20]
    profile.coverage_configured = bool(coverage_evidence) if (coverage_evidence or profile.test_file_count) else None

    profile.static_analysis = sorted(set(
        [rel for name, rel in exact.items() if name in LINT_NAMES]
        + [name for name in ("ruff", "flake8", "pylint", "eslint", "stylelint", "rubocop", "phpstan", "golangci-lint")
           if name in dependency_names]
    ))[:30]
    type_evidence: list[str] = [rel for name, rel in exact.items() if name in TYPE_NAMES]
    for name in ("mypy", "pyright", "typescript"):
        if name in dependency_names:
            type_evidence.append(f"type checker dependency: {name}")
    for language in context.languages:
        if language.name in COMPILED_LANGUAGES and language.role in {"primary", "secondary"}:
            type_evidence.append(f"compiler type checking: {language.name}")
    profile.type_checking = sorted(set(type_evidence))[:30]

    ci_files = sorted(set(context.profile.ci_files) | {
        entry.path for entry in context.workspace.files
        if entry.path.startswith(".github/workflows/") or Path(entry.path).name.casefold() in {
            ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile", "bitbucket-pipelines.yml"}
    })
    ci_test_workflows: list[str] = []
    for rel in ci_files[:80]:
        text = context.read_text(rel, max_bytes=512_000) or ""
        if TEST_COMMAND.search(text):
            ci_test_workflows.append(rel)
    profile.ci_test_workflows = ci_test_workflows[:20]

    if profile.test_file_count or profile.config_files:
        profile.confidence = Confidence.CONFIRMED
    elif profile.frameworks:
        profile.confidence = Confidence.LIKELY
    elif profile.test_directories:
        profile.confidence = Confidence.POSSIBLE
    else:
        profile.confidence = Confidence.UNKNOWN

    testing_evidence = [
        AnalysisEvidence(kind="test-file", location=path,
                         detail="discovered conventional test file")
        for path in profile.sample_test_files[:6]
    ]
    testing_evidence.extend(
        AnalysisEvidence(kind="test-config", location=path,
                         detail="test configuration")
        for path in profile.config_files[:6])
    testing_evidence.extend(
        AnalysisEvidence(kind="test-framework", location=item.name,
                         detail="; ".join(item.evidence[:3]))
        for item in profile.frameworks[:6])
    if profile.test_file_count or profile.frameworks:
        context.finding(
            "OGX-INTEL-TESTING", "Testing infrastructure detected", "testing",
            profile.confidence, "Static test-file, framework and configuration discovery",
            evidence=testing_evidence,
            limitations=["No test was executed; success and coverage are unknown."],
        )
    else:
        context.finding(
            "OGX-INTEL-TESTING-MISSING", "No conventional test infrastructure detected", "testing",
            Confidence.LIKELY, "Static test-file and configuration discovery",
            evidence=[AnalysisEvidence(kind="search", location=".",
                                       detail="no conventional test file or test configuration was indexed")],
            limitations=["Absence is bounded to the indexed project scope."],
        )
        if context.languages and any(item.role in {"primary", "secondary"} for item in context.languages):
            context.recommend(
                "testing", "Add deterministic tests for the primary code paths",
                "No conventional test files or test configuration were found in the indexed scope.",
                "medium")

    profile.limitations = [
        "Test execution was not performed; discovered file counts are not test counts or pass/fail results.",
        "Coverage configuration presence does not mean coverage was measured.",
        "Static analysis and type-checker configuration presence does not mean they pass or are enforced.",
        "Test discovery is conventional-path and language-pattern based; custom test layouts may be missed.",
    ]
    for limitation in profile.limitations:
        context.add_limitation("testing", limitation)
    return profile
