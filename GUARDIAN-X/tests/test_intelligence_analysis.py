from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ogx.intelligence import AnalysisOptions, ProjectIntelligenceEngine


def write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class AnalysisTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def analyze(self, **options):
        return ProjectIntelligenceEngine().analyze(
            self.root, AnalysisOptions(registry_lookup=False, **options))

    @staticmethod
    def names(items):
        return {item["name"] for item in items}

    def language(self, data, name):
        return next(item for item in data["languages"] if item["name"] == name)

    def architecture(self, data, name):
        return next((item for item in data["architecture"] if item["name"] == name), None)


class PythonProjectTests(AnalysisTestCase):
    def _fixture(self):
        write(self.root, "pyproject.toml", """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "demo"
version = "0.1.0"
dependencies = ["fastapi>=0.110", "sqlalchemy>=2.0"]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.4"]

[project.scripts]
demo = "demo.cli:main"

[tool.coverage.run]
source = ["demo"]
""")
        write(self.root, "uv.lock", "version = 1\n")
        write(self.root, "README.md", "# Demo\n\nUsage.\n")
        write(self.root, "CONTRIBUTING.md", "# Contributing\n")
        write(self.root, "CHANGELOG.md", "# Changelog\n")
        write(self.root, "LICENSE", "MIT\n")
        write(self.root, "SECURITY.md", "# Security\n")
        write(self.root, "docs/architecture.md", "# Architecture\n")
        write(self.root, "docs/api/reference.md", "# API\n")
        write(self.root, "ruff.toml", "line-length = 100\n")
        write(self.root, "mypy.ini", "[mypy]\nstrict = True\n")
        write(self.root, "Makefile", "build:\n\tpython -m build\n")
        write(self.root, "src/demo/__init__.py", "")
        write(self.root, "src/demo/cli.py", """import fastapi

def main():
    return 0

if __name__ == "__main__":
    main()
""")
        write(self.root, "tests/test_cli.py", "def test_ok():\n    assert True\n")
        write(self.root, ".github/workflows/ci.yml",
              "name: ci\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
              "    steps:\n      - run: pytest\n      - run: npm run build\n")
        write(self.root, ".env", "SECRET_CANARY=canaryValue123\n")
        write(self.root, ".env.example", "SECRET_CANARY=\n")

    def test_python_api_cli_project(self):
        self._fixture()
        data = self.analyze().to_dict()
        self.assertEqual(data["errors"], [])
        python = self.language(data, "Python")
        self.assertEqual(python["role"], "primary")
        self.assertEqual(python["confidence"], "confirmed")
        self.assertTrue(any("file(s)" in item for item in python["evidence"]))

        frameworks = self.names(data["frameworks"])
        technologies = self.names(data["technologies"])
        self.assertIn("FastAPI", frameworks)
        self.assertIn("SQLAlchemy", technologies)
        self.assertIn("pytest", technologies)

        declarations = {item["name"]: item for item in data["dependencies"]["declarations"]}
        self.assertEqual(declarations["fastapi"]["scope"], "runtime")
        self.assertEqual(declarations["pytest"]["scope"], "optional")
        self.assertEqual(declarations["pytest"]["group"], "dev")
        self.assertEqual(declarations["ruff"]["scope"], "optional")
        self.assertEqual({item["name"] for item in data["dependencies"]["package_managers"]}, {"uv"})
        self.assertIn("uv.lock", data["dependencies"]["lockfiles"])

        entry_kinds = {item["kind"] for item in data["entry_points"]}
        self.assertIn("python-scripts", entry_kinds)
        self.assertIn("python-candidate", entry_kinds)
        cli = self.architecture(data, "cli application")
        self.assertIsNotNone(cli)
        self.assertEqual(cli["verdict"], "confirmed")
        api = self.architecture(data, "api service")
        self.assertEqual(api["verdict"], "confirmed")
        self.assertIn(data["classification"]["kind"], {"cli application", "api service"})

        testing = data["testing"]
        self.assertFalse(testing["executed"])
        self.assertGreaterEqual(testing["test_file_count"], 1)
        self.assertIn("pytest", self.names(testing["frameworks"]))
        self.assertTrue(testing["coverage_configured"])
        self.assertIn(".github/workflows/ci.yml", testing["ci_test_workflows"])
        self.assertTrue(any(item.endswith("ruff.toml") for item in testing["static_analysis"]))
        self.assertTrue(any(item.endswith("mypy.ini") for item in testing["type_checking"]))

        documentation = data["documentation"]
        self.assertEqual(
            {item["item"] for item in documentation["indicators"] if item["present"]},
            {"README", "Documentation directory", "CONTRIBUTING guide", "CHANGELOG",
             "License", "Security policy", "Architecture documentation", "API documentation"},
        )
        self.assertTrue(documentation["architecture_documentation"])
        self.assertTrue(documentation["api_documentation"])

        self.assertIn("sensitive", {item["category"] for item in data["configuration"]["sensitive_candidates"]})
        self.assertIn(".env.example", data["configuration"]["categories"].get("environment", []))
        self.assertIn(".env", {item["path"] for item in data["configuration"]["sensitive_candidates"]})

        health = data["health"]
        self.assertIsNotNone(health["score"])
        self.assertGreater(health["scored_dimensions"], 0)
        dimensions = {item["name"]: item for item in health["dimensions"]}
        self.assertIn(dimensions["documentation"]["status"], {"established", "partial"})
        self.assertEqual(dimensions["build system"]["status"], "established")
        self.assertEqual(dimensions["testing"]["status"], "established")
        self.assertEqual(dimensions["static analysis"]["status"], "established")

        for finding in data["findings"]:
            self.assertTrue(finding["finding_id"].startswith("OGX-INTEL-"))
            self.assertTrue(finding["method"])
            self.assertIn(finding["confidence"], {"confirmed", "likely", "possible", "unknown"})
            for evidence in finding["evidence"]:
                self.assertEqual(set(evidence), {"kind", "location", "detail"})
                self.assertTrue(evidence["location"])

        serialized = json.dumps(data)
        self.assertNotIn("canaryValue123", serialized)

    def test_python_report_does_not_expose_secret_values(self):
        self._fixture()
        report = self.analyze()
        serialized = json.dumps(report.to_dict())
        self.assertNotIn("canaryValue123", serialized)
        from ogx.intelligence.reports import render_markdown, render_terminal
        self.assertNotIn("canaryValue123", render_terminal(report))
        self.assertNotIn("canaryValue123", render_markdown(report))


class JavaScriptProjectTests(AnalysisTestCase):
    def test_typescript_react_project(self):
        write(self.root, "package.json", json.dumps({
            "name": "web",
            "scripts": {"build": "next build", "dev": "next dev"},
            "dependencies": {"react": "^18.2.0", "next": "^14.0.0"},
            "devDependencies": {
                "typescript": "^5.0.0", "vitest": "^1.0.0",
                "@playwright/test": "^1.40.0", "eslint": "^8.0.0",
            },
        }))
        write(self.root, "package-lock.json", "{}")
        write(self.root, "tsconfig.json", "{}")
        write(self.root, "next.config.js", "module.exports = {};\n")
        write(self.root, "app/page.tsx", "export default function Page() { return <div />; }\n")
        write(self.root, "components/Button.tsx", "export const Button = () => <button />;\n")
        write(self.root, "tests/Button.test.tsx", "import { test } from 'vitest';\ntest('x', () => {});\n")
        write(self.root, "vitest.config.ts", "export default {};\n")
        write(self.root, ".eslintrc.json", "{}")
        write(self.root, "README.md", "# Web\n")
        write(self.root, "docs/guide.md", "# Guide\n")

        data = self.analyze().to_dict()
        self.assertEqual(self.language(data, "TypeScript")["role"], "primary")
        self.assertEqual(self.language(data, "TypeScript")["confidence"], "confirmed")
        self.assertIn("React", self.names(data["frameworks"]))
        self.assertIn("Next.js", self.names(data["frameworks"]))
        self.assertIn("TypeScript", self.names(data["technologies"]))
        self.assertIn("Vitest", self.names(data["technologies"]))
        self.assertEqual(self.names(data["dependencies"]["package_managers"]), {"npm"})
        self.assertIn("package-lock.json", data["dependencies"]["lockfiles"])
        self.assertIn("package.json build script", self.names(data["build_systems"]))
        self.assertIn("javascript-script", {item["kind"] for item in data["entry_points"]})
        self.assertIn("tsconfig.json", data["testing"]["type_checking"])
        self.assertEqual(self.architecture(data, "web application")["verdict"], "likely")
        self.assertEqual(data["classification"]["kind"], "web application")
        self.assertFalse(data["testing"]["executed"])
        self.assertGreaterEqual(data["testing"]["test_file_count"], 1)


class CrossLanguageTests(AnalysisTestCase):
    def test_monorepo_keeps_multiple_languages_and_packages(self):
        write(self.root, "package.json", json.dumps({
            "private": True, "workspaces": ["apps/*", "services/*"]}))
        write(self.root, "README.md", "# Mono\n")
        write(self.root, "Makefile", "build:\n\techo build\n")
        write(self.root, "apps/web/package.json", json.dumps({
            "name": "web", "dependencies": {"react": "^18"}}))
        write(self.root, "apps/web/src/App.tsx", "export default function App() { return <div />; }\n")
        write(self.root, "services/api/pyproject.toml", """[project]
name = "api"
dependencies = ["fastapi>=0.110"]
""")
        write(self.root, "services/api/app/main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        write(self.root, "services/worker/go.mod",
              "module example.test/worker\n\ngo 1.22\n\nrequire github.com/gin-gonic/gin v1.9.1\n")
        write(self.root, "services/worker/main.go", "package main\nfunc main() {}\n")

        data = self.analyze().to_dict()
        languages = {item["name"]: item for item in data["languages"]}
        self.assertTrue({"Python", "TypeScript", "Go"}.issubset(languages))
        self.assertEqual(sum(item["role"] == "primary" for item in data["languages"]), 1)
        self.assertGreaterEqual(sum(item["role"] == "secondary" for item in data["languages"]), 2)
        self.assertIn("monorepo", {item["name"] for item in data["architecture"]})
        monorepo = self.architecture(data, "monorepo")
        self.assertEqual(monorepo["verdict"], "confirmed")
        self.assertIn("monorepo", data["classification"]["secondary_kinds"])
        packages = {item["path"] for item in data["structure"]["monorepo_packages"]}
        self.assertTrue({"apps/web", "services/api", "services/worker"}.issubset(packages))
        self.assertTrue(data["structure"]["workspace_indicators"])
        managers = self.names(data["dependencies"]["package_managers"])
        self.assertTrue({"npm", "pip", "Go Modules"}.issubset(managers))
        self.assertNotIn("Bun", managers)
        declarations = {item["name"]: item for item in data["dependencies"]["declarations"]}
        self.assertEqual(declarations["react"]["scope"], "runtime")
        self.assertEqual(declarations["fastapi"]["scope"], "runtime")
        self.assertEqual(declarations["github.com/gin-gonic/gin"]["scope"], "runtime")
        self.assertIn("FastAPI", self.names(data["frameworks"]))
        self.assertIn("Gin", self.names(data["frameworks"]))
        self.assertIn("React", self.names(data["frameworks"]))
        self.assertIn("Make", self.names(data["build_systems"]))
        self.assertEqual(data["errors"], [])

    def test_java_maven_project(self):
        write(self.root, "pom.xml", """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <version>3.2.0</version>
    </dependency>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.0</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>""")
        write(self.root, "src/main/java/com/example/Application.java",
              "package com.example;\npublic class Application { public static void main(String[] args) {} }\n")
        write(self.root, "src/test/java/com/example/ApplicationTest.java",
              "package com.example;\npublic class ApplicationTest { public void test() {} }\n")
        write(self.root, "README.md", "# Java\n")

        data = self.analyze().to_dict()
        self.assertEqual(self.language(data, "Java")["role"], "primary")
        self.assertIn("Spring Boot", self.names(data["frameworks"]))
        self.assertIn("JUnit", self.names(data["technologies"]))
        self.assertIn("Maven", self.names(data["build_systems"]))
        self.assertIn("Maven", self.names(data["dependencies"]["package_managers"]))
        self.assertIn("jvm-main", {item["kind"] for item in data["entry_points"]})
        self.assertEqual(data["testing"]["test_file_count"], 1)
        self.assertIn("JUnit", self.names(data["testing"]["frameworks"]))
        declarations = {item["name"]: item for item in data["dependencies"]["declarations"]}
        self.assertEqual(declarations["org.junit.jupiter:junit-jupiter"]["scope"], "development")
        self.assertEqual(self.architecture(data, "api service")["verdict"], "likely")
        self.assertFalse(data["testing"]["executed"])

    def test_rust_cargo_project(self):
        write(self.root, "Cargo.toml", """[package]
name = "demo"
version = "0.1.0"

[dependencies]
tokio = "1"
axum = "0.7"
""")
        write(self.root, "src/main.rs", "fn main() {}\n")
        write(self.root, "Cargo.lock", "version = 3\n")
        write(self.root, "README.md", "# Rust\n")

        data = self.analyze().to_dict()
        self.assertEqual(self.language(data, "Rust")["role"], "primary")
        self.assertIn("Axum", self.names(data["frameworks"]))
        self.assertIn("Tokio", self.names(data["technologies"]))
        self.assertIn("Cargo", self.names(data["build_systems"]))
        self.assertIn("Cargo", self.names(data["dependencies"]["package_managers"]))
        self.assertIn("rust-main", {item["kind"] for item in data["entry_points"]})
        self.assertEqual(self.architecture(data, "api service")["verdict"], "likely")

    def test_go_module_project(self):
        write(self.root, "go.mod", """module example.test/demo

go 1.22

require github.com/gin-gonic/gin v1.9.1
""")
        write(self.root, "main.go", "package main\nfunc main() {}\n")
        write(self.root, "go.sum", "")
        write(self.root, "README.md", "# Go\n")

        data = self.analyze().to_dict()
        self.assertEqual(self.language(data, "Go")["role"], "primary")
        self.assertIn("Gin", self.names(data["frameworks"]))
        self.assertIn("Go toolchain", self.names(data["build_systems"]))
        self.assertIn("Go Modules", self.names(data["dependencies"]["package_managers"]))
        self.assertIn("go-main", {item["kind"] for item in data["entry_points"]})
        self.assertIn("go.sum", data["dependencies"]["lockfiles"])
        self.assertEqual(self.architecture(data, "api service")["verdict"], "likely")

    def test_c_make_project(self):
        write(self.root, "CMakeLists.txt", "cmake_minimum_required(VERSION 3.20)\nproject(demo C)\n")
        write(self.root, "src/main.c", "int main(void) { return 0; }\n")
        write(self.root, "src/util.cpp", "int helper() { return 1; }\n")
        write(self.root, "include/util.h", "int helper(void);\n")
        write(self.root, "README.md", "# C\n")

        data = self.analyze().to_dict()
        self.assertEqual(self.language(data, "C")["role"], "primary")
        self.assertIn("C++", {item["name"] for item in data["languages"]})
        self.assertIn("CMake", self.names(data["build_systems"]))
        self.assertIn("native-main", {item["kind"] for item in data["entry_points"]})
        self.assertEqual(data["errors"], [])


class RobustnessTests(AnalysisTestCase):
    def test_malformed_manifests_are_reported_not_fatal(self):
        write(self.root, "package.json", "{ this is not json")
        write(self.root, "pyproject.toml", "[broken\n")
        write(self.root, "pom.xml", "<project>")
        write(self.root, "app.py", "print('ok')\n")
        write(self.root, "README.md", "# Demo\n")
        data = self.analyze().to_dict()
        self.assertEqual(data["errors"], [])
        statuses = {item["path"]: item["status"] for item in data["dependencies"]["manifests"]}
        self.assertEqual(statuses.get("package.json"), "parse_error")
        self.assertEqual(statuses.get("pyproject.toml"), "parse_error")
        self.assertEqual(statuses.get("pom.xml"), "parse_error")
        self.assertTrue(any(item["finding_id"] == "OGX-INTEL-DEP-PARSE"
                            for item in data["findings"]))
        self.assertTrue(any("Could not parse dependency manifest" in warning
                            for warning in data["warnings"]))

    def test_missing_manifests_and_unknown_language_are_graceful(self):
        write(self.root, "notes.txt", "hello\n")
        write(self.root, "data.unknown", "opaque\n")
        data = self.analyze().to_dict()
        self.assertEqual(data["languages"], [])
        self.assertEqual(data["classification"]["confidence"], "unknown")
        self.assertEqual(data["dependencies"]["declarations"], [])
        self.assertEqual(data["build_systems"], [])
        self.assertEqual(data["errors"], [])
        self.assertTrue(any("No supported source" in item["detail"] for item in data["limitations"]))

    def test_generated_and_vendored_files_are_not_counted_as_source(self):
        write(self.root, "app.py", "print('app')\n")
        write(self.root, "lib.py", "print('lib')\n")
        write(self.root, "node_modules/pkg/index.js", "module.exports = 1;\n")
        write(self.root, "vendor/lib.py", "print('vendor')\n")
        write(self.root, "dist/bundle.min.js", "console.log(1);\n")
        write(self.root, "generated/code.py", "print('generated')\n")
        data = self.analyze().to_dict()
        names = self.names(data["languages"])
        self.assertIn("Python", names)
        self.assertNotIn("JavaScript", names)
        self.assertEqual(self.language(data, "Python")["file_count"], 2)
        self.assertGreaterEqual(data["structure"]["generated_files"], 1)
        self.assertTrue(any("generated or vendored" in warning for warning in data["warnings"]))

    def test_large_project_is_bounded(self):
        for index in range(150):
            write(self.root, f"src/module_{index:03d}.py", f"VALUE = {index}\n")
        data = self.analyze(max_files=25).to_dict()
        self.assertEqual(data["project"]["file_count"], 25)
        self.assertTrue(any("File limit reached" in warning for warning in data["warnings"]))
        self.assertEqual(data["errors"], [])

    def test_permission_errors_are_counted_not_fatal(self):
        write(self.root, "blocked.py", "print('blocked')\n")
        write(self.root, "readable.py", "print('readable')\n")
        original_stat = Path.stat

        def selective_stat(path: Path, *args, **kwargs):
            if path.name == "blocked.py":
                raise PermissionError("synthetic denied read")
            return original_stat(path, *args, **kwargs)

        with patch("ogx.workspace.Path.stat", new=selective_stat):
            data = self.analyze().to_dict()
        self.assertEqual(data["errors"], [])
        tree = {item["path"] for item in data["structure"]["tree"]}
        self.assertNotIn("blocked.py", tree)
        self.assertIn("readable.py", tree)

    def test_file_and_tree_limits_are_explicit(self):
        for index in range(8):
            write(self.root, f"f{index}.py", f"VALUE = {index}\n")
        report = self.analyze(max_files=3, max_tree_entries=2)
        data = report.to_dict()
        self.assertEqual(data["project"]["file_count"], 3)
        self.assertTrue(any("File limit reached" in warning for warning in data["warnings"]))
        self.assertTrue(data["structure"]["tree_truncated"])
        self.assertLessEqual(len(data["structure"]["tree"]), 2)

    def test_dependency_limit_is_reported(self):
        write(self.root, "requirements.txt",
              "".join(f"package-{index}==1.{index}.0\n" for index in range(8)))
        data = self.analyze(max_dependencies=2).to_dict()
        self.assertLessEqual(len(data["dependencies"]["declarations"]), 2)
        self.assertTrue(data["dependencies"]["truncated"])
        self.assertTrue(any("Dependency declaration limit" in warning
                            for warning in data["warnings"]))

    def test_custom_excludes_are_applied(self):
        write(self.root, "app.py", "print('kept')\n")
        write(self.root, "private/generated.py", "print('hidden')\n")
        data = self.analyze(excludes=("private",)).to_dict()
        paths = {item["path"] for item in data["structure"]["tree"]}
        self.assertNotIn("private/generated.py", paths)
        self.assertEqual(self.language(data, "Python")["file_count"], 1)

    def test_symlinked_files_are_not_followed(self):
        outside = Path(self._tmp.name) / "outside.py"
        outside.write_text("SECRET_CANARY = 'outsideSecretValue123'\n", encoding="utf-8")
        root = Path(self._tmp.name) / "project"
        root.mkdir()
        (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
        try:
            (root / "linked.py").symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation is unavailable on this platform.")
        self.root = root
        data = self.analyze().to_dict()
        self.assertNotIn("outsideSecretValue123", json.dumps(data))
        self.assertNotIn("linked.py", {item["path"] for item in data["structure"]["tree"]})

    def test_architecture_does_not_confirm_from_directory_names_alone(self):
        for directory in ("controllers", "services", "models", "views", "plugins"):
            write(self.root, f"{directory}/__init__.py", "")
        write(self.root, "app.py", "VALUE = 1\n")
        write(self.root, "README.md", "# Layered\n")
        data = self.analyze().to_dict()
        layered = self.architecture(data, "layered architecture")
        mvc = self.architecture(data, "MVC")
        plugin = self.architecture(data, "plugin architecture")
        self.assertEqual(layered["verdict"], "possible")
        self.assertEqual(mvc["verdict"], "possible")
        self.assertEqual(plugin["verdict"], "possible")
        self.assertNotIn("confirmed", {item["verdict"] for item in data["architecture"]})

    def test_mobile_and_unknown_framework_cases(self):
        write(self.root, "pubspec.yaml", """name: demo
environment:
  sdk: ">=3.0.0 <4.0.0"
dependencies:
  flutter:
    sdk: flutter
""")
        write(self.root, "lib/main.dart", "void main() {}\n")
        write(self.root, "test/widget_test.dart", "void main() {}\n")
        write(self.root, "README.md", "# Flutter\n")
        data = self.analyze().to_dict()
        self.assertIn("Flutter", self.names(data["frameworks"]))
        self.assertEqual(self.architecture(data, "mobile application")["verdict"], "confirmed")
        self.assertIn("Pub", self.names(data["build_systems"]))

        other = Path(self._tmp.name) / "unknown"
        other.mkdir()
        (other / "index.html").write_text("<!doctype html><title>x</title>\n", encoding="utf-8")
        (other / "style.css").write_text("body { color: black; }\n", encoding="utf-8")
        (other / "script.js").write_text("console.log('x');\n", encoding="utf-8")
        report = ProjectIntelligenceEngine().analyze(
            other, AnalysisOptions(registry_lookup=False))
        unknown = report.to_dict()
        self.assertEqual(unknown["frameworks"], [])
        self.assertEqual(unknown["build_systems"], [])
        self.assertEqual(unknown["errors"], [])


if __name__ == "__main__":
    unittest.main()
