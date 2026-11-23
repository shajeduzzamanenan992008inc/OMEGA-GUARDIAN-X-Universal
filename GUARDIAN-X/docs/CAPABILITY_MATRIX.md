# OGX 0.7.1a1 Capability Matrix

Status: **Implemented** means code exists and has local automated tests; **Heuristic** means rule-based and incomplete; **Adapter foundation** means an interface/integration exists but does not provide the full lifecycle; **Not configured** means the implementation exists but the required local tool/SDK, manifest or test source is absent; **Unsupported** means no implementation for that operation; **Planned** means not shipped in this release. Engine reports use the exact status vocabulary `supported`, `partial`, `not_configured`, `unsupported` and `failed`.

| Capability | Status | Notes |
|---|---|---|
| Local project registry | Implemented | External OGX state |
| Universal engine contract and registry | Implemented | 51 unique engines: 1 Android + 50 declarative technology engines; capability-declared, failure-isolated, static-read-only |
| Engine inspection CLI | Implemented | `ogx engines list` and `ogx engines inspect`; JSON output supported |
| Unified findings | Implemented | Stable IDs, engine provenance, severity/confidence, verification state, deduplication, secret redaction |
| Spec-driven language/technology engines | Implemented, heuristic | Detection plus bounded structural checks; not compiler-grade or full AST per language |
| Android static analysis | Implemented, heuristic | Manifest, Gradle, resources, sources, tests and APK/AAB ZIP metadata; no binary manifest decoding or signature verification |
| Android build/test discovery | Implemented | Reports candidate commands/tools and test files; never executes Gradle, tests or adb |
| Android build/test execution | Not configured | Requires Android SDK + Gradle in an approved isolated environment; not attempted here |
| Device/emulator operations | Not attempted/not configured | No adb, emulator or device is available; no APK install or instrumentation test was run |
| APK/AAB signature verification | Unsupported | ZIP metadata and v1 signature-entry presence only; no cryptographic verification |
| Bounded filesystem inventory | Implemented | Symlinks skipped; configured limits |
| Language/ecosystem detection | Heuristic | Broad manifest/extension indicators, including manifest-only technologies |
| Framework detection | Heuristic | Selected manifest dependency indicators |
| Secrets scanning | Heuristic | Redacted pattern matches; not exhaustive |
| Secure coding rules | Heuristic | Regex patterns; no full taint/data-flow engine |
| Dependency declaration review | Heuristic | Declared manifests across the technology specs; no resolution or transitive graph |
| CycloneDX manifest inventory | Implemented, best-effort | Selected ecosystems; no transitive resolution |
| License/SPDX inventory | Implemented, best-effort | Not legal advice; not complete dependency licensing |
| OSV.dev exact-version advisory query | Implemented, opt-in | Network requires both `--online` and `--confirm-network`; manifest-declared versions only; candidate matches require review |
| Python syntax parse | Implemented | Static parse only; code not executed |
| Static import graph | Implemented, partial | Python AST and JS relative import heuristic |
| Runtime call graph | Planned | Requires runtime/compiler/language adapters |
| Log diagnostics | Implemented, heuristic | Text patterns; no causal proof |
| Debug triage | Implemented, heuristic | Maps selected stack-trace frames, matches log patterns and nearby static findings; hypotheses only |
| Change-impact estimate | Implemented, partial | Static Python/JS import graph; no runtime/data-flow proof |
| Regression test plan | Implemented | Proposal generated from findings/trace locations; does not create tests |
| Remediation plan | Implemented | Evidence-based proposal; no arbitrary source edits |
| Universal Guardian audit | Implemented, heuristic | Combines static scan, inventory, partial architecture graph, compatibility, test/CI and Git signals |
| Maintenance prioritization | Implemented | Recommendations are prioritized from detected evidence and gaps |
| Safe autofix | Implemented, narrow | Adds `.env` ignore rules only when safe; backup stored externally; explicit approval required |
| Upstream Git check/fetch | Implemented | Remote comparison is opt-in; fetch updates refs only and never merges/rebases |
| Cross-platform dependency automation | Adapter/configuration | Dependabot for GitHub; Renovate configuration for other supported platforms; bot deployment is external |
| Autonomous source self-rewrite | Disabled | Self-evolution audits OGX and produces proposals; source changes require reviewed implementation and tests |
| Docker sandbox | Implemented, restricted | Requires local Docker and pinned local image digest |
| Unsandboxed command execution | Disabled | No fallback |
| Git inspect / ls-remote | Implemented | Git CLI |
| Git clone / branch / push | Implemented with explicit confirmation | Direct main/master push refused |
| GitHub API metadata / draft PR | Adapter foundation | Env token required |
| GitLab API metadata / draft MR | Adapter foundation | Env token required |
| Bitbucket API metadata / PR | Adapter foundation | Access token/app password environment |
| Gitea/Forgejo API metadata / PR | Adapter foundation | Custom host inferred from remote URL |
| Generic Git host | Implemented for Git operations | No generic PR API |
| AI agent runtime discovery | Implemented, limited | CLI presence + declarative adapter list |
| Universal agent execution | Planned/adapters partial | Per-agent protocols and permissions still needed |
| Official declarative skill packs | Implemented | Manifest + Markdown; not executed |
| Third-party skill execution | Disabled | Validate-only security posture |
| Tenant-oriented SQLite model | Prototype implemented | Local single-node only; not SaaS |
| Production SaaS/SSO/RBAC | Planned | Requires server, IdP and independent testing |
| Signed marketplace | Planned | Signature and publisher trust infrastructure absent |
| Full CVE intelligence | Planned | OSV.dev opt-in exact-version query exists; comprehensive resolved/transitive CVE intelligence remains planned |
| Full SAST/DAST | Planned | External scanner adapter strategy only |
| Arbitrary source auto-repair / auto commit | Disabled | No generic source patching; commits and pushes remain explicit separate operations |
| Commercial certification | Not claimed | Legal/security audits required |

## Compatibility interpretation

A language can be detected while deep static analysis, dependency resolution or build/test execution is unavailable. Use `ogx compatibility PATH` to view toolchain presence, `ogx engines inspect PATH` for per-engine capability states, and `ogx android PATH` for the Android-specific view. Detection is not certification, and `not_configured` is not a pass.

No Android build, test, device, emulator or APK-signature verification is claimed in this release. The environment used for validation had a JDK but no Android SDK, Gradle, adb, sdkmanager or emulator, so those capabilities remain unverified.
