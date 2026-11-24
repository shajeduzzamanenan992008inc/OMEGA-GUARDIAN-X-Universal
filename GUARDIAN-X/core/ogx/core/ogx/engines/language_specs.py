"""Declarative language and technology specifications.

Adding a technology should normally mean adding a spec and, where necessary, a
small parser/rule declaration.  The orchestration layer is not language-aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    key: str
    name: str
    extensions: tuple[str, ...] = ()
    file_names: tuple[str, ...] = ()
    manifest_globs: tuple[str, ...] = ()
    manifests: tuple[str, ...] = ()
    lockfiles: tuple[str, ...] = ()
    build_adapter_ids: tuple[str, ...] = ()
    test_languages: tuple[str, ...] = ()
    parser: str = "text"
    category: str = "language"
    notes: tuple[str, ...] = ()
    detection_mode: str = "extension"  # extension | name | manifest | content


SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        "python", "Python", (".py", ".pyi"),
        manifests=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
                   "requirements-dev.txt", "Pipfile", "environment.yml"),
        lockfiles=("poetry.lock", "uv.lock", "Pipfile.lock"),
        build_adapter_ids=("python-pip", "python-poetry", "python-uv"),
        test_languages=("Python",), parser="python-ast",
        notes=("Python files are parsed with the standard-library AST; code is never executed.",),
    ),
    LanguageSpec(
        "javascript", "JavaScript", (".js", ".jsx", ".mjs", ".cjs"),
        manifests=("package.json",),
        lockfiles=("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"),
        build_adapter_ids=("javascript-npm", "javascript-pnpm", "javascript-yarn", "javascript-bun"),
        test_languages=("JavaScript/TypeScript",), parser="c-like",
        notes=("No JavaScript parser dependency is bundled; delimiter and pattern checks are heuristic.",),
    ),
    LanguageSpec(
        "typescript", "TypeScript", (".ts", ".tsx", ".mts", ".cts"),
        file_names=("tsconfig.json", "jsconfig.json"),
        manifests=("package.json",),
        lockfiles=("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"),
        build_adapter_ids=("javascript-npm", "javascript-pnpm", "javascript-yarn", "javascript-bun"),
        test_languages=("JavaScript/TypeScript",), parser="c-like",
        notes=("tsconfig.json/jsconfig.json are parsed with a bounded JSONC lexical helper.",),
    ),
    LanguageSpec(
        "java", "Java", (".java",),
        manifests=("pom.xml", "build.gradle"),
        lockfiles=("gradle.lockfile",),
        build_adapter_ids=("jvm-maven", "jvm-gradle"),
        test_languages=("Java",), parser="c-like",
    ),
    LanguageSpec(
        "kotlin", "Kotlin", (".kt", ".kts"),
        manifests=("build.gradle.kts",),
        build_adapter_ids=("jvm-gradle-kts",),
        test_languages=("Kotlin",), parser="c-like",
        notes=("Android-specific Kotlin/Java analysis is owned by the dedicated Android engine.",),
    ),
    LanguageSpec(
        "c", "C", (".c", ".h"),
        manifests=("CMakeLists.txt", "Makefile", "meson.build", "conanfile.txt", "vcpkg.json"),
        build_adapter_ids=("cpp-cmake", "cpp-bazel"),
        test_languages=(), parser="c-like",
    ),
    LanguageSpec(
        "cpp", "C++", (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        manifests=("CMakeLists.txt", "Makefile", "meson.build", "vcpkg.json"),
        build_adapter_ids=("cpp-cmake", "cpp-bazel"),
        test_languages=(), parser="c-like",
    ),
    LanguageSpec(
        "csharp", "C#", (".cs",),
        manifests=("packages.config", "Directory.Build.props"),
        manifest_globs=("*.csproj", "*.sln", "*.fsproj", "*.vbproj"),
        lockfiles=("packages.lock.json",),
        build_adapter_ids=("dotnet",),
        test_languages=("C#",), parser="c-like",
    ),
    LanguageSpec(
        "go", "Go", (".go",),
        manifests=("go.mod",), lockfiles=("go.sum",),
        build_adapter_ids=("go",), test_languages=("Go",), parser="c-like",
    ),
    LanguageSpec(
        "rust", "Rust", (".rs",),
        manifests=("Cargo.toml",), lockfiles=("Cargo.lock",),
        build_adapter_ids=("rust",), test_languages=("Rust",), parser="c-like",
    ),
    LanguageSpec(
        "ruby", "Ruby", (".rb", ".rake"),
        manifests=("Gemfile",), manifest_globs=("*.gemspec",),
        lockfiles=("Gemfile.lock",),
        build_adapter_ids=("ruby",), test_languages=("Ruby",), parser="text",
    ),
    LanguageSpec(
        "php", "PHP", (".php", ".phtml"),
        manifests=("composer.json",), lockfiles=("composer.lock",),
        build_adapter_ids=("php",), test_languages=("PHP",), parser="c-like",
    ),
    LanguageSpec(
        "swift", "Swift", (".swift",),
        manifests=("Package.swift",), lockfiles=("Package.resolved",),
        build_adapter_ids=("swift",), test_languages=("Swift",), parser="c-like",
    ),
    LanguageSpec(
        "dart", "Dart", (".dart",),
        manifests=("pubspec.yaml",), lockfiles=("pubspec.lock",),
        build_adapter_ids=("dart",), test_languages=("Dart",), parser="text",
    ),
    LanguageSpec(
        "r", "R", (".r",),
        manifests=("DESCRIPTION", "renv.lock"),
        build_adapter_ids=(), test_languages=(), parser="text",
    ),
    LanguageSpec(
        "julia", "Julia", (".jl",),
        manifests=("Project.toml", "Manifest.toml"),
        build_adapter_ids=(), test_languages=(), parser="text",
    ),
    LanguageSpec(
        "lua", "Lua", (".lua",),
        manifest_globs=("*.rockspec",), build_adapter_ids=(), test_languages=(), parser="text",
    ),
    LanguageSpec(
        "perl", "Perl", (".pl", ".pm"),
        manifests=("cpanfile", "Makefile.PL"), build_adapter_ids=(), test_languages=(), parser="text",
    ),
    LanguageSpec(
        "scala", "Scala", (".scala", ".sc"),
        manifests=("build.sbt",), build_adapter_ids=("scala",),
        test_languages=("Scala",), parser="c-like",
    ),
    LanguageSpec(
        "haskell", "Haskell", (".hs", ".lhs"),
        manifests=("stack.yaml", "cabal.project"), manifest_globs=("*.cabal",),
        build_adapter_ids=("haskell",),
        test_languages=("Haskell",), parser="text",
    ),
    LanguageSpec(
        "elixir", "Elixir", (".ex", ".exs"),
        manifests=("mix.exs",), lockfiles=("mix.lock",),
        build_adapter_ids=("elixir",), test_languages=("Elixir",), parser="text",
    ),
    LanguageSpec(
        "erlang", "Erlang", (".erl", ".hrl"),
        manifests=("rebar.config",), build_adapter_ids=("erlang",),
        test_languages=("Erlang",), parser="text",
    ),
    LanguageSpec(
        "zig", "Zig", (".zig",),
        manifests=("build.zig",), build_adapter_ids=(), test_languages=(), parser="c-like",
    ),
    LanguageSpec(
        "assembly", "Assembly", (".asm", ".s", ".nasm", ".masm"),
        build_adapter_ids=(), test_languages=(), parser="text",
        notes=("Assembly is inventoried; no assembler or semantic analysis is invoked.",),
    ),
    LanguageSpec(
        "solidity", "Solidity", (".sol",),
        manifests=("foundry.toml", "hardhat.config.js", "hardhat.config.ts", "truffle-config.js"),
        build_adapter_ids=("solidity",), test_languages=(), parser="c-like",
        notes=("Contract security review is heuristic; no compiler or symbolic execution is performed.",),
    ),
    LanguageSpec(
        "sql", "SQL", (".sql",),
        manifests=(), build_adapter_ids=(), test_languages=(), parser="text",
    ),
    LanguageSpec(
        "wasm", "WebAssembly", (".wat", ".wasm"),
        build_adapter_ids=(), test_languages=(), parser="text",
        notes=("Binary .wasm files are inventoried by size/hash only; they are not disassembled.",),
    ),
    LanguageSpec(
        "html", "HTML", (".html", ".htm"), build_adapter_ids=(), test_languages=(), parser="text",
        category="markup",
    ),
    LanguageSpec(
        "css", "CSS", (".css",), build_adapter_ids=(), test_languages=(), parser="text",
        category="style",
    ),
    LanguageSpec(
        "scss", "SCSS", (".scss",), build_adapter_ids=(), test_languages=(), parser="text",
        category="style",
    ),
    LanguageSpec(
        "sass", "SASS", (".sass",), build_adapter_ids=(), test_languages=(), parser="text",
        category="style",
    ),
    LanguageSpec(
        "less", "LESS", (".less",), build_adapter_ids=(), test_languages=(), parser="text",
        category="style",
    ),
    LanguageSpec(
        "xml", "XML", (".xml", ".xsd", ".xsl", ".xslt", ".svg"), manifests=(),
        build_adapter_ids=(), test_languages=(), parser="xml", category="data",
        notes=("XML DTD/entity declarations are rejected before parsing.",),
    ),
    LanguageSpec(
        "json", "JSON", (".json", ".jsonc"), build_adapter_ids=(), test_languages=(),
        parser="json", category="data",
    ),
    LanguageSpec(
        "toml", "TOML", (".toml",), build_adapter_ids=(), test_languages=(),
        parser="toml", category="configuration",
        notes=("TOML files are parsed with the standard-library tomllib parser.",),
    ),
    LanguageSpec(
        "yaml", "YAML", (".yaml", ".yml"), build_adapter_ids=(), test_languages=(),
        parser="yaml", category="data",
        notes=("No YAML parser dependency is bundled; analysis is limited to structural checks and rules.",),
    ),
    LanguageSpec(
        "markdown", "Markdown", (".md", ".mdx"), build_adapter_ids=(), test_languages=(),
        parser="text", category="documentation",
    ),
    LanguageSpec(
        "protobuf", "Protocol Buffers", (".proto",),
        manifests=("buf.yaml", "buf.gen.yaml"), build_adapter_ids=(), test_languages=(), parser="c-like",
        category="interface",
    ),
    LanguageSpec(
        "graphql", "GraphQL", (".graphql", ".gql"),
        manifests=(".graphqlrc", "codegen.yml", "codegen.yaml"),
        build_adapter_ids=(), test_languages=(), parser="text", category="interface",
    ),
    LanguageSpec(
        "shell", "Shell", (".sh", ".bash", ".zsh", ".ksh"),
        file_names=("Makefile",), build_adapter_ids=(), test_languages=(), parser="shell",
        category="automation",
    ),
    LanguageSpec(
        "powershell", "PowerShell", (".ps1", ".psm1", ".psd1"),
        build_adapter_ids=(), test_languages=(), parser="powershell", category="automation",
    ),
    LanguageSpec(
        "batch", "Batch", (".bat", ".cmd"), build_adapter_ids=(), test_languages=(), parser="text",
        category="automation",
    ),
    LanguageSpec(
        "terraform", "Terraform", (".tf", ".tfvars"),
        manifests=("main.tf", "terraform.tf"), build_adapter_ids=("terraform",),
        test_languages=(), parser="terraform", category="infrastructure",
    ),
    LanguageSpec(
        "dockerfile", "Dockerfile", file_names=("Dockerfile", "Containerfile"),
        manifest_globs=("*.dockerfile",), build_adapter_ids=(), test_languages=(),
        parser="dockerfile", category="container", detection_mode="name",
    ),
    LanguageSpec(
        "kubernetes", "Kubernetes manifests", (".yaml", ".yml"),
        build_adapter_ids=(), test_languages=(), parser="kubernetes", category="infrastructure",
        detection_mode="content",
        notes=("Kubernetes detection is content-based (apiVersion/kind); rendering Helm templates is not attempted.",),
    ),
    LanguageSpec(
        "helm", "Helm chart", manifests=("Chart.yaml", "values.yaml"),
        build_adapter_ids=("helm",), test_languages=(), parser="yaml", category="infrastructure",
        detection_mode="manifest",
    ),
    LanguageSpec(
        "ci", "CI/CD configuration", (),
        file_names=(".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile", "bitbucket-pipelines.yml"),
        manifest_globs=(".github/workflows/*.yml", ".github/workflows/*.yaml", ".circleci/*.yml"),
        build_adapter_ids=(), test_languages=(), parser="yaml", category="ci-cd",
        detection_mode="name",
    ),
    LanguageSpec(
        "vue", "Vue", (".vue",),
        build_adapter_ids=(), test_languages=(), parser="text", category="web",
        notes=("Vue single-file components are inventoried; no template compiler is bundled.",),
    ),
    LanguageSpec(
        "svelte", "Svelte", (".svelte",),
        build_adapter_ids=(), test_languages=(), parser="text", category="web",
        notes=("Svelte components are inventoried; no template compiler is bundled.",),
    ),
    LanguageSpec(
        "groovy", "Groovy", (".groovy",), build_adapter_ids=(), test_languages=(), parser="c-like",
    ),
)


SPEC_BY_KEY = {spec.key: spec for spec in SPECS}


def spec_by_key(key: str) -> LanguageSpec:
    try:
        return SPEC_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown language spec: {key}") from exc
