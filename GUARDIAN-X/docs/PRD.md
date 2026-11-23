# Product Requirements Document — OMEGA GUARDIAN X

**Product:** OMEGA GUARDIAN X (OGX)  
**Edition target:** Universal / Professional / Business / Enterprise / Self-hosted / SaaS  
**Current release:** 0.7.1a1 Guardian alpha with universal engine layer

**Status:** Foundation implementation; universal static analysis is implemented, Android build/test execution is not configured, and enterprise capabilities are planned and gated by evidence.

## 1. Product vision

Provide a universal repository intelligence platform that can be added to an existing software project, identify its technology stack, select compatible analyzers, and produce actionable, evidence-backed engineering and security findings without requiring the target project to be executed.

## 2. Primary users

- Individual developers maintaining one or more repositories.
- Engineering teams responsible for CI quality and dependency hygiene.
- Application-security and DevSecOps teams.
- Software vendors maintaining many customer-specific codebases.
- Enterprises that require self-hosted deployment, auditability, and policy control.

## 3. Core user journey

1. Install OGX in its own supported environment.
2. Run `ogx start <project-path>` for a read-only scan, or optionally run `ogx integrate plan <project-path>` and `ogx integrate apply <project-path> --confirm` to add project-root agent governance.
3. OGX discovers manifests, lockfiles, languages, framework indicators, and CI files.
4. OGX selects only analyzers supported for the detected project.
5. The scanner reads bounded files and performs local static checks.
6. Findings are emitted with rule ID, severity, location, evidence, confidence, and recommendation.
7. The user reviews JSON/Markdown reports; no source code is automatically modified.

## 4. Functional requirements

### FR-01 — Universal detection

Detect supported languages and ecosystem manifests recursively while respecting exclusions, symlinks, and resource limits. Detection must state indicators and avoid claiming complete support for an ecosystem from a file extension alone.

### FR-02 — Safe onboarding and project control integration

Default onboarding/scanning remains external, read-only, and explicit. The optional project-control integration must preview every proposed file, require a separate confirmation, create only a fixed allowlist of missing files, preserve all conflicts, reject symlink destinations, and record SHA-256 ownership. Removal must be separately confirmed and delete only unchanged OGX-created files.

### FR-03 — Dependency intelligence

Parse supported manifests and lockfiles, normalize package identities, identify duplicate/unbounded declarations, and eventually resolve transitive dependency graphs. Unsupported formats must be reported as unsupported, not silently treated as clean.

### FR-04 — Security analysis

Use rule packs with stable IDs, severity, confidence, safe evidence, and remediation guidance. Heuristic results must be labelled as potential findings pending verification.

### FR-05 — Reports

Produce machine-readable and human-readable reports, include scan metadata and coverage limitations, and redact secrets. Enterprise editions should add SARIF, SBOM, trend/baseline and policy outputs.

### FR-06 — Extensibility

Support versioned language/framework/package-manager adapters. Default adapters are read-only and cannot run target code, invoke package managers, or make network calls.

### FR-07 — Debugging

0.4.0a1 can analyze log patterns, map selected traceback frames to statically indexed source files, correlate nearby scan findings, and produce root-cause hypotheses and regression-test plans. It does not attach a live debugger or prove causation. Any test execution requires explicit approval and the restricted sandbox path.

### FR-08 — Remediation

Future remediation may produce patch proposals. Applying patches requires explicit approval, backups/rollback, and successful regression validation. No autonomous production deployment.

### FR-09 - Enterprise administration

Future enterprise control plane must provide tenant isolation, role-based access, SSO, policy packs, signed updates, audit export, retention controls, and self-hosted deployment.

### FR-10 - Universal engine contract

Provide a stable, versioned engine contract with explicit capability states (`supported`, `partial`, `not_configured`, `unsupported`, `failed`), failure isolation, execution/network assertions, bounded resource use, and engine provenance on every finding. A registry must expose engine health and support additive, spec-driven language/technology engines without changing existing scanner behavior.

### FR-11 - Android intelligence

Provide an equal-priority Android engine that statically inspects manifests, Gradle configuration, resources, sources, tests and APK/AAB metadata. It must report unavailable SDK/Gradle/adb/emulator capabilities as `not_configured` or `not_attempted`, never simulate build, test, device or signature-verification results. Executing builds, tests, adb or emulators requires a separately approved isolated environment.

## 5. Non-functional requirements

- **Security:** least privilege, deny-by-default capabilities, safe parsing, path containment, secret redaction, signed release artifacts.
- **Privacy:** local-first scanning; no source upload without explicit user opt-in and documented data controls.
- **Performance:** bounded files and file sizes; incremental scan/cache planned; avoid parallel heavy workloads by default.
- **Reliability:** deterministic rule outputs, stable schemas, regression fixtures, and supported-version matrix.
- **Portability:** Windows, Linux, and macOS; initial runtime Python 3.11+.
- **Maintainability:** modular adapters, typed interfaces, isolated tests, versioned report schema.
- **Transparency:** every scan reports enabled capabilities and known coverage gaps.

## 6. Commercial architecture requirements

One core engine should support different delivery models without duplicating scanners:

- Local CLI and IDE integration.
- CI runner and ephemeral build agent.
- Customer-managed self-hosted service.
- Private cloud control plane.
- Multi-tenant SaaS control plane with strict tenant/data isolation.

Entitlements and licensing must be enforced by signed, auditable policy—not by unsafe obfuscation or hidden data collection. Final pricing, EULA, trademark and distribution terms require business and legal review.

## Current 0.7.1a1 universal engine increment

The 0.7.1a1 increment adds an additive universal static-analysis layer: an engine contract and registry, 51 engines (1 Android + 50 spec-driven technology engines), bounded read-only workspace indexing, unified findings with stable IDs/provenance/deduplication, build/test discovery, and the `engines list`, `engines inspect`, `analyze` and `android` CLI commands. Reports are written only under external `OGX_HOME` state. Android build/test/device operations are not configured in this environment and are not simulated. The pre-existing scanner and Guardian workflows remain intact. See `docs/UNIVERSAL_AUDIT.md`, `docs/ANDROID_ENGINE.md` and `docs/LANGUAGE_SUPPORT_MATRIX.md`.

## Previous 0.5.0a1 Guardian increment

The 0.5.0a1 Guardian increment provides consolidated repository audits, common JSON/TOML/XML syntax checks, maintenance prioritization, one narrow allowlisted safe repair, optional read-only Git remote comparison, confirmed ref-only fetch, Dependabot/Renovate configuration and an OGX self-audit proposal. It does not provide generic AI source patch generation, automatic dependency merges, unattended deployment or autonomous engine self-rewriting. See `docs/UNIVERSAL_GUARDIAN.md` for the operational contract.

## 7. Acceptance criteria for 0.4.0a1 foundation

- Runs on Python 3.11+ without third-party runtime dependencies.
- Detects multiple common language/ecosystem indicators in a nested repository.
- Scans without executing target code or modifying source files.
- Enforces file count/size bounds and excludes common generated/vendor paths.
- Rejects report paths that escape the repository or use symlinked path components.
- Redacts potential secret values from report evidence.
- Previews project-root integration without writes, requires explicit confirmation to apply, and preserves existing files.
- Removes only unchanged OGX-created files based on recorded hashes.
- Emits JSON, Markdown, SARIF and best-effort CycloneDX inventory with explicit capability limitations.
- Generates log/trace debug hypotheses, test proposals and static import impact estimates.
- Supports optional exact-version OSV requests only after explicit network opt-in.
- Passes the included unit tests and builds/install tests an isolated wheel.

## 7a. Acceptance criteria for 0.7.1a1 universal layer

- Registers 51 unique engines with stable IDs, versions, categories and explicit capability states.
- Reports `execution_performed: false` and `network_access: false` for every scan.
- Never executes project scripts, builds, tests, package managers, adb, emulators or devices.
- Produces unified findings with provenance, verification status, deduplication and redacted evidence.
- Writes universal and Android reports only to external OGX state; refuses symlinked report destinations.
- Detects Android statically and reports build/test as `not_configured` and device operations as `not_attempted` when the toolchain is absent.
- Keeps the existing CLI commands and scanner behavior working; the full local suite passes.
- Builds a wheel containing the engine layer and installs into an isolated target for a CLI smoke test.

## 8. Explicit exclusions from 0.4.0a1

Full dependency resolution, comprehensive CVE coverage, complete SAST/DAST/data-flow, malware detection, dynamic runtime debugger, proven root cause, autonomous code repair, production-grade sandbox certification, full Git provider lifecycle, live AI agent execution, plugin runtime, hosted dashboard/API, production SaaS, SSO/RBAC enforcement, commercial billing/licensing server and compliance certification are not implemented. AI-agent instruction files are not an OS-enforced control. Android build/test execution, emulator/device validation, binary-manifest decoding and cryptographic APK/AAB signature verification are also not implemented or not configured; they are not claimed by static analysis.
