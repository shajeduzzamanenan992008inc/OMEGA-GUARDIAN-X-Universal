# Project Intelligence Engine (Milestone G)

`ogx intelligence analyze` is a **static, read-only** project-understanding layer. It
does not replace the security pipeline, the Android engine, or project discovery.
It consumes the existing bounded `Workspace`, project detector, language specs,
declaration-only manifest parsers, and test discovery, then adds a report model
that keeps observations, inference and limitations separate.

## Boundaries

The engine never:

- imports or executes project code, tests, hooks, plugins or scripts;
- runs builds, package managers, installers or lifecycle scripts;
- accesses the network or resolves dependencies from a registry;
- writes to the analyzed project;
- reads or prints the contents of a file identified as a possible secret;
- follows symlinks outside the selected project.

All output is re-computed from the current bounded file index. There is no cache
and no registry write. Reports can be written as external artifacts under
`OGX_HOME`, outside the analyzed project.

## Architecture

```
src/ogx/intelligence/
  __init__.py              public engine/options exports
  engine.py                orchestration and report assembly
  models.py                dataclasses, confidence, schema and serialization
  options.py               options, limits, shared analyzer context
  integration.py           read-only identity and Registry V2 association
  detectors/
    project_type.py        classification from architecture indicators
    technology.py          evidence-based framework/technology signatures
    package_manager.py     manifest + lockfile package-manager inference
  analyzers/
    common.py              path, generated/vendored and tree heuristics
    language.py            extension census + language manifests
    dependencies.py        declaration-only manifest parsing and scopes
    structure.py           bounded tree, source/test/doc/CI/package roots
    entrypoints.py         static entry-point conventions and signatures
    framework.py           framework/technology analyzer wrapper
    build_system.py        build manifests, backends, CI build commands
    testing.py             test discovery, frameworks, coverage/lint/type config
    documentation.py       documentation presence indicators
    configuration.py       configuration categories and sensitive-name detection
    architecture.py        graded architecture indicators
    health.py              transparent indicator-coverage profile
  reports/
    json_report.py         stable JSON rendering
    formatter.py           terminal and Markdown rendering
```

Analyzers run in a fixed dependency order: language, dependencies, structure,
entry points, frameworks, build, testing, documentation, configuration,
architecture, health. Each analyzer is isolated: an unexpected failure is
recorded in `errors` and a limitation, while the remaining analyzers continue.

## Models and evidence

`ProjectIntelligence` contains project identity, classification, language,
framework/technology, dependency, structure, entry-point, architecture,
build-system, testing, documentation, configuration, health, finding,
recommendation and limitation sections. Every report has:

- `schema_version` (`ogx-intelligence-1`) and `engine_version`;
- an `analysis_id` derived from project identity, tree digest and schema version;
- `analysis_mode: static-read-only`, `execution_performed: false` and
  `network_access: false`;
- `cache_status: recomputed`;
- explicit `warnings`, `errors` and analysis limitations.

Confidence is evidence strength, not probability:

| Value | Meaning |
|---|---|
| `confirmed` | direct manifest, configuration, signature or declaration evidence |
| `likely` | multiple independent indicators or a strong declaration |
| `possible` | suggestive structural evidence only |
| `unknown` | no reliable evidence; not a negative claim |

Directory names alone are capped below `confirmed`. Conflict between an immutable
identity file and an explicit ID preserves the identity file and records a note.
Findings carry a finding ID, category, structured evidence (`kind`, repository-
relative `location`, `detail`), confidence, method and limitations.

## Language intelligence

Language detection uses the canonical `engines.language_specs.SPECS` extension
map plus language-specific manifest markers. Generated, minified and vendored
paths are excluded from counts; generated exclusions are reported.

Verified extension-driven language families include Python, JavaScript,
TypeScript, Java, Kotlin, C, C++, C#, Go, Rust, Ruby, PHP, Swift, Dart, Scala, R,
Julia, Lua, Perl, Haskell, Elixir, Erlang, Zig, Assembly, Solidity, SQL,
WebAssembly, HTML, CSS/SCSS/SASS/LESS, XML, JSON, TOML, YAML, Markdown,
Protocol Buffers, GraphQL, Shell, PowerShell, Batch, Terraform, Dockerfile,
Kubernetes, Helm, CI, Vue, Svelte and Groovy.

Only code-category languages receive `primary`/`secondary` roles. Data,
configuration and documentation languages are `incidental`. Mixed-language
projects keep all countable languages. A single file without a manifest marker
is `possible`; it is not treated as confirmed.

## Framework and technology intelligence

Frameworks are detected from declared dependencies, recognized configuration
files and explicit platform markers. Directory names alone are never framework
evidence.

Framework signatures include Django, Flask, FastAPI, Streamlit, Apache Airflow,
React, Next.js, Vue, Angular, Svelte, SvelteKit, Express, Fastify, NestJS,
React Native, Electron, Tauri, Spring Boot, Spring Framework, ASP.NET Core,
Gin, Echo, Fiber, Axum, Actix Web, Laravel, Symfony, Rails and Flutter.

Technology signatures additionally cover Pydantic, SQLAlchemy, PyTorch,
TensorFlow, scikit-learn, pandas, NumPy, Jupyter, pytest, Click, Typer, Celery,
Vite, Jest, Vitest, Mocha, Playwright, Cypress, TypeScript, JUnit, Jetpack
Compose, Hibernate, Entity Framework Core, xUnit, NUnit, MSTest, Cobra, GORM,
Tokio, Clap, Serde, RSpec, Docker, Helm, Terraform, GitHub Actions, GitLab CI,
Jenkins, Ansible, and declared database clients for PostgreSQL, MySQL, MongoDB,
Redis and SQLite.

A database entry describes a declared client or configuration, not a running
service. A framework declaration does not prove runtime presence or version
behavior.

## Dependency intelligence

The engine recognizes declaration manifests and extracts names, constraints
and scopes without installation or resolution:

- `requirements*.txt`, `pyproject.toml`, `package.json`, `pom.xml`,
  `build.gradle`, `build.gradle.kts`, `Cargo.toml`, `go.mod`,
  `*.csproj`/`*.fsproj`/`*.vbproj`, `composer.json`, `Gemfile`,
  `pubspec.yaml`, `Package.swift`, `Project.toml`, `DESCRIPTION`,
  `cpanfile`, and `*.rockspec`.
- Lockfiles are recognized by name (including `poetry.lock`, `uv.lock`,
  `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `Cargo.lock`, `go.sum`,
  `composer.lock`, `Gemfile.lock`, `Package.resolved`, `pubspec.lock`,
  `gradle.lockfile`, `mix.lock`, `Manifest.toml`, `renv.lock`), but their
  contents are not resolved.
- Scopes are inferred from manifest sections (`runtime`, `development`,
  `optional`, `build`, `peer` where the parser supports it).
- Nested package manifests participate in monorepo package discovery and
  package-manager inference.

Unsupported declarations are listed as limitations rather than guessed:
`Pipfile`, `setup.py`, `setup.cfg`, `environment.yml`, `mix.exs`, `build.sbt`,
`packages.config`, `vcpkg.json`, `conanfile.txt` and `conanfile.py`. No
transitive graph, installed version, vulnerability lookup or lockfile
resolution is produced.

## Structure, entry points and build systems

Structure analysis builds a bounded tree with configurable depth and entry
limits. It records source, test, documentation, script and asset roots; CI and
infrastructure files; build-output names; generated-file counts; and nested
monorepo package roots with workspace indicators.

Entry-point candidates are static: Python main guards and conventional modules,
PEP 621/Poetry scripts and console scripts, JavaScript `main`/`module`/`bin`
and start/dev scripts, Rust binary targets, Go `package main`, JVM `main`,
native `main`, .NET `Main`, Swift `@main`, Dart `lib/main.dart`, HTML documents
and Android launcher activities. Nothing is imported or launched.

Build-system detection covers Python backends (setuptools, Hatch, Poetry, Flit,
PDM, uv, Maturin, scikit-build-core, Meson), Node tooling (Vite, Next.js,
webpack, Rollup, esbuild, Parcel, TypeScript compiler, package scripts), CMake,
Make, Meson, Zig, Gradle, Maven, sbt, Cargo, Go, Swift Package Manager, Pub,
Composer, Bundler, Mix, Stack, Cabal, Foundry, MSBuild/.NET SDK and Bazel. CI
files are inspected for build commands only; commands are never run.

## Testing, documentation and configuration

Testing discovery reuses the existing static `discover_tests` helper. The
report records test files, directories, framework declarations, configuration,
coverage configuration, lint/static-analysis configuration, type-checking
configuration and CI test workflows. `executed` is always `false`; file counts
are not test-case counts and do not establish pass/fail or measured coverage.

Documentation indicators are presence-only: README, documentation directory,
CONTRIBUTING, CHANGELOG, license, security policy, architecture documentation
and API documentation. Content quality, accuracy and freshness are not judged.

Configuration categories are packaging, build, containers, infrastructure,
environment examples, linting, typing, testing, governance and VCS. Sensitive
candidates are detected by filename/path only. Their contents are never read,
printed or included; `.env.example`, `.env.sample` and `.env.template` are
treated as safe templates. A filename match is not proof that a credential is
present.

## Architecture indicators

Indicators include monorepo, microservices, layered architecture, MVC, MVVM,
modular architecture, plugin architecture, CLI application, web application,
desktop application, mobile application, API service, library, data-science
project and machine-learning project.

Verdicts follow the confidence model. For example, a declared executable entry
point can confirm a CLI; a backend framework without a frontend can make an API
service likely or confirmed when API documentation is present; multiple
directory names alone can only make a layered/MVC/plugin claim `possible`.
Runtime topology, deployment and design intent remain unverified.

## Engineering health

The health profile contains 11 fixed dimensions: project organization,
dependency management, testing, documentation, configuration consistency,
build system, CI/CD, static analysis, type checking, lockfiles and architecture
clarity.

Indicator coverage is transparent:

- `established` = 1.0
- `partial` = 0.5
- `missing` = 0.0
- `not_applicable` and `unknown` are excluded from the denominator.

The score is the equal-weight mean of scored dimensions, multiplied by 100.
With no scored dimensions, `score` is `null`/unavailable. This measures
observable engineering indicators only. It is **not** a code-quality score, a
security assessment, a certification or a team-performance measure. Missing
evidence is not automatically poor quality; for example, no lockfile is only
`partial` because it may be intentional for a library.

## CLI

```bash
ogx intelligence analyze PROJECT_ID
ogx intelligence analyze --path ./project
ogx intelligence analyze --project registered-name
ogx intelligence analyze --path ./project --format json
ogx intelligence analyze --path ./project --json
ogx intelligence analyze --path ./project --format markdown
ogx intelligence analyze --path ./project --format both
ogx intelligence analyze --path ./project --languages
ogx intelligence analyze --path ./project --dependencies
ogx intelligence analyze --path ./project --architecture
ogx intelligence analyze --path ./project --health
```

Options: `--exclude DIR` (repeatable), `--max-files`, `--max-depth`,
`--no-registry`. `terminal` is the default. `json` and `markdown` print to
stdout. `both` prints terminal output and writes versioned JSON and Markdown
files under external `OGX_HOME` storage. JSON output includes every declaration;
terminal and Markdown reports bound long lists.

Target resolution precedence is `--path`, then `--project` (legacy registered
name), then a positional target. A positional target that is an existing
directory is analyzed directly; otherwise it is resolved as a Registry V2
project ID or canonical name. A registry project without an accessible local
workspace is an error: intelligence does not clone or access remotes.

## Identity and Registry V2

Identity sources, strongest first:

1. `.guardian-x/identity.json` in the project (immutable project identity);
2. an existing Registry V2 project/workspace at the normalized location;
3. an explicit project ID;
4. the deterministic workspace fingerprint.

The engine only reads Registry V2. It never registers a project, creates a
workspace, updates metadata or duplicates a project. Relocating a project with
its identity file preserves the identity. Analysis results are recomputed, not
cached. `analysis_id` changes when the identity, tree digest or schema changes.

## Resource limits and exclusions

Defaults: 12,000 files, 2 MB per read, 4 MB per manifest, depth 6, 400 tree
entries, 5,000 dependency declarations, 300 entry-point files, 2,000 analyzed
files and a 60-second analysis deadline. The workspace always excludes VCS
metadata, virtual environments, `node_modules`, `vendor`, common build outputs,
caches and coverage directories; additional directory names can be supplied
with `--exclude` or `AnalysisOptions.excludes`.

File, tree and declaration limits produce explicit warnings and partial
coverage. Symlinked files and directories are skipped. Each read is confined to
the resolved project root. Analysis reads UTF-8 text with replacement for
invalid bytes and rejects binary/NUL content for text parsers. XML manifests
reject DTD/entity declarations.

## Extending

- **Language**: add or update a `LanguageSpec` in
  `src/ogx/engines/language_specs.py`; the intelligence census consumes the
  extension map and manifest markers. Add a language-marker mapping in
  `analyzers/language.py` if no existing manifest convention applies.
- **Framework/technology**: add a `TechnologySignature` in
  `detectors/technology.py` (packages, prefixes, files or bounded globs). Keep
  directory-name evidence out of `confirmed` unless another direct source is
  present.
- **Manifest parser**: add a declaration-only parser to the existing
  `engines/language.py` family, then map the filename in
  `analyzers/dependencies.py` and add scope extraction. Never execute the
  manifest.
- **Analyzer**: implement a bounded function over `AnalysisContext`, register
  it in `analyzers/__init__.py` and `ANALYZER_ORDER`, add its name to
  `ANALYZER_NAMES`, and add it to the engine dispatch and report model.
- **Report field**: add the model field, export it in `to_dict`, and update
  both renderers. Add deterministic tests for serialization and section
  filtering.

## Known limitations

- Language and framework detection are declaration/extension heuristics; they
  do not prove semantic use, runtime presence or versions.
- Architecture indicators are structural evidence, not a design review,
  dependency graph, runtime topology or deployment model.
- Dependency graphs are not resolved; installed and transitive versions are
  unknown; lockfiles are recognized but not parsed.
- Import graphs, call graphs, data flow and dynamic routing are out of scope.
- Test counts are discovered files, not test cases; no test is run, so
  pass/fail and real coverage are unknown.
- Documentation and health indicators measure presence/coverage, not quality,
  accuracy, freshness or security.
- Generated/vendored detection is filename and directory heuristic based.
- Custom test layouts, custom entry conventions, workspace globs with
  non-standard manifests, and permission-denied paths may be missed.
- Reports are bounded and recomputed; they are not a complete filesystem
  inventory and are not cached.
