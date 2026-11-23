"""Universal analysis pipeline.

Combines the engine registry, project fingerprinting and unified findings into
one evidence-oriented report.  The pipeline is strictly local and read-only:
it never executes project code, invokes build tools, or accesses the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from . import __version__
from .config import ScanConfig, load_config
from .detector import detect_project
from .findings import UnifiedFinding, dedupe, severity_summary, unify
from .models import ProjectProfile
from .workspace import Workspace
from .engines.android import AndroidEngine
from .engines.contract import CAPABILITY_NAMES, EngineContext, EngineResult
from .engines.registry import EngineRegistry

SCHEMA = "ogx-universal-report-1"
OBSERVED_RULES = {
    "OGX-BUG-001", "OGX-SYNTAX-JSON", "OGX-SYNTAX-TOML", "OGX-SYNTAX-XML",
    "OGX-SYNTAX-YAML", "OGX-ANDROID-MANIFEST-001", "OGX-ANDROID-NSC-004",
    "OGX-ANDROID-CATALOG-001", "OGX-ANDROID-APK-002",
}


def _context(root: str | Any, config: ScanConfig | None = None) -> tuple[Workspace, ProjectProfile, EngineContext, dict[str, Any]]:
    workspace = Workspace.open(root, config or load_config())
    absolutized = [workspace.root / entry.path for entry in workspace.files]
    profile = detect_project(workspace.root, absolutized)
    fingerprint = workspace.fingerprint()
    context = EngineContext(
        workspace=workspace,
        profile=profile,
        project_id=fingerprint["project_id"],
        fingerprint=fingerprint,
    )
    return workspace, profile, context, fingerprint


def analyze_project(root: str | Any, config: ScanConfig | None = None,
                    engine_ids: Iterable[str] | None = None) -> dict[str, Any]:
    workspace, profile, context, fingerprint = _context(root, config)
    registry = EngineRegistry()
    try:
        selected = registry.select(engine_ids)
    except KeyError as exc:
        raise ValueError(str(exc).strip("'\"")) from exc
    results = [engine.evaluate(context) for engine in selected]
    detected_results = [result for result in results if result.detected]
    findings = _unified_findings(detected_results, context.project_id)
    summary = severity_summary(findings)
    warnings = list(workspace.warnings)
    errors: list[str] = []
    for result in results:
        warnings.extend(result.warnings)
        errors.extend(f"{result.engine_id}: {error}" for error in result.errors)

    report = {
        "schema": SCHEMA,
        "tool": "OMEGA GUARDIAN X",
        "engine_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_id": context.project_id,
        "project": {
            "name": profile.name,
            "root": profile.root,
            "languages": profile.languages,
            "ecosystems": profile.ecosystems,
            "frameworks": profile.frameworks,
            "manifests": profile.manifests,
            "lockfiles": profile.lockfiles,
            "ci_files": profile.ci_files,
            "file_count": len(workspace),
            "total_bytes": workspace.total_bytes,
        },
        "fingerprint": fingerprint,
        "analysis_mode": "static-read-only",
        "execution_performed": False,
        "network_access": False,
        "engines": [_engine_summary(result) for result in results],
        "detected_engines": [result.engine_id for result in detected_results],
        "not_detected_engines": [result.engine_id for result in results if not result.detected],
        "capabilities": _capability_summary(detected_results),
        "findings": [finding.to_dict() for finding in findings],
        "summary": summary,
        "warnings": _dedupe_strings(warnings),
        "errors": _dedupe_strings(errors),
        "limits": [
            "Static analysis only: target source, builds, tests, package managers and installers were not executed.",
            "Language coverage is capability-declared, not certified; see each engine's operation status.",
            "Dependency declarations are not resolved; installed and transitive versions are unknown.",
            "No vulnerability database or network service is queried by this report.",
            "Heuristic findings are observations or inferences, not proof of exploitability.",
            "Android device/emulator, APK install and binary manifest decoding were not performed.",
        ],
    }
    return report


def inspect_engines(root: str | Any, config: ScanConfig | None = None,
                    engine_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Inspect engine detection and declared capabilities without full analysis.

    Capability negotiation may inspect manifests and local tool names, but it
    never executes a build, test, package manager, device command, or network
    request.
    """
    workspace, profile, context, fingerprint = _context(root, config)
    registry = EngineRegistry()
    try:
        selected = registry.select(engine_ids)
    except KeyError as exc:
        raise ValueError(str(exc).strip("'\"")) from exc
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for engine in selected:
        try:
            detected = bool(engine.detect(context))
        except Exception as exc:  # pragma: no cover - defensive boundary
            detected = False
            errors.append(f"{engine.engine_id}: detect: {type(exc).__name__}")
        try:
            capabilities = {name: capability.to_dict()
                            for name, capability in engine.capabilities(context).items()}
        except Exception as exc:  # pragma: no cover - defensive boundary
            capabilities = {}
            errors.append(f"{engine.engine_id}: capabilities: {type(exc).__name__}")
        entries.append({
            "engine_id": engine.engine_id,
            "engine_version": engine.engine_version,
            "display_name": engine.display_name,
            "category": engine.category,
            "language_keys": list(engine.language_keys),
            "detected": detected,
            "capabilities": capabilities,
            "health": engine.health(),
        })
    return {
        "schema": "ogx-engine-inspection-1",
        "tool": "OMEGA GUARDIAN X",
        "engine_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {"name": profile.name, "root": profile.root,
                    "languages": profile.languages, "ecosystems": profile.ecosystems,
                    "file_count": len(workspace), "total_bytes": workspace.total_bytes},
        "project_id": context.project_id,
        "fingerprint": fingerprint,
        "analysis_mode": "static-read-only",
        "execution_performed": False,
        "network_access": False,
        "engines": entries,
        "detected_engines": [entry["engine_id"] for entry in entries if entry["detected"]],
        "warnings": _dedupe_strings(workspace.warnings),
        "errors": _dedupe_strings(errors),
    }


def analyze_android(root: str | Any, config: ScanConfig | None = None) -> dict[str, Any]:
    workspace, profile, context, fingerprint = _context(root, config)
    engine = AndroidEngine()
    result = engine.evaluate(context)
    findings = _unified_findings([result] if result.detected else [], context.project_id)
    analysis_facts = result.facts.get("analyze", {}).get("facts", {})
    if not isinstance(analysis_facts, dict):
        analysis_facts = {}
    return {
        "schema": "ogx-android-report-1",
        "tool": "OMEGA GUARDIAN X",
        "engine_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_id": context.project_id,
        "project": {"name": profile.name, "root": profile.root,
                    "languages": profile.languages, "frameworks": profile.frameworks},
        "fingerprint": fingerprint,
        "analysis_mode": "static-read-only",
        "execution_performed": False,
        "network_access": False,
        "detected": result.detected,
        "engine": _engine_summary(result),
        "capabilities": {name: capability.to_dict() for name, capability in result.capabilities.items()},
        "detection": result.facts.get("detection", {}),
        "facts": analysis_facts,
        "operations": {name: value for name, value in result.facts.items()
                       if name not in {"detection", "analyze"}},
        "findings": [finding.to_dict() for finding in findings],
        "summary": severity_summary(findings),
        "warnings": _dedupe_strings(list(workspace.warnings) + result.warnings),
        "errors": list(result.errors),
        "limits": [
            "No Gradle build, SDK tool, adb command, emulator or device was executed.",
            "Binary AndroidManifest.xml inside APK/AAB was not decoded.",
            "APK/AAB signatures were not cryptographically verified.",
            "Gradle/AGP/Kotlin/SDK compatibility is declared from files, not validated by a build.",
            "Source/resource checks are pattern and XML-attribute heuristics, not full Android Lint.",
        ],
    }


def _engine_summary(result: EngineResult) -> dict[str, Any]:
    return {
        "engine_id": result.engine_id,
        "engine_version": result.engine_version,
        "display_name": result.display_name,
        "detected": result.detected,
        "duration_ms": result.duration_ms,
        "mode": result.mode,
        "execution_performed": result.execution_performed,
        "capabilities": {name: capability.to_dict() for name, capability in result.capabilities.items()},
        "facts": result.facts,
        "finding_count": len(result.findings),
        "warnings": result.warnings,
        "errors": result.errors,
    }


def _capability_summary(results: list[EngineResult]) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    engines: dict[str, dict[str, str]] = {}
    for result in results:
        engines[result.engine_id] = {}
        for name in CAPABILITY_NAMES:
            capability = result.capabilities.get(name)
            status = capability.status.value if capability else "unsupported"
            engines[result.engine_id][name] = status
            counts.setdefault(name, {})
            counts[name][status] = counts[name].get(status, 0) + 1
    return {"by_capability": counts, "by_engine": engines}


def _unified_findings(results: list[EngineResult], project_id: str) -> list[UnifiedFinding]:
    unified: list[UnifiedFinding] = []
    for result in results:
        for finding in result.findings:
            state = "observed" if finding.rule_id in OBSERVED_RULES else "inferred"
            unified.append(unify(finding, project_id=project_id,
                                 engine_id=result.engine_id,
                                 engine_version=result.engine_version,
                                 verification_status=state))
    return _cross_engine_dedupe(unified)


def _cross_engine_dedupe(findings: list[UnifiedFinding]) -> list[UnifiedFinding]:
    merged: dict[tuple[str, str | None, int | None, str], UnifiedFinding] = {}
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    for finding in findings:
        key = (finding.rule_id, finding.path, finding.line, finding.description)
        existing = merged.get(key)
        if existing is None:
            merged[key] = finding
            continue
        if (severity_rank.get(finding.severity, 9), -confidence_rank.get(finding.confidence, 0)) < (
                severity_rank.get(existing.severity, 9), -confidence_rank.get(existing.confidence, 0)):
            finding.also_reported_by = sorted({existing.engine_id, *existing.also_reported_by})
            finding.finding_id = existing.finding_id
            merged[key] = finding
        else:
            existing.also_reported_by = sorted({*existing.also_reported_by, finding.engine_id})
    return dedupe(list(merged.values()))


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def render_universal_markdown(report: dict[str, Any]) -> str:
    project = report["project"]
    lines = [
        "# OMEGA GUARDIAN X — Universal Analysis Report", "",
        f"- **Project:** `{project['name']}`",
        f"- **Project ID:** `{report['project_id']}`",
        f"- **Root:** `{project['root']}`",
        f"- **Engine version:** `{report['engine_version']}`",
        f"- **Generated:** {report['generated_at']}",
        f"- **Files indexed:** {project['file_count']} ({project['total_bytes']} bytes)",
        f"- **Detected engines:** {len(report['detected_engines'])} of {len(report['engines'])}",
        f"- **Mode:** {report['analysis_mode']} (no code executed, no network access)", "",
        "## Severity summary", "", "| Severity | Count |", "|---|---:|",
    ]
    lines.extend(f"| {severity.title()} | {count} |" for severity, count in report["summary"].items())
    lines += ["", "## Detected technology engines", "",
              "| Engine | Version | Analyze | Dependencies | Build | Test | Findings |",
              "|---|---|---|---|---|---|---:|"]
    for engine in report["engines"]:
        if not engine["detected"]:
            continue
        caps = engine["capabilities"]
        lines.append("| {id} | {version} | {analyze} | {dependencies} | {build} | {test} | {count} |".format(
            id=engine["engine_id"], version=engine["engine_version"],
            analyze=caps.get("analyze", {}).get("status", "unsupported"),
            dependencies=caps.get("dependencies", {}).get("status", "unsupported"),
            build=caps.get("build", {}).get("status", "unsupported"),
            test=caps.get("test", {}).get("status", "unsupported"),
            count=engine["finding_count"],
        ))
    lines += ["", "## Findings", ""]
    if not report["findings"]:
        lines.append("No findings were produced by the enabled engines. This is not a guarantee that the project is secure.")
    else:
        lines += ["| Severity | Rule | Engine | Location | Confidence | Title |",
                  "|---|---|---|---|---|---|"]
        for finding in report["findings"]:
            location = finding["path"] or "repository"
            if finding["line"]:
                location += f":{finding['line']}"
            engines = finding["engine_id"]
            if finding["also_reported_by"]:
                engines += ", " + ", ".join(finding["also_reported_by"])
            lines.append("| {severity} | `{rule}` | {engine} | `{location}` | {confidence} | {title} |".format(
                severity=finding["severity"], rule=finding["rule_id"], engine=engines,
                location=str(location).replace("|", "\\|"),
                confidence=finding["confidence"], title=finding["title"].replace("|", "\\|")))
    if report["warnings"] or report["errors"]:
        lines += ["", "## Warnings and errors", ""]
        lines.extend(f"- WARNING: {item}" for item in report["warnings"])
        lines.extend(f"- ERROR: {item}" for item in report["errors"])
    lines += ["", "## Capability boundaries", ""]
    lines.extend(f"- {item}" for item in report["limits"])
    lines += ["", "> Engine detection and capability status are not a certification of language, framework or security coverage.", ""]
    return "\n".join(lines)


def render_android_markdown(report: dict[str, Any]) -> str:
    facts = report.get("facts", {})
    lines = [
        "# OMEGA GUARDIAN X — Android Report", "",
        f"- **Project:** `{report['project']['name']}`",
        f"- **Project ID:** `{report['project_id']}`",
        f"- **Android detected:** {report['detected']}",
        f"- **Engine version:** `{report['engine_version']}`",
        f"- **Mode:** static-read-only; no build/device operation", "",
        "## Capability status", "", "| Operation | Status | Detail |", "|---|---|---|",
    ]
    for name, capability in report["capabilities"].items():
        lines.append(f"| {name} | {capability['status']} | {capability['detail'].replace('|', '/')} |")
    manifests = facts.get("manifests") or []
    lines += ["", "## Manifest summary", ""]
    if not manifests:
        lines.append("No AndroidManifest.xml was discovered or parsed.")
    for manifest in manifests:
        lines += [
            f"### `{manifest['path']}`", "",
            f"- Package: `{manifest.get('package')}`",
            f"- minSdk: `{manifest.get('min_sdk')}`; targetSdk: `{manifest.get('target_sdk')}`",
            f"- Permissions: {len(manifest.get('permissions', []))} "
            f"(dangerous: {len(manifest.get('dangerous_permissions', []))}, special: {len(manifest.get('special_permissions', []))})",
            f"- Components: {len(manifest.get('components', []))}; deep links: {len(manifest.get('deep_links', []))}",
            f"- Cleartext traffic: `{manifest.get('application', {}).get('uses_cleartext_traffic')}`; "
            f"debuggable: `{manifest.get('application', {}).get('debuggable')}`",
        ]
        dangerous = manifest.get("dangerous_permissions") or []
        if dangerous:
            lines.append(f"- Dangerous permission names: {', '.join(f'`{item}`' for item in dangerous[:20])}")
        special = manifest.get("special_permissions") or []
        if special:
            lines.append(f"- Special permission names: {', '.join(f'`{item}`' for item in special[:20])}")
    gradle = facts.get("gradle") or {}
    if gradle:
        wrapper = gradle.get("gradle_wrapper") or {}
        lines += [
            "", "## Gradle summary", "",
            f"- Gradle files analyzed: {len(gradle.get('gradle_files') or gradle.get('files') or [])}",
            f"- AGP: `{gradle.get('agp_version')}`; Kotlin: `{gradle.get('kotlin_version')}`",
            f"- compileSdk: `{gradle.get('compile_sdk')}`; minSdk: `{gradle.get('min_sdk')}`; targetSdk: `{gradle.get('target_sdk')}`",
            f"- Namespace: `{gradle.get('namespace')}`; applicationId: `{gradle.get('application_id')}`",
            f"- Declared dependencies: {len(gradle.get('dependencies') or [])}; conflicts: {len(gradle.get('conflicts') or {})}",
            f"- Compose enabled: {gradle.get('compose_enabled')}; product flavors: {', '.join(gradle.get('product_flavors') or []) or 'none detected'}",
            f"- Gradle wrapper: `{wrapper.get('distribution_version')}`; checksum pinned: {wrapper.get('checksum_pinned')}",
            f"- Signing configs detected: {gradle.get('signing_configs_present')}",
        ]
        if gradle.get("conflicts"):
            lines.append("- Version conflicts: " + "; ".join(
                f"{name} ({', '.join(versions)})" for name, versions in list(gradle["conflicts"].items())[:20]))
    environment = facts.get("environment") or {}
    if environment:
        lines += [
            "", "## Local Android environment", "",
            f"- SDK detected: {environment.get('sdk_detected')}",
            f"- JDK present: {environment.get('jdk_present')}; Gradle present: {environment.get('gradle_present')}; "
            f"wrapper present: {environment.get('wrapper_present')}; adb present: {environment.get('adb_present')}",
            f"- Build execution: `{environment.get('build_execution')}`; device operations: `{environment.get('device_operations')}`",
        ]
    resources = facts.get("resources") or {}
    if resources:
        lines += ["", "## Resources and accessibility", "",
                  f"- Layout files: {resources.get('layout_count')}; assets: {resources.get('asset_count')}",
                  f"- Accessibility candidates: {len(resources.get('accessibility_candidates') or [])}"]
    sources = facts.get("sources") or {}
    if sources:
        lines += ["", "## Source overview", "",
                  f"- Kotlin/Java source files analyzed: {sources.get('source_file_count')}",
                  f"- Compose files: {sources.get('compose_files')}; WebView-related files: {sources.get('webview_files')}"]
    artifacts = facts.get("artifacts") or []
    if artifacts:
        lines += ["", "## APK/AAB metadata", "",
                  "| Artifact | Kind | Bytes | ZIP valid | v1 signature entries | Signature verified |",
                  "|---|---|---:|---|---|---|"]
        for artifact in artifacts:
            lines.append(f"| `{artifact['path']}` | {artifact['kind']} | {artifact['size_bytes']} | "
                         f"{artifact['zip_valid']} | {len(artifact.get('v1_signature_entries') or [])} | false |")
    lines += ["", "## Findings", ""]
    if not report["findings"]:
        lines.append("No Android-specific findings were produced. This is not a security clearance.")
    else:
        lines += ["| Severity | Rule | Location | Confidence | Title |", "|---|---|---|---|---|"]
        for finding in report["findings"]:
            location = finding["path"] or "repository"
            if finding["line"]:
                location += f":{finding['line']}"
            lines.append(f"| {finding['severity']} | `{finding['rule_id']}` | `{location}` | "
                         f"{finding['confidence']} | {finding['title'].replace('|', '/')} |")
    if report["warnings"] or report["errors"]:
        lines += ["", "## Warnings and errors", ""]
        lines.extend(f"- WARNING: {item}" for item in report["warnings"])
        lines.extend(f"- ERROR: {item}" for item in report["errors"])
    lines += ["", "## Boundaries", ""]
    lines.extend(f"- {item}" for item in report["limits"])
    lines += ["", "> No APK build, APK signature verification, emulator, device, adb command or project script was executed.", ""]
    return "\n".join(lines)
