# Release Review Inspection (Phase 0)

Date: 2026-11-24
Branch: `main`
Starting commit: `c441f01403c2ebcd877970d880d1e66c625274eb`
Worktree at inspection: **clean** (`git status --porcelain` empty)

REA was not used: this is a complete source repository, and the skill directs
ordinary repository analysis for that case.

## 1. Repository layout

```text
.github/workflows/        ci.yml, config-validation.yml, dependency-review.yml, security.yml
GUARDIAN-X/
  VERSION                 0.8.0a1
  pyproject.toml          package omega-guardian-x, no runtime dependencies
  src/ogx/                canonical package (CLI, platform, registry, hosts, intelligence)
  core/ogx/               hand-maintained stdlib drop-in (no platform/ package)
  tests/                  canonical suite (409 passing)
  _dev/tests/             drop-in / architecture suite (245 tests)
  docs/                   architecture, security and completion documents
  dist/, release/         gitignored build outputs
```

## 2. Capabilities present

- 46 CLI commands; read-only scanning and external reports by default.
- Provider engine: 28 descriptors, 7 verified Git adapters, 21 descriptor-only
  families, approval-gated pull-request creation only.
- Registry V2 (SQLite): projects, repositories, workspaces, hosts, lifecycle
  state, migrations, backup/recover.
- Project identity (`.guardian-x/identity.json`) and deterministic repository
  UUIDs.
- Declarative skills/agents; intelligence and static analysis pipeline;
  Docker-based sandbox plan/run.

## 3. Defects and gaps found in this inspection

| ID | Finding | Severity |
| --- | --- | --- |
| I-01 | `validate_api_base` validated only the hostname; `urllib` resolved DNS at connect time, leaving a DNS-rebinding/TOCTOU window | High |
| I-02 | Audit records were append-only but had no integrity envelope; edits were undetectable | Medium |
| I-03 | `hosts` table lacked `display_name`, so every host read raised `IndexError` | High |
| I-04 | `hosts` table lacked `last_seen_at`, so status updates raised `OperationalError` | High |
| I-05 | `HostRegistry.link_project_host` used the process-global registry instead of the injected one, failing for temporary/tenant databases | High |
| I-06 | Repository identity dropped explicit non-default ports, collapsing distinct provider instances | Medium |
| I-07 | No host contract suite; no adapter could be evaluated | Medium |
| I-08 | No shared provider contract suite across adapters | Medium |
| I-09 | CI workflows exist at the repository root but have never executed (no hosted run) | High (release) |
| I-10 | Local verification is Windows/CPython 3.15.2 only (outside the declared 3.11–3.13 classifiers) | Medium |
| I-11 | `python -m build` is shadowed by a local `build/` directory | Low |
| I-12 | PyYAML is no longer available in this environment, so workflow YAML could not be parsed this pass | Low |

## 4. Security concerns carried forward

- Endpoint validation now enforces resolved-address policy, but there is no
  allowlist of provider hosts, no egress-proxy integration, and no certificate
  pinning beyond normal CA validation.
- The audit hash chain is tamper-evident only against partial edits; an actor
  who rewrites the whole file, or truncates the tail, is not prevented.
- Credentials are environment-scoped; no OS keychain, Vault/KMS, rotation or
  revocation integration exists.
- Sandbox execution was not exercised locally (Docker unavailable/untested).
- No live provider API call was made; adapters are mock-verified only.

## 5. Test and CI gaps

- No host lifecycle tests (connect, read, disconnect, health, recovery).
- No live-provider tests and no approved test accounts.
- No Python 3.11/3.12/3.13 or Linux/macOS execution locally.
- No hosted workflow result, no run URL, no branch protection evidence.
- `validate_config.rb` cannot run (Ruby absent).

## 6. Packaging risks

- Wheel and drop-in are built locally; artifacts are gitignored and unsigned.
- No reproducible-build guarantee and no provenance attestation.
- No source distribution was produced in this pass.

## 7. Documentation inconsistencies found

- `HOST_ARCHITECTURE.md` and `CORE_CONTRACTS.md` stated that no host contract
  suite existed.
- `PROJECT_IDENTITY.md` did not document port handling.
- `SECURITY_MODEL.md` did not mention resolved-address pinning or audit
  chaining.
- The completion tracker did not record the defects above.

These were corrected in this pass; see `RELEASE_DOCUMENTATION_AUDIT.md`.
