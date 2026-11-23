# Language and Technology Support Matrix — 0.7.1a1

This matrix describes the **50 spec-driven technology engines** plus the dedicated Android engine. It is a capability declaration, not a certification of semantic depth.

## Reading the columns

- **Detection**: extensions, exact file names, manifest names/globs, or Kubernetes `apiVersion/kind` content detection.
- **Analysis depth**: the strongest registered static parser. Every engine also performs bounded inventory, secret-pattern checks and applicable rule checks.
- **Dependencies**: declaration-level inventory only. No resolver, lockfile verification, installed versions, transitive graph or CVE lookup.
- **Build discovery**: commands are proposed, never executed. `not_configured` is expected when local tools are missing.
- **Test discovery**: conventional files/directories are discovered. No test is executed.

## Matrix

| Technology | Engine | Category | Detection | Analysis depth | Declared dependencies | Build discovery | Test discovery |
|---|---|---|---|---|---|---|---|
| Android | `android` | mobile | manifest + Android Gradle plugin | manifest/Gradle/R8/resource/source/ZIP heuristics | Gradle declarations + conflicts | Android Gradle adapter; `not_configured` without SDK/Gradle/wrapper | unit/instrumentation discovery |
| Python | `lang-python` | language | `.py`, `.pyi`, Python manifests | Python AST syntax; code/secret rules | `pyproject.toml`, `requirements*.txt`; other manifests marker-only | pip/Poetry/uv proposals | registered |
| JavaScript | `lang-javascript` | language | `.js/.jsx/.mjs/.cjs`, `package.json` | delimiter + code/secret rules | `package.json` | npm/pnpm/Yarn/Bun proposals | registered |
| TypeScript | `lang-typescript` | language | `.ts/.tsx/.mts/.cts`, `package.json`, `tsconfig.json` | JSONC + delimiter + code/secret rules | `package.json` | npm/pnpm/Yarn/Bun proposals | registered |
| Java | `lang-java` | language | `.java`, `pom.xml`, `build.gradle` | delimiter + code/secret rules | Maven `pom.xml`, Gradle | Maven/Gradle proposals | registered |
| Kotlin | `lang-kotlin` | language | `.kt/.kts`, `build.gradle.kts` | delimiter + code/secret rules; Android engine adds depth | Gradle Kotlin DSL | Gradle Kotlin DSL proposal | registered |
| C | `lang-c` | language | `.c/.h`, build markers | delimiter + code/secret rules | marker-only | CMake/Bazel proposals | none |
| C++ | `lang-cpp` | language | `.cpp/.cc/.cxx/.hpp/.hh/.hxx` | delimiter + code/secret rules | marker-only | CMake/Bazel proposals | none |
| C# | `lang-csharp` | language | `.cs`, project/solution files | delimiter + code/secret rules | `*.csproj/*.fsproj/*.vbproj` | `dotnet` proposal | registered |
| Go | `lang-go` | language | `.go`, `go.mod` | delimiter + code/secret rules | `go.mod` | `go` proposal | registered |
| Rust | `lang-rust` | language | `.rs`, `Cargo.toml` | delimiter + code/secret rules | `Cargo.toml` | Cargo proposal | registered |
| Ruby | `lang-ruby` | language | `.rb/.rake`, `Gemfile`, gemspecs | text + secret rules | `Gemfile` | Bundler proposal | registered |
| PHP | `lang-php` | language | `.php/.phtml`, `composer.json` | delimiter + code/secret rules | `composer.json` | Composer proposal | registered |
| Swift | `lang-swift` | language | `.swift`, `Package.swift` | delimiter + code/secret rules | `Package.swift` | Swift proposal | registered |
| Dart | `lang-dart` | language | `.dart`, `pubspec.yaml` | text + secret rules | `pubspec.yaml` | Dart/Flutter proposals | registered |
| R | `lang-r` | language | `.r`, `DESCRIPTION`, `renv.lock` | text + secret rules | `DESCRIPTION` | none | none |
| Julia | `lang-julia` | language | `.jl`, `Project.toml` | text + secret rules | `Project.toml` | none | none |
| Lua | `lang-lua` | language | `.lua`, `*.rockspec` | text + secret rules | `*.rockspec` | none | none |
| Perl | `lang-perl` | language | `.pl/.pm`, `cpanfile` | text + secret rules | `cpanfile` | none | none |
| Scala | `lang-scala` | language | `.scala/.sc`, `build.sbt` | delimiter + code/secret rules | marker-only | sbt proposal | registered |
| Haskell | `lang-haskell` | language | `.hs/.lhs`, Stack/Cabal markers | text + secret rules | marker-only | Stack proposal | registered |
| Elixir | `lang-elixir` | language | `.ex/.exs`, `mix.exs` | text + secret rules | marker-only | Mix proposal | registered |
| Erlang | `lang-erlang` | language | `.erl/.hrl`, `rebar.config` | text + secret rules | marker-only | Rebar3 proposal | registered |
| Zig | `lang-zig` | language | `.zig`, `build.zig` | delimiter + code/secret rules | marker-only | none | none |
| Assembly | `lang-assembly` | language | `.asm/.s/.nasm/.masm` | text inventory + secret rules | none | none | none |
| Solidity | `lang-solidity` | language | `.sol`, Foundry/Hardhat/Truffle markers | delimiter + code/secret rules | marker-only | Foundry proposal | none |
| SQL | `lang-sql` | language | `.sql` | text + secret rules | none | none | none |
| WebAssembly | `lang-wasm` | language | `.wat/.wasm` | inventory/hash only; not disassembled | none | none | none |
| HTML | `lang-html` | markup | `.html/.htm` | text + secret rules | none | none | none |
| CSS | `lang-css` | style | `.css` | text + secret rules | none | none | none |
| SCSS | `lang-scss` | style | `.scss` | text + secret rules | none | none | none |
| SASS | `lang-sass` | style | `.sass` | text + secret rules | none | none | none |
| LESS | `lang-less` | style | `.less` | text + secret rules | none | none | none |
| XML | `lang-xml` | data | `.xml/.xsd/.xsl/.xslt/.svg` | DTD-rejected XML parse | none | none | none |
| JSON | `lang-json` | data | `.json/.jsonc` | JSON/JSONC parse | none | none | none |
| TOML | `lang-toml` | configuration | `.toml` | `tomllib` parse | none | none | none |
| YAML | `lang-yaml` | data | `.yaml/.yml` | tab/structure heuristics only | none | none | none |
| Markdown | `lang-markdown` | documentation | `.md/.mdx` | text + secret rules | none | none | none |
| Protocol Buffers | `lang-protobuf` | interface | `.proto`, Buf markers | delimiter + secret rules | marker-only | none | none |
| GraphQL | `lang-graphql` | interface | `.graphql/.gql`, codegen markers | text + secret rules | marker-only | none | none |
| Shell | `lang-shell` | automation | `.sh/.bash/.zsh/.ksh`, `Makefile` | shell pattern rules + secret rules | none | none | none |
| PowerShell | `lang-powershell` | automation | `.ps1/.psm1/.psd1` | PowerShell pattern rules + secret rules | none | none | none |
| Batch | `lang-batch` | automation | `.bat/.cmd` | text + secret rules | none | none | none |
| Terraform | `lang-terraform` | infrastructure | `.tf/.tfvars`, `main.tf` | Terraform pattern rules + secret rules | marker-only | Terraform proposal | none |
| Dockerfile | `lang-dockerfile` | container | `Dockerfile`, `Containerfile`, `*.dockerfile` | Dockerfile pattern rules + secret rules | none | none | none |
| Kubernetes manifests | `lang-kubernetes` | infrastructure | `apiVersion` + `kind` content | Kubernetes pattern rules + secret rules | none | none | none |
| Helm chart | `lang-helm` | infrastructure | `Chart.yaml`, `values.yaml` | YAML structural checks + secret rules | marker-only | Helm proposal | none |
| CI/CD configuration | `lang-ci` | ci-cd | GitHub/GitLab/Azure/CircleCI/Jenkins markers | YAML structural + CI pattern rules | none | none | none |
| Vue | `lang-vue` | web | `.vue` | inventory + secret rules; no template compiler | none | none | none |
| Svelte | `lang-svelte` | web | `.svelte` | inventory + secret rules; no template compiler | none | none | none |
| Groovy | `lang-groovy` | language | `.groovy` | delimiter + code/secret rules | none | none | none |

## Cross-cutting behavior

All engines share:

- bounded workspace indexing (file count, file size and per-engine byte budgets);
- path traversal and symlink refusal;
- secret-pattern scanning with redacted evidence;
- structural code checks where the parser supports them;
- unified finding provenance, stable IDs, confidence and verification status;
- explicit `execution_performed: false` and `network_access: false` in reports.

## What "supported" does not mean

- It does not mean a compiler, linker, interpreter, test runner or package resolver was run.
- It does not mean the language grammar is fully parsed.
- It does not mean dependencies are resolved, pinned or vulnerability-free.
- It does not mean the project builds or its tests pass.
- It does not mean the Android app is installable, signed correctly, or secure at runtime.

For unavailable operations, the engine reports `partial`, `not_configured`, `unsupported` or `failed` with a reason.
