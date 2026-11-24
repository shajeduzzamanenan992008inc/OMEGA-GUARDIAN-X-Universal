"""Dedicated Android platform engine.

Scope (static, read-only, no device or build execution):

* project/manifest/Gradle/R8/resource/source discovery
* dangerous and special permission review
* exported component and deep-link review
* network security configuration review
* WebView and insecure-storage pattern review
* Gradle dependency declaration conflicts and AndroidX/support mixing
* APK/AAB ZIP metadata and v1 signature presence (not cryptographic proof)
* test/build capability discovery with explicit toolchain requirements

The engine never invokes Gradle, the Android SDK, adb, an emulator, or a
device.  It does not decode binary AndroidManifest.xml or verify APK
signatures cryptographically.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..models import Finding
from ..rules import redact_evidence
from ..workspace import Workspace, WorkspaceError
from .build import android_environment, discover_builds, discover_tests
from .contract import BaseEngine, Capability, CapabilityStatus, EngineContext
from .language import (
    Component, c_like_balanced, gradle_dependencies, secret_findings,
    secure_coding_findings,
)

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
TOOLS_NS = "{http://schemas.android.com/tools}"
MAX_GRADLE_FILES = 80
MAX_MANIFESTS = 20
MAX_SOURCE_FILES = 1500
MAX_LAYOUTS = 300
MAX_ARTIFACTS = 20
MAX_FINDINGS = 500
MAX_ARTIFACT_HASH_BYTES = 64_000_000
MAX_GRADLE_BYTES = 32_000_000
MAX_SOURCE_BYTES = 64_000_000
MAX_RESOURCE_BYTES = 32_000_000

DANGEROUS_PERMISSIONS = {
    "CAMERA", "RECORD_AUDIO", "READ_CONTACTS", "WRITE_CONTACTS", "GET_ACCOUNTS",
    "ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION",
    "READ_CALL_LOG", "WRITE_CALL_LOG", "CALL_PHONE", "READ_PHONE_STATE",
    "READ_PHONE_NUMBERS", "ANSWER_PHONE_CALLS", "PROCESS_OUTGOING_CALLS",
    "READ_SMS", "RECEIVE_SMS", "SEND_SMS", "RECEIVE_MMS", "RECEIVE_WAP_PUSH",
    "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE", "READ_MEDIA_IMAGES",
    "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO", "BODY_SENSORS", "ACTIVITY_RECOGNITION",
    "BLUETOOTH_SCAN", "BLUETOOTH_CONNECT", "BLUETOOTH_ADVERTISE", "POST_NOTIFICATIONS",
    "NEARBY_WIFI_DEVICES", "READ_CALENDAR", "WRITE_CALENDAR", "USE_EXACT_ALARM",
    "ACCESS_MEDIA_LOCATION",
}

SPECIAL_PERMISSIONS = {
    "SYSTEM_ALERT_WINDOW", "MANAGE_EXTERNAL_STORAGE", "REQUEST_INSTALL_PACKAGES",
    "QUERY_ALL_PACKAGES", "PACKAGE_USAGE_STATS", "SCHEDULE_EXACT_ALARM",
    "BIND_ACCESSIBILITY_SERVICE", "BIND_NOTIFICATION_LISTENER_SERVICE",
    "BIND_VPN_SERVICE", "REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
}

SOURCE_CHECKS: tuple[tuple[str, str, re.Pattern[str], str, str, str], ...] = (
    ("OGX-ANDROID-WEBVIEW-001", "WebView JavaScript enabled",
     re.compile(r"\.setJavaScriptEnabled\s*\(\s*true\s*\)"), "medium",
     "JavaScript is enabled in a WebView; risk depends on loaded content and navigation controls.",
     "Load only trusted HTTPS content, restrict navigation, and keep the WebView implementation updated."),
    ("OGX-ANDROID-WEBVIEW-002", "JavaScript bridge exposed to WebView content",
     re.compile(r"\.addJavascriptInterface\s*\("), "high",
     "A JavaScript bridge is added to WebView content; untrusted pages may reach native methods.",
     "Avoid bridges for untrusted content; restrict origins, validate every argument, and expose minimal annotated methods."),
    ("OGX-ANDROID-WEBVIEW-003", "WebView universal file access enabled",
     re.compile(r"\.setAllowUniversalAccessFromFileURLs\s*\(\s*true\s*\)"), "high",
     "Universal access from file URLs can expose local files to WebView content.",
     "Keep this disabled and load local assets through a reviewed, constrained mechanism."),
    ("OGX-ANDROID-WEBVIEW-004", "WebView file access enabled",
     re.compile(r"\.setAllowFileAccess\s*\(\s*true\s*\)"), "medium",
     "File access is explicitly enabled in a WebView.",
     "Keep file access disabled unless a reviewed offline-asset flow requires it."),
    ("OGX-ANDROID-WEBVIEW-005", "WebView content debugging enabled",
     re.compile(r"\.setWebContentsDebuggingEnabled\s*\(\s*true\s*\)"), "medium",
     "WebView content debugging is enabled explicitly.",
     "Gate debugging to non-release builds and remove it from production."),
    ("OGX-ANDROID-STORAGE-001", "World-accessible storage mode",
     re.compile(r"MODE_WORLD_(?:READABLE|WRITEABLE)"), "high",
     "A legacy world-readable/world-writable file mode is used.",
     "Use MODE_PRIVATE, scoped storage, and an approved encrypted storage mechanism."),
    ("OGX-ANDROID-STORAGE-002", "Shared external storage path",
     re.compile(r"getExternalStoragePublicDirectory|Environment\.getExternalStorageDirectory"), "medium",
     "A deprecated shared external-storage path is used.",
     "Use app-scoped storage or the Storage Access Framework with least-privilege permissions."),
    ("OGX-ANDROID-LOG-001", "Credential-like value in a log statement",
     re.compile(r"(?i)\bLog\.[dviwe]\s*\([^)\n]{0,200}\b(?:token|password|passwd|secret|api[_-]?key|session[_-]?id)\b"), "medium",
     "A log statement references a credential-like identifier.",
     "Do not log secrets or session identifiers; use structured, redacted diagnostics."),
)

INSECURE_MAVEN = re.compile(r"""maven\s*\{[^}]{0,500}?url\s*=?\s*(?:uri\()?\s*["'](http://[^"']+)["']""", re.DOTALL)
SIGNING_SECRET = re.compile(r"""(?i)\b(storePassword|keyPassword)\s*[=:]?\s*["']([^"']+)["']""")
GRADLE_DEP = re.compile(
    r"(?m)^\s*(implementation|api|compileOnly|runtimeOnly|kapt|ksp|annotationProcessor|"
    r"testImplementation|androidTestImplementation|debugImplementation)\s*\(?\s*"
    r"[\"']([^:'\"]+):([^:'\"]+):([^\"']+)[\"']"
)


def _safe_url(value: str) -> str:
    """Remove URL user-info and common token query values from evidence."""
    text = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1<redacted>@", value)
    return re.sub(
        r"(?i)([?&](?:token|api[_-]?key|secret|password|sig|signature)=)[^&\s]+",
        lambda match: f"{match.group(1)}<redacted>",
        text,
    )


def _af(rule_id: str, title: str, severity: str, category: str, message: str,
        path: str | None = None, line: int | None = None, evidence: str | None = None,
        recommendation: str | None = None, confidence: str = "medium") -> Finding:
    if evidence:
        evidence = _safe_url(redact_evidence(evidence))
        evidence = re.sub(
            r"(?i)(storePassword|keyPassword)(\s*[=:]\s*)(['\"]?)[^'\"\s]+",
            lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}<redacted>",
            evidence,
        )
    return Finding(rule_id, title, severity, category, message, path, line, evidence,
                   recommendation, confidence)


def _attr(element, name: str) -> str | None:
    return element.attrib.get(ANDROID_NS + name) or element.attrib.get(name)


def _bool_attr(element, name: str) -> bool | None:
    value = _attr(element, name)
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read(workspace: Workspace, rel: str, limit: int = 1_500_000) -> str | None:
    entry = workspace.entry(rel)
    if entry is None or entry.size > limit:
        return None
    try:
        return workspace.read_text(rel, max_bytes=limit)
    except WorkspaceError:
        return None


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(severity, 5)


class AndroidEngine(BaseEngine):
    engine_id = "android"
    engine_version = "1.0.0"
    display_name = "Android platform engine"
    language_keys = ("android",)
    category = "mobile"

    # -- discovery --------------------------------------------------------
    def _manifests(self, context: EngineContext) -> list[str]:
        return [entry.path for entry in context.workspace.files if entry.path.endswith("AndroidManifest.xml")][:MAX_MANIFESTS]

    def _gradle_files(self, context: EngineContext) -> list[str]:
        names = {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
        files = [entry.path for entry in context.workspace.files
                 if Path(entry.path).name in names or entry.path.endswith("gradle/libs.versions.toml")]
        return files[:MAX_GRADLE_FILES]

    def _plugin_markers(self, context: EngineContext) -> list[str]:
        markers: list[str] = []
        for rel in self._gradle_files(context):
            text = _read(context.workspace, rel, limit=600_000)
            if text and re.search(r"com\.android\.(?:application|library|dynamic-feature|test)", text):
                markers.append(rel)
            if len(markers) >= 10:
                break
        return markers

    def detect(self, context: EngineContext) -> bool:
        return bool(self._manifests(context) or self._plugin_markers(context))

    def _signals(self, context: EngineContext) -> dict[str, Any]:
        return {
            "manifests": len(self._manifests(context)),
            "gradle_files": len(self._gradle_files(context)),
            "android_plugins": self._plugin_markers(context)[:10],
            "artifacts": [entry.path for entry in context.workspace.files
                          if entry.path.lower().endswith((".apk", ".aab"))][:10],
        }

    def _environment(self, context: EngineContext) -> dict[str, Any]:
        env = android_environment()
        wrappers = [rel for rel in ("gradlew", "gradlew.bat") if context.workspace.exists(rel)]
        wrappers.extend(entry.path for entry in context.workspace.glob("**/gradlew*", limit=10)
                        if Path(entry.path).name in {"gradlew", "gradlew.bat"})
        wrappers = sorted(set(wrappers))[:10]
        env["wrapper_files"] = wrappers
        env["wrapper_present"] = bool(wrappers)
        env["build_ready"] = bool(env["sdk_detected"] and env["jdk_present"]
                                  and (env["gradle_present"] or wrappers))
        env["build_execution"] = "not_configured"
        env["device_operations"] = "not_attempted"
        return env

    # -- capabilities -----------------------------------------------------
    def capabilities(self, context: EngineContext) -> dict[str, Capability]:
        env = self._environment(context)
        manifests = self._manifests(context)
        gradle = self._gradle_files(context)
        tests = discover_tests(context.workspace, ["Java", "Kotlin"])
        test_found = any(item.get("discovered") for item in tests.get("languages", {}).values())
        if env["build_ready"]:
            build_status = CapabilityStatus.PARTIAL
            build_detail = "Build discovery only; SDK/JDK/wrapper signals are present but nothing was executed."
        else:
            missing = [name for name in ("sdk_detected", "jdk_present") if not env.get(name)]
            if not env["gradle_present"] and not env["wrapper_present"]:
                missing.append("gradle_or_wrapper")
            build_status = CapabilityStatus.NOT_CONFIGURED
            build_detail = "Missing local capability: " + ", ".join(missing) + ". No build or device operation was attempted."
        return {
            "detect": Capability(CapabilityStatus.SUPPORTED if manifests or gradle else CapabilityStatus.UNSUPPORTED,
                                 f"{len(manifests)} manifest(s), {len(gradle)} Gradle file(s)."),
            "analyze": Capability(
                CapabilityStatus.SUPPORTED,
                "Static manifest/Gradle/resource/source analysis is implemented. Binary manifest decoding, APK signature verification and runtime checks are not performed.",
            ),
            "dependencies": Capability(
                CapabilityStatus.PARTIAL,
                "Declared Gradle/Maven dependencies and conflict detection; no resolution, transitive graph or vulnerability query.",
            ),
            "build": Capability(build_status, build_detail),
            "test": Capability(
                CapabilityStatus.PARTIAL if test_found else CapabilityStatus.NOT_CONFIGURED,
                "Unit/instrumentation test files discovered; no test, emulator or device operation was executed."
                if test_found else "No conventional Android test sources were discovered; execution was not attempted.",
            ),
            "diagnose": Capability(
                CapabilityStatus.PARTIAL,
                "Static project diagnostics and existing Android Lint XML ingestion; build-log diagnosis and runtime logs are not covered.",
            ),
        }

    def health(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "version": self.engine_version,
            "status": "ok",
            "mode": "static-read-only",
            "execution_performed": False,
            "network_access": False,
            "device_operations": "not_attempted",
            "limitations": [
                "Binary AndroidManifest.xml (inside APK/AAB) is not decoded.",
                "APK/AAB signatures are not cryptographically verified.",
                "Gradle, AGP, Kotlin and SDK compatibility is reported from declarations, not built or tested.",
                "No emulator, device, adb, APK install or instrumentation test is attempted.",
            ],
        }

    # -- analysis ---------------------------------------------------------
    def analyze(self, context: EngineContext) -> dict[str, Any]:
        workspace = context.workspace
        findings: list[Finding] = []
        warnings: list[str] = []
        facts: dict[str, Any] = {
            "environment": self._environment(context),
            "manifests": [],
            "gradle": [],
            "resources": {},
            "sources": {},
            "artifacts": [],
            "limitations": [],
        }

        for rel in self._manifests(context):
            manifest_facts, manifest_findings, manifest_warnings = self._analyze_manifest(context, rel)
            facts["manifests"].append(manifest_facts)
            findings.extend(manifest_findings)
            warnings.extend(manifest_warnings)

        gradle_model = self._analyze_gradle(context)
        facts["gradle"] = gradle_model["facts"]
        findings.extend(gradle_model["findings"])
        warnings.extend(gradle_model["warnings"])

        resources = self._analyze_resources(context)
        facts["resources"] = resources["facts"]
        findings.extend(resources["findings"])
        warnings.extend(resources["warnings"])

        sources = self._analyze_sources(context)
        facts["sources"] = sources["facts"]
        findings.extend(sources["findings"])
        warnings.extend(sources["warnings"])

        artifacts = self._analyze_artifacts(context)
        facts["artifacts"] = artifacts["facts"]
        findings.extend(artifacts["findings"])
        warnings.extend(artifacts["warnings"])

        facts["tests"] = self.test(context)
        facts["build"] = self.build(context)
        facts["limitations"] = self.health()["limitations"] + [
            "APK/AAB artifacts are detected only when present outside default-excluded build directories.",
        ]
        findings = _dedupe_findings(findings)
        if len(findings) > MAX_FINDINGS:
            warnings.append(f"Findings truncated to {MAX_FINDINGS} for the Android engine.")
            findings = findings[:MAX_FINDINGS]
        return {"facts": facts, "findings": findings, "warnings": warnings}

    # -- manifest ---------------------------------------------------------
    def _analyze_manifest(self, context: EngineContext, rel: str) -> tuple[dict[str, Any], list[Finding], list[str]]:
        workspace = context.workspace
        findings: list[Finding] = []
        warnings: list[str] = []
        facts: dict[str, Any] = {
            "path": rel, "package": None, "min_sdk": None, "target_sdk": None,
            "permissions": [], "dangerous_permissions": [], "special_permissions": [],
            "components": [], "deep_links": [], "application": {},
            "network_security_config": None,
        }
        try:
            root = workspace.xml(rel)
        except WorkspaceError as exc:
            findings.append(_af(
                "OGX-ANDROID-MANIFEST-001", "Android manifest could not be parsed", "high", "android-manifest",
                f"The manifest was rejected: {exc}", rel,
                recommendation="Fix the XML and DTD/entity usage; verify the merged manifest with Android tooling.",
                confidence="high"))
            return facts, findings, warnings

        if _local(root.tag) != "manifest":
            findings.append(_af(
                "OGX-ANDROID-MANIFEST-002", "Unexpected manifest root element", "medium", "android-manifest",
                "The AndroidManifest.xml root element is not <manifest>.", rel,
                recommendation="Verify that the file is an Android manifest and not a different XML document.",
                confidence="high"))
            return facts, findings, warnings

        facts["package"] = root.attrib.get("package")
        application = next((child for child in root if _local(child.tag) == "application"), None)
        uses_sdk = next((child for child in root if _local(child.tag) == "uses-sdk"), None)
        if uses_sdk is not None:
            facts["min_sdk"] = _attr(uses_sdk, "minSdkVersion")
            facts["target_sdk"] = _attr(uses_sdk, "targetSdkVersion")

        permissions: list[str] = []
        for child in root:
            if _local(child.tag) not in {"uses-permission", "uses-permission-sdk-23"}:
                continue
            name = _attr(child, "name") or ""
            normalized = name.removeprefix("android.permission.")
            if normalized:
                permissions.append(normalized)
        facts["permissions"] = sorted(set(permissions))
        dangerous = sorted(set(permissions) & DANGEROUS_PERMISSIONS)
        special = sorted(set(permissions) & SPECIAL_PERMISSIONS)
        facts["dangerous_permissions"] = dangerous
        facts["special_permissions"] = special
        if dangerous:
            findings.append(_af(
                "OGX-ANDROID-PERM-001", "Dangerous runtime permissions requested", "medium", "android-permissions",
                "The app requests permissions that can access sensitive user data or capabilities. Verify each permission is necessary and handled at runtime.",
                rel, evidence=", ".join(dangerous[:20]),
                recommendation="Apply least privilege, request at use time, explain purpose, and remove unused permissions.",
                confidence="high"))
        if special:
            findings.append(_af(
                "OGX-ANDROID-PERM-002", "Special or policy-sensitive permissions requested", "high", "android-permissions",
                "The app requests permissions that are restricted or require specific store policy review.",
                rel, evidence=", ".join(special[:20]),
                recommendation="Verify policy eligibility, documented use, and store declaration requirements before release.",
                confidence="high"))

        app_facts: dict[str, Any] = {}
        if application is not None:
            app_facts = {
                "debuggable": _bool_attr(application, "debuggable"),
                "allow_backup": _bool_attr(application, "allowBackup"),
                "uses_cleartext_traffic": _bool_attr(application, "usesCleartextTraffic"),
                "network_security_config": _attr(application, "networkSecurityConfig"),
                "full_backup_content": _attr(application, "fullBackupContent"),
                "data_extraction_rules": _attr(application, "dataExtractionRules"),
            }
            if app_facts["debuggable"] is True:
                findings.append(_af(
                    "OGX-ANDROID-MANIFEST-003", "Application is debuggable", "high", "android-manifest",
                    "android:debuggable=true allows debugging and can expose app internals. Release builds must not be debuggable.",
                    rel, recommendation="Remove android:debuggable or keep it limited to debug build variants.",
                    confidence="high"))
            if app_facts["uses_cleartext_traffic"] is True:
                findings.append(_af(
                    "OGX-ANDROID-NET-001", "Cleartext network traffic allowed", "high", "android-network",
                    "android:usesCleartextTraffic=true permits unencrypted HTTP traffic.",
                    rel, recommendation="Require HTTPS and use a network security configuration that denies cleartext for production domains.",
                    confidence="high"))
            if app_facts["allow_backup"] is not False:
                findings.append(_af(
                    "OGX-ANDROID-BACKUP-001", "Application backup not explicitly disabled", "low", "android-privacy",
                    "android:allowBackup is missing or true. Backup behavior should be a deliberate data-protection decision.",
                    rel, recommendation="Set android:allowBackup=false or define narrowly scoped backup/data-extraction rules for sensitive data.",
                    confidence="medium"))
            nsc = app_facts["network_security_config"]
            if nsc:
                nsc_facts, nsc_findings = self._analyze_network_security_config(context, rel, nsc)
                facts["network_security_config"] = nsc_facts
                findings.extend(nsc_findings)
        facts["application"] = app_facts

        if application is not None:
            for child in application:
                kind = _local(child.tag)
                if kind not in {"activity", "activity-alias", "service", "receiver", "provider"}:
                    continue
                name = _attr(child, "name") or "<unnamed>"
                exported = _bool_attr(child, "exported")
                permission = _attr(child, "permission")
                intent_filters = [item for item in child if _local(item.tag) == "intent-filter"]
                actions = {(_attr(action, "name") or "").rsplit(".", 1)[-1] for item in intent_filters
                           for action in item if _local(action.tag) == "action"}
                categories = {(_attr(category, "name") or "").rsplit(".", 1)[-1] for item in intent_filters
                              for category in item if _local(category.tag) == "category"}
                is_launcher = "MAIN" in actions and "LAUNCHER" in categories
                component = {
                    "type": kind, "name": name, "exported": exported, "permission": permission,
                    "intent_filters": len(intent_filters), "launcher": is_launcher,
                }
                facts["components"].append(component)
                if exported is True and not permission and not is_launcher:
                    findings.append(_af(
                        "OGX-ANDROID-EXPORT-001", "Exported component without permission", "high", "android-components",
                        f"An exported {kind} can be started by other applications without a component-level permission.",
                        rel, evidence=f"{kind} {name}",
                        recommendation="Set android:exported=false unless external invocation is required; protect required entry points with a permission.",
                        confidence="medium"))
                if exported is True and kind == "provider" and _bool_attr(child, "grantUriPermissions") is True:
                    findings.append(_af(
                        "OGX-ANDROID-EXPORT-003", "Exported provider grants URI permissions", "high", "android-components",
                        "An exported ContentProvider also grants URI permissions, which can widen data access.",
                        rel, evidence=f"provider {name}",
                        recommendation="Restrict grantUriPermissions, validate caller identity, and expose the narrowest possible paths.",
                        confidence="medium"))
                if exported is None and intent_filters and not is_launcher:
                    findings.append(_af(
                        "OGX-ANDROID-EXPORT-002", "Exported flag missing on component with intent filter", "medium", "android-components",
                        "A component with an intent filter does not declare android:exported explicitly. Modern target SDKs require this for components with intent filters.",
                        rel, evidence=f"{kind} {name}",
                        recommendation="Declare android:exported explicitly and review whether external invocation is intended.",
                        confidence="high"))
                for intent_filter in intent_filters:
                    actions_full = {(_attr(item, "name") or "") for item in intent_filter if _local(item.tag) == "action"}
                    categories_full = {(_attr(item, "name") or "") for item in intent_filter if _local(item.tag) == "category"}
                    data = [item for item in intent_filter if _local(item.tag) == "data"]
                    if "android.intent.action.VIEW" not in actions_full or "android.intent.category.BROWSABLE" not in categories_full:
                        continue
                    for data_item in data:
                        scheme = _attr(data_item, "scheme") or ""
                        host = _attr(data_item, "host") or ""
                        if scheme not in {"http", "https"}:
                            continue
                        auto_verify = _bool_attr(intent_filter, "autoVerify") is True
                        link = {"component": name, "scheme": scheme, "host": host, "auto_verify": auto_verify,
                                "exported": exported}
                        facts["deep_links"].append(link)
                        if exported is not False and not auto_verify:
                            findings.append(_af(
                                "OGX-ANDROID-DEEPLINK-001", "HTTP deep link without app-link verification", "medium", "android-deeplink",
                                "An exported HTTP(S) deep link does not enable autoVerify, so ownership of the host is not verified by Android App Links.",
                                rel, evidence=f"{scheme}://{host} via {name}",
                                recommendation="Enable android:autoVerify with a hosted assetlinks.json, and validate all deep-link parameters.",
                                confidence="high"))

        min_sdk = _int_or_none(facts["min_sdk"])
        target_sdk = _int_or_none(facts["target_sdk"])
        if target_sdk is not None and target_sdk < 33:
            findings.append(_af(
                "OGX-ANDROID-SDK-001", "Target SDK is below 33", "low", "android-sdk",
                f"targetSdkVersion is {target_sdk}. Review current distribution and platform security requirements; modern targets enforce stricter exported-component and notification behavior.",
                rel, recommendation="Evaluate a target SDK upgrade with regression and behavioral testing.",
                confidence="high"))
        if min_sdk is not None and min_sdk < 24:
            findings.append(_af(
                "OGX-ANDROID-SDK-002", "Very low minimum SDK", "low", "android-sdk",
                f"minSdkVersion is {min_sdk}; very old platform versions increase compatibility and security-maintenance burden.",
                rel, recommendation="Review the supported OS range and raise minSdk if the product allows it.",
                confidence="high"))
        if min_sdk is not None and target_sdk is not None and min_sdk > target_sdk:
            findings.append(_af(
                "OGX-ANDROID-SDK-003", "minSdkVersion exceeds targetSdkVersion", "high", "android-sdk",
                "The declared minimum SDK is greater than the target SDK, which is an invalid compatibility configuration.",
                rel, recommendation="Correct the SDK values and validate the merged manifest/build configuration.",
                confidence="high"))
        return facts, findings, warnings

    def _analyze_network_security_config(self, context: EngineContext, manifest_rel: str,
                                         reference: str) -> tuple[dict[str, Any], list[Finding]]:
        findings: list[Finding] = []
        name = reference.split("/", 1)[-1]
        candidates = [entry.path for entry in context.workspace.glob(f"**/res/xml/{name}.xml", limit=20)]
        facts: dict[str, Any] = {"reference": reference, "path": None, "cleartext_permitted": None,
                                 "user_ca_trusted": False, "debug_overrides": False}
        if not candidates:
            findings.append(_af(
                "OGX-ANDROID-NSC-003", "Network security config reference not found", "medium", "android-network",
                f"The manifest references {reference}, but no matching res/xml/{name}.xml was found.",
                manifest_rel, recommendation="Add the referenced configuration or remove the dangling reference; verify the merged resource output.",
                confidence="medium"))
            return facts, findings
        rel = candidates[0]
        facts["path"] = rel
        try:
            root = context.workspace.xml(rel)
        except WorkspaceError as exc:
            findings.append(_af(
                "OGX-ANDROID-NSC-004", "Network security config could not be parsed", "medium", "android-network",
                f"The network security configuration was rejected: {exc}", rel,
                recommendation="Fix the XML/DTD usage and validate the configuration with Android tooling.",
                confidence="high"))
            return facts, findings
        base = next((child for child in root if _local(child.tag) == "base-config"), None)
        if base is not None:
            cleartext = base.attrib.get("cleartextTrafficPermitted")
            facts["cleartext_permitted"] = cleartext
            if str(cleartext).lower() == "true":
                findings.append(_af(
                    "OGX-ANDROID-NSC-001", "Cleartext traffic permitted by network security config", "high", "android-network",
                    "The base network security configuration permits cleartext traffic.",
                    rel, recommendation="Deny cleartext by default and narrowly scope any debug-only exception.",
                    confidence="high"))
        def visit(element, in_debug: bool = False) -> None:
            local = _local(element.tag)
            inside_debug = in_debug or local == "debug-overrides"
            if local == "debug-overrides":
                facts["debug_overrides"] = True
            if local == "certificates" and element.attrib.get("src") == "user" and not inside_debug:
                facts["user_ca_trusted"] = True
            for child in element:
                visit(child, inside_debug)

        visit(root)
        if facts["user_ca_trusted"]:
            findings.append(_af(
                "OGX-ANDROID-NSC-002", "User CA certificates trusted in production configuration", "medium", "android-network",
                "The network security configuration trusts user-installed CAs outside a debug-only override.",
                rel, recommendation="Trust only system CAs in production; keep user-CA trust in debug-overrides if required.",
                confidence="medium"))
        return facts, findings

    # -- gradle -----------------------------------------------------------
    def _analyze_gradle(self, context: EngineContext) -> dict[str, Any]:
        workspace = context.workspace
        findings: list[Finding] = []
        warnings: list[str] = []
        facts: dict[str, Any] = {
            "files": [], "android_plugins": [], "agp_version": None, "kotlin_version": None,
            "compile_sdk": None, "min_sdk": None, "target_sdk": None,
            "namespace": None, "application_id": None, "version_code": None, "version_name": None,
            "minify_enabled": [], "shrink_resources": [], "test_instrumentation_runner": None,
            "compose_enabled": False, "product_flavors": [], "flavor_dimensions": [],
            "signing_configs_present": False, "signing_config_references": [],
            "repositories_insecure": [], "jcenter": False, "dependencies": [], "conflicts": {},
            "androidx_support_mix": False, "support_library": False, "dynamic_versions": [],
            "gradle_wrapper": None, "gradle_properties": {}, "version_catalog": None,
            "proguard": [], "lint_abort_on_error_false": False,
        }
        components: list[Component] = []
        gradle_bytes = 0
        for rel in self._gradle_files(context):
            entry = workspace.entry(rel)
            if entry is not None and gradle_bytes + entry.size > MAX_GRADLE_BYTES:
                warnings.append(f"Gradle analysis stopped at the {MAX_GRADLE_BYTES}-byte budget.")
                break
            text = _read(workspace, rel)
            if text is None:
                warnings.append(f"Gradle file exceeded the read limit: {rel}")
                continue
            if entry is not None:
                gradle_bytes += entry.size
            facts["files"].append(rel)
            if re.search(r"com\.android\.(?:application|library|dynamic-feature|test)", text):
                facts["android_plugins"].append(rel)
            agp = re.search(r"com\.android\.tools\.build:gradle:([0-9][0-9A-Za-z.\-]*)", text)
            if not agp:
                agp = re.search(r"id\s*\(\s*[\"']com\.android\.(?:application|library)[\"']\s*\)\s*version\s*[\"']([^\"']+)[\"']", text)
            if agp and not facts["agp_version"]:
                facts["agp_version"] = agp.group(1)
            kotlin = re.search(r"org\.jetbrains\.kotlin\.(?:android|jvm)[\"']?\s*\)?\s*version\s*[\"']([^\"']+)[\"']", text)
            if kotlin and not facts["kotlin_version"]:
                facts["kotlin_version"] = kotlin.group(1)
            for key, pattern in (
                ("compile_sdk", r"\bcompileSdk(?:Version)?\s*=?\s*(\d+)"),
                ("min_sdk", r"\bminSdk(?:Version)?\s*=?\s*(\d+)"),
                ("target_sdk", r"\btargetSdk(?:Version)?\s*=?\s*(\d+)"),
            ):
                match = re.search(pattern, text)
                if match and facts[key] is None:
                    facts[key] = int(match.group(1))
            namespace = re.search(r"\bnamespace\s*=?\s*[\"']([^\"']+)[\"']", text)
            if namespace and not facts["namespace"]:
                facts["namespace"] = namespace.group(1)
            application_id = re.search(r"\bapplicationId\s*=?\s*[\"']([^\"']+)[\"']", text)
            if application_id and not facts["application_id"]:
                facts["application_id"] = application_id.group(1)
            version_code = re.search(r"\bversionCode\s*=?\s*(\d+)", text)
            if version_code and facts["version_code"] is None:
                facts["version_code"] = int(version_code.group(1))
            version_name = re.search(r"\bversionName\s*=?\s*[\"']([^\"']+)[\"']", text)
            if version_name and not facts["version_name"]:
                facts["version_name"] = version_name.group(1)
            for match in re.finditer(r"\b(?:isMinifyEnabled|minifyEnabled)\s*=?\s*(true|false)", text):
                facts["minify_enabled"].append(match.group(1) == "true")
            for match in re.finditer(r"\b(?:isShrinkResources|shrinkResources)\s*=?\s*(true|false)", text):
                facts["shrink_resources"].append(match.group(1) == "true")
            runner = re.search(r"\btestInstrumentationRunner\s*=?\s*[\"']([^\"']+)[\"']", text)
            if runner and not facts["test_instrumentation_runner"]:
                facts["test_instrumentation_runner"] = runner.group(1)
            if re.search(r"\bcompose\s*=?\s*true", text) or re.search(r"buildFeatures\s*\{[^}]{0,500}compose\s*=?\s*true", text, re.DOTALL):
                facts["compose_enabled"] = True
            flavors = re.search(r"productFlavors\s*\{([\s\S]{0,5000})", text)
            if flavors:
                for match in re.finditer(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{", flavors.group(1)):
                    if match.group(1) not in {"create", "register"}:
                        facts["product_flavors"].append(match.group(1))
            dimensions = re.search(r"flavorDimensions\s*=?\s*[\"']?([^\"'\n]+)", text)
            if dimensions:
                facts["flavor_dimensions"].extend(item.strip().strip("\"',") for item in dimensions.group(1).split(",") if item.strip())
            if "signingConfigs" in text:
                facts["signing_configs_present"] = True
            for match in re.finditer(r"signingConfig\s*=?\s*(?:signingConfigs\.)?([A-Za-z0-9_.]+)", text):
                facts["signing_config_references"].append(match.group(1))
            for match in SIGNING_SECRET.finditer(text):
                value = match.group(2)
                if value and not re.search(r"(?i)your[_-]|change[_-]?me|example|placeholder|<", value):
                    findings.append(_af(
                        "OGX-ANDROID-SIGN-001", "Literal signing credential in Gradle", "high", "android-signing",
                        "A signing password appears to be a literal value in a build file. Secret values are redacted from this report.",
                        rel, evidence=f"{match.group(1)}=<redacted>",
                        recommendation="Move signing credentials to environment variables or a secure CI secret store immediately; rotate if exposed.",
                        confidence="medium"))
            for match in INSECURE_MAVEN.finditer(text):
                insecure_url = _safe_url(match.group(1))
                facts["repositories_insecure"].append({"file": rel, "url": insecure_url})
                findings.append(_af(
                    "OGX-ANDROID-GRADLE-REPO-001", "Insecure Maven repository URL", "high", "android-supply-chain",
                    "A Gradle repository is declared over plain HTTP, enabling artifact tampering.",
                    rel, evidence=insecure_url,
                    recommendation="Use HTTPS with a pinned, trusted repository and verify dependency integrity.",
                    confidence="high"))
            if "allowInsecureProtocol" in text and re.search(r"allowInsecureProtocol\s*=?\s*true", text):
                findings.append(_af(
                    "OGX-ANDROID-GRADLE-REPO-002", "Insecure repository protocol explicitly allowed", "high", "android-supply-chain",
                    "A repository allows insecure protocols.", rel,
                    recommendation="Remove allowInsecureProtocol and use a verified HTTPS repository.",
                    confidence="high"))
            if "jcenter()" in text:
                facts["jcenter"] = True
            if re.search(r"lintOptions\s*\{[^}]{0,1000}abortOnError\s*=?\s*false", text, re.DOTALL) or re.search(r"lint\s*\{[^}]{0,1000}abortOnError\s*=?\s*false", text, re.DOTALL):
                facts["lint_abort_on_error_false"] = True
                findings.append(_af(
                    "OGX-ANDROID-LINT-001", "Android Lint abortOnError disabled", "medium", "android-quality",
                    "Lint failures are configured not to fail the build.", rel,
                    recommendation="Enable abortOnError for release/CI builds and review suppressions explicitly.",
                    confidence="high"))

            for component in gradle_dependencies(text, rel):
                components.append(component)
                if component.constraint.endswith("+"):
                    facts["dynamic_versions"].append(component.to_dict())
            if re.search(r"(?m)^\s*compile\s+[\"']", text):
                findings.append(_af(
                    "OGX-ANDROID-DEP-005", "Deprecated Gradle compile configuration", "low", "android-dependencies",
                    "The deprecated `compile` configuration is used for a dependency.", rel,
                    recommendation="Use implementation/api with the appropriate visibility.", confidence="high"))
            if re.search(r"com\.android\.support:", text):
                facts["support_library"] = True
            if re.search(r"androidx\.", text) and re.search(r"com\.android\.support:", text):
                facts["androidx_support_mix"] = True
                findings.append(_af(
                    "OGX-ANDROID-DEP-003", "AndroidX and legacy support libraries mixed", "high", "android-dependencies",
                    "The project declares both AndroidX and legacy com.android.support artifacts.",
                    rel, recommendation="Complete the AndroidX migration and remove legacy support libraries.",
                    confidence="high"))
            if re.search(r"com\.android\.support:", text):
                findings.append(_af(
                    "OGX-ANDROID-DEP-004", "Legacy Android support library", "medium", "android-dependencies",
                    "Legacy com.android.support artifacts are deprecated; they can conflict with AndroidX and miss fixes.",
                    rel, recommendation="Migrate to AndroidX and verify behavior with a reviewed build.",
                    confidence="high"))

        facts["dependencies"] = [component.to_dict() for component in _dedupe_components(components)]
        conflicts = _dependency_conflicts(components)
        facts["conflicts"] = conflicts
        for name, versions in conflicts.items():
            findings.append(_af(
                "OGX-ANDROID-DEP-001", "Gradle dependency declared with conflicting versions", "medium", "android-dependencies",
                f"Dependency '{name}' is declared with multiple version constraints; the resolved version is not determined by this static scan.",
                facts["files"][0] if facts["files"] else None,
                evidence=f"{name}: {', '.join(versions[:8])}",
                recommendation="Align versions through a version catalog or dependency constraints, then verify with Gradle dependency insight in an approved environment.",
                confidence="medium"))
        if facts["dynamic_versions"]:
            findings.append(_af(
                "OGX-ANDROID-DEP-002", "Dynamic Gradle dependency version", "medium", "android-dependencies",
                "At least one dependency uses a dynamic '+' version, which can change without a source change.",
                facts["dynamic_versions"][0]["source"],
                evidence=", ".join(item["name"] + ":" + item["constraint"] for item in facts["dynamic_versions"][:10]),
                recommendation="Pin versions through a reviewed catalog and update them with dependency automation.",
                confidence="high"))

        compile_sdk = facts["compile_sdk"]
        target_sdk = facts["target_sdk"]
        min_sdk = facts["min_sdk"]
        if compile_sdk and target_sdk and target_sdk > compile_sdk:
            findings.append(_af(
                "OGX-ANDROID-SDK-004", "targetSdkVersion exceeds compileSdkVersion", "high", "android-sdk",
                f"targetSdk {target_sdk} is greater than compileSdk {compile_sdk}.", facts["files"][0] if facts["files"] else None,
                recommendation="Compile against an SDK at least as new as the target SDK.", confidence="high"))
        if min_sdk and target_sdk and min_sdk > target_sdk:
            findings.append(_af(
                "OGX-ANDROID-SDK-005", "minSdkVersion exceeds targetSdkVersion", "high", "android-sdk",
                f"minSdk {min_sdk} is greater than targetSdk {target_sdk}.", facts["files"][0] if facts["files"] else None,
                recommendation="Correct the SDK configuration and validate the merged build model.", confidence="high"))
        if facts["minify_enabled"] and not any(facts["minify_enabled"]):
            findings.append(_af(
                "OGX-ANDROID-R8-001", "Code shrinking/minification not enabled", "low", "android-build",
                "All discovered minifyEnabled flags are false. This can increase APK size and leave more readable bytecode.",
                facts["files"][0] if facts["files"] else None,
                recommendation="Enable R8/minification for release builds and run regression tests against the minified build.",
                confidence="medium"))
        if facts["jcenter"]:
            findings.append(_af(
                "OGX-ANDROID-GRADLE-REPO-003", "Deprecated jcenter repository", "low", "android-supply-chain",
                "The jcenter repository is referenced; it is deprecated and should not be relied upon for new builds.",
                facts["files"][0] if facts["files"] else None,
                recommendation="Replace jcenter with the maintained repositories required by each dependency.",
                confidence="high"))
        if facts["lint_abort_on_error_false"]:
            warnings.append("Android Lint is configured with abortOnError=false.")

        wrapper_facts, wrapper_findings = self._analyze_wrapper(context)
        facts["gradle_wrapper"] = wrapper_facts
        findings.extend(wrapper_findings)
        properties_facts, properties_findings = self._analyze_gradle_properties(context)
        facts["gradle_properties"] = properties_facts
        findings.extend(properties_findings)
        proguard_facts, proguard_findings = self._analyze_proguard(context)
        facts["proguard"] = proguard_facts
        findings.extend(proguard_findings)
        catalog_facts, catalog_findings = self._analyze_version_catalog(context)
        facts["version_catalog"] = catalog_facts
        findings.extend(catalog_findings)
        facts["signing_configs_present"] = bool(facts["signing_configs_present"])
        if facts["android_plugins"] and not facts["signing_configs_present"]:
            findings.append(_af(
                "OGX-ANDROID-SIGN-002", "Release signing configuration not detected", "info", "android-signing",
                "No signingConfigs block was detected. Signing may be intentionally external (CI or store signing).",
                facts["files"][0] if facts["files"] else None,
                recommendation="Document and verify the release signing path; never commit keystores or passwords.",
                confidence="low"))
        return {"facts": facts, "findings": findings, "warnings": warnings}

    def _analyze_wrapper(self, context: EngineContext) -> tuple[dict[str, Any], list[Finding]]:
        findings: list[Finding] = []
        candidates = [entry.path for entry in context.workspace.files
                      if entry.path.endswith("gradle/wrapper/gradle-wrapper.properties")]
        facts: dict[str, Any] = {"properties": None, "distribution_url": None,
                                 "distribution_version": None, "https": None,
                                 "checksum_pinned": False, "jar_present": False,
                                 "wrapper_scripts": []}
        facts["wrapper_scripts"] = [rel for rel in ("gradlew", "gradlew.bat") if context.workspace.exists(rel)]
        facts["jar_present"] = any(entry.path.endswith("gradle/wrapper/gradle-wrapper.jar")
                                   for entry in context.workspace.files)
        if not candidates:
            return facts, findings
        rel = candidates[0]
        facts["properties"] = rel
        text = _read(context.workspace, rel, limit=200_000) or ""
        url = re.search(r"(?m)^\s*distributionUrl\s*=\s*(.+)$", text)
        if url:
            distribution = _safe_url(url.group(1).strip())
            facts["distribution_url"] = distribution
            facts["https"] = distribution.lower().startswith("https://")
            version = re.search(r"gradle-(\d+\.\d+(?:\.\d+)?)-(?:all|bin)\.zip", distribution)
            if version:
                facts["distribution_version"] = version.group(1)
            if not facts["https"]:
                findings.append(_af(
                    "OGX-ANDROID-GRADLE-001", "Gradle distribution downloaded over insecure HTTP", "high", "android-supply-chain",
                    "gradle-wrapper.properties uses a non-HTTPS distribution URL.", rel,
                    evidence=distribution,
                    recommendation="Use the official HTTPS Gradle distribution and pin its SHA-256 checksum.",
                    confidence="high"))
            if facts["distribution_version"]:
                parts = tuple(int(part) for part in facts["distribution_version"].split(".") if part.isdigit())
                if parts and parts[0] < 8:
                    findings.append(_af(
                        "OGX-ANDROID-GRADLE-004", "Older Gradle distribution", "low", "android-build",
                        f"Gradle {facts['distribution_version']} is old. Verify compatibility with the declared Android Gradle Plugin and current security fixes.",
                        rel, recommendation="Plan a reviewed Gradle/AGP upgrade; do not change versions blindly.",
                        confidence="high"))
        facts["checksum_pinned"] = bool(re.search(r"(?m)^\s*distributionSha256Sum\s*=\s*[0-9a-fA-F]{64}\s*$", text))
        if facts["distribution_url"] and not facts["checksum_pinned"]:
            findings.append(_af(
                "OGX-ANDROID-GRADLE-002", "Gradle distribution checksum not pinned", "low", "android-supply-chain",
                "The wrapper distribution URL is present without distributionSha256Sum. Checksum pinning improves supply-chain integrity.",
                rel, recommendation="Pin the published distribution SHA-256 checksum where the Gradle version supports it.",
                confidence="medium"))
        return facts, findings

    def _analyze_gradle_properties(self, context: EngineContext) -> tuple[dict[str, Any], list[Finding]]:
        findings: list[Finding] = []
        if not context.workspace.exists("gradle.properties"):
            return {"present": False}, findings
        rel = "gradle.properties"
        text = _read(context.workspace, rel, limit=500_000) or ""
        properties: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "!")) or "=" not in line:
                continue
            key, value = line.split("=", 1)
            properties[key.strip()] = value.strip()
        facts: dict[str, Any] = {"present": True, "android_use_androidx": properties.get("android.useAndroidX")}
        if str(properties.get("android.useAndroidX", "")).lower() == "false":
            findings.append(_af(
                "OGX-ANDROID-GRADLE-005", "AndroidX disabled in gradle.properties", "medium", "android-build",
                "android.useAndroidX=false conflicts with modern AndroidX dependencies.", rel,
                recommendation="Complete the AndroidX migration and verify the dependency graph.",
                confidence="high"))
        jvmargs = properties.get("org.gradle.jvmargs", "")
        memory = re.search(r"-Xmx(\d+)([mMgG])", jvmargs)
        if memory:
            amount = int(memory.group(1)) * (1024 if memory.group(2).lower() == "g" else 1)
            facts["gradle_max_heap_mb"] = amount
            if amount > 8192:
                findings.append(_af(
                    "OGX-ANDROID-GRADLE-006", "Very large Gradle heap request", "info", "android-build",
                    f"org.gradle.jvmargs requests roughly {amount} MB maximum heap. Verify this is required for the target machines.",
                    rel, recommendation="Tune memory for the actual build environment and CI runner capacity.",
                    confidence="high"))
        facts["properties"] = {key: ("<redacted>" if re.search(r"(?i)password|secret|token|key", key) else value)
                               for key, value in properties.items()}
        return facts, findings

    def _analyze_proguard(self, context: EngineContext) -> tuple[list[dict[str, Any]], list[Finding]]:
        findings: list[Finding] = []
        files = [entry.path for entry in context.workspace.files
                 if Path(entry.path).name in {"proguard-rules.pro", "proguard.cfg"} or entry.path.endswith(".pro")][:20]
        facts: list[dict[str, Any]] = []
        for rel in files:
            text = _read(context.workspace, rel, limit=1_000_000) or ""
            rule_count = sum(1 for line in text.splitlines() if line.strip().startswith("-"))
            item = {"path": rel, "rule_count": rule_count,
                    "dont_obfuscate": bool(re.search(r"(?m)^\s*-dontobfuscate\b", text)),
                    "dont_shrink": bool(re.search(r"(?m)^\s*-dontshrink\b", text)),
                    "dont_optimize": bool(re.search(r"(?m)^\s*-dontoptimize\b", text)),
                    "broad_keep": bool(re.search(r"(?m)^\s*-keep\s+class\s+\*\s*(?:\{\s*\*\s*;\s*\})?\s*$", text))}
            facts.append(item)
            if item["dont_obfuscate"]:
                findings.append(_af(
                    "OGX-ANDROID-R8-003", "R8 obfuscation disabled", "low", "android-build",
                    "proguard-rules.pro disables obfuscation, which can make reverse engineering easier.",
                    rel, recommendation="Verify why obfuscation is disabled and narrow the rules if possible.",
                    confidence="high"))
            if item["broad_keep"]:
                findings.append(_af(
                    "OGX-ANDROID-R8-002", "Overly broad ProGuard/R8 keep rule", "low", "android-build",
                    "A keep rule preserves all classes and members, reducing shrinking/obfuscation benefits.",
                    rel, recommendation="Replace the broad rule with targeted keep rules justified by reflection/serialization.",
                    confidence="medium"))
        return facts, findings

    def _analyze_version_catalog(self, context: EngineContext) -> tuple[dict[str, Any] | None, list[Finding]]:
        candidates = [entry.path for entry in context.workspace.files
                      if entry.path.endswith("gradle/libs.versions.toml")]
        if not candidates:
            return None, []
        rel = candidates[0]
        findings: list[Finding] = []
        try:
            data = context.workspace.toml(rel)
        except (WorkspaceError, ValueError) as exc:
            findings.append(_af(
                "OGX-ANDROID-CATALOG-001", "Version catalog could not be parsed", "medium", "android-build",
                f"The Gradle version catalog was rejected: {type(exc).__name__}", rel,
                recommendation="Fix the TOML and validate the catalog with Gradle in an approved environment.",
                confidence="high"))
            return {"path": rel, "parsed": False}, findings
        versions = data.get("versions", {})
        libraries = data.get("libraries", {})
        facts = {
            "path": rel,
            "parsed": True,
            "version_count": len(versions) if isinstance(versions, dict) else 0,
            "library_count": len(libraries) if isinstance(libraries, dict) else 0,
            "dynamic_versions": [str(value) for value in (versions.values() if isinstance(versions, dict) else [])
                                 if isinstance(value, str) and value.endswith("+")][:20],
        }
        if facts["dynamic_versions"]:
            findings.append(_af(
                "OGX-ANDROID-DEP-006", "Dynamic version in Gradle version catalog", "medium", "android-dependencies",
                "A version catalog entry uses a dynamic '+' version.", rel,
                evidence=", ".join(facts["dynamic_versions"]),
                recommendation="Pin versions and use dependency automation for updates.",
                confidence="high"))
        return facts, findings

    # -- resources --------------------------------------------------------
    def _analyze_resources(self, context: EngineContext) -> dict[str, Any]:
        workspace = context.workspace
        findings: list[Finding] = []
        warnings: list[str] = []
        layouts = [entry.path for entry in workspace.glob("**/src/main/res/layout/*.xml", limit=MAX_LAYOUTS + 1)]
        if len(layouts) > MAX_LAYOUTS:
            warnings.append(f"Layout analysis limited to {MAX_LAYOUTS} files.")
            layouts = layouts[:MAX_LAYOUTS]
        res_files = [entry for entry in workspace.files if "/src/main/res/" in f"/{entry.path}"]
        assets = [entry for entry in workspace.files if "/src/main/assets/" in f"/{entry.path}"]
        by_type: defaultdict[str, int] = defaultdict(int)
        for entry in res_files:
            parts = entry.path.split("/src/main/res/", 1)
            if len(parts) == 2:
                by_type[parts[1].split("/", 1)[0]] += 1
        large_assets = [{"path": entry.path, "size_bytes": entry.size}
                        for entry in assets if entry.size > 5_000_000][:20]
        if large_assets:
            findings.append(_af(
                "OGX-ANDROID-ASSET-001", "Large asset bundled in the app", "info", "android-size",
                "One or more assets exceed 5 MB, which can affect download size and startup/storage costs.",
                large_assets[0]["path"], evidence=", ".join(f"{item['path']}={item['size_bytes']}" for item in large_assets[:5]),
                recommendation="Compress, downscale, or deliver large media through a reviewed content-delivery strategy.",
                confidence="high"))
        a11y: list[dict[str, Any]] = []
        resource_bytes = 0
        for rel in layouts:
            entry = workspace.entry(rel)
            if entry is not None and resource_bytes + entry.size > MAX_RESOURCE_BYTES:
                warnings.append(f"Layout analysis stopped at the {MAX_RESOURCE_BYTES}-byte budget.")
                break
            text = _read(workspace, rel, limit=context.config.max_file_bytes)
            if text is None:
                continue
            if entry is not None:
                resource_bytes += entry.size
            try:
                root = workspace.xml(rel)
            except WorkspaceError:
                continue
            for element in root.iter():
                tag = _local(element.tag)
                if not tag.endswith(("ImageView", "ImageButton")):
                    continue
                if _attr(element, "contentDescription") is not None:
                    continue
                if (_attr(element, "importantForAccessibility") or "").lower() == "no":
                    continue
                if (element.attrib.get(TOOLS_NS + "ignore") or "").find("ContentDescription") >= 0:
                    continue
                a11y.append({"path": rel, "element": tag, "id": _attr(element, "id")})
                if len(a11y) >= 50:
                    break
            if len(a11y) >= 50:
                break
        if a11y:
            findings.append(_af(
                "OGX-ANDROID-A11Y-001", "Image element without contentDescription", "low", "android-accessibility",
                "One or more ImageView/ImageButton elements lack contentDescription and are not marked decorative. Some may be decorative; verify with accessibility testing.",
                a11y[0]["path"], evidence=", ".join(f"{item['path']}:{item['element']}" for item in a11y[:10]),
                recommendation="Add meaningful contentDescription values or mark decorative images as not important for accessibility.",
                confidence="low"))
        facts = {
            "layout_count": len(layouts),
            "resource_file_count": len(res_files),
            "asset_count": len(assets),
            "resource_types": dict(sorted(by_type.items())),
            "large_assets": large_assets,
            "accessibility_candidates": a11y,
            "limitations": ["Layout checks are XML/attribute heuristics; no rendering or accessibility-tree test is performed."],
        }
        return {"facts": facts, "findings": findings, "warnings": warnings}

    # -- sources ----------------------------------------------------------
    def _analyze_sources(self, context: EngineContext) -> dict[str, Any]:
        workspace = context.workspace
        findings: list[Finding] = []
        warnings: list[str] = []
        candidates = [entry.path for entry in workspace.files
                      if Path(entry.path).suffix.lower() in {".kt", ".java"}
                      and "/src/main/" in f"/{entry.path}"][:MAX_SOURCE_FILES]
        compose_files = 0
        compose_clickables = 0
        webviews = 0
        skipped = 0
        source_bytes = 0
        for rel in candidates:
            entry = workspace.entry(rel)
            if entry is None or entry.size > context.config.max_file_bytes:
                skipped += 1
                continue
            if source_bytes + entry.size > MAX_SOURCE_BYTES:
                warnings.append(f"Source analysis stopped at the {MAX_SOURCE_BYTES}-byte budget.")
                break
            source_bytes += entry.size
            try:
                text = workspace.read_text(rel)
            except WorkspaceError:
                skipped += 1
                continue
            findings.extend(secret_findings(rel, text))
            findings.extend(secure_coding_findings(rel, text))
            for rule_id, title, pattern, severity, message, recommendation in SOURCE_CHECKS:
                for match in pattern.finditer(text):
                    evidence = match.group(0)[:160]
                    if rule_id == "OGX-ANDROID-LOG-001":
                        evidence = "credential-like identifier in a log call"
                    findings.append(_af(rule_id, title, severity, "android-source", message, rel,
                                        text.count("\n", 0, match.start()) + 1, evidence,
                                        recommendation, "medium"))
            if "@Composable" in text:
                compose_files += 1
                for match in re.finditer(r"\.clickable\s*(?:\(|\{)", text):
                    compose_clickables += 1
                    if "contentDescription" not in text and "semantics" not in text:
                        findings.append(_af(
                            "OGX-ANDROID-A11Y-003", "Clickable Compose element without semantics hint", "info", "android-accessibility",
                            "A clickable Compose modifier was found in a file without contentDescription/semantics. This is a heuristic; text children may already provide accessibility.",
                            rel, text.count("\n", 0, match.start()) + 1,
                            "Modifier.clickable",
                            "Verify with accessibility testing; add semantics/contentDescription where the target is not described by text.",
                            "low"))
                        break
            if "WebView" in text:
                webviews += 1
            if not c_like_balanced(text):
                warnings.append(f"Possible unbalanced delimiters in {rel}; run the native compiler/linter for confirmation.")
        facts = {
            "source_file_count": len(candidates),
            "source_files_skipped": skipped,
            "compose_files": compose_files,
            "compose_clickable_occurrences": compose_clickables,
            "webview_files": webviews,
            "limitations": ["Source checks are pattern-based; no type resolution, taint analysis or Compose compiler is used."],
        }
        return {"facts": facts, "findings": findings, "warnings": warnings}

    # -- artifacts --------------------------------------------------------
    def _analyze_artifacts(self, context: EngineContext) -> dict[str, Any]:
        workspace = context.workspace
        findings: list[Finding] = []
        warnings: list[str] = []
        entries = [entry.path for entry in workspace.files if entry.path.lower().endswith((".apk", ".aab"))][:MAX_ARTIFACTS]
        facts: list[dict[str, Any]] = []
        for rel in entries:
            entry = workspace.entry(rel)
            if entry is None:
                continue
            artifact: dict[str, Any] = {
                "path": rel, "kind": Path(rel).suffix.lower().lstrip("."),
                "size_bytes": entry.size, "sha256": None, "zip_valid": False,
                "entry_count": None, "v1_signature_entries": [], "manifest_entries": [],
                "bundle_config": False, "signature_verified": False,
                "limitations": ["Binary AndroidManifest.xml is not decoded.",
                                "v2/v3/v4 signature blocks are not inspected or verified."],
            }
            if entry.size <= MAX_ARTIFACT_HASH_BYTES:
                try:
                    artifact["sha256"] = workspace.sha256(rel)
                except (WorkspaceError, OSError):
                    pass
            else:
                warnings.append(f"Skipped SHA-256 for artifact above {MAX_ARTIFACT_HASH_BYTES} bytes: {rel}")
            if entry.size > 250_000_000:
                warnings.append(f"Artifact too large for ZIP metadata inspection: {rel}")
                facts.append(artifact)
                continue
            try:
                path = workspace.readable_path(rel)
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                artifact["zip_valid"] = True
                artifact["entry_count"] = len(names)
                artifact["v1_signature_entries"] = [name for name in names
                                                    if name.startswith("META-INF/") and name.upper().endswith((".RSA", ".DSA", ".EC"))][:20]
                artifact["manifest_entries"] = [name for name in names
                                                if name.endswith("AndroidManifest.xml")][:20]
                artifact["bundle_config"] = "BundleConfig.pb" in names
            except (zipfile.BadZipFile, OSError, WorkspaceError) as exc:
                findings.append(_af(
                    "OGX-ANDROID-APK-002", "Artifact is not a readable ZIP/APK/AAB", "medium", "android-artifact",
                    f"The artifact could not be inspected as a ZIP archive: {type(exc).__name__}", rel,
                    recommendation="Regenerate the artifact and verify its checksum and signature in an approved build environment.",
                    confidence="high"))
                facts.append(artifact)
                continue
            if artifact["kind"] == "apk" and not artifact["v1_signature_entries"]:
                findings.append(_af(
                    "OGX-ANDROID-APK-001", "APK signing metadata not detected", "medium", "android-artifact",
                    "No APK v1 signature entries were detected in META-INF. APKs normally require a signature to install; this check does not verify v2/v3 blocks.",
                    rel, recommendation="Verify the signing configuration and run apksigner in an approved environment before distribution.",
                    confidence="low"))
            facts.append(artifact)
        return {"facts": facts, "findings": findings, "warnings": warnings}

    # -- build / test / diagnose -----------------------------------------
    def build(self, context: EngineContext) -> dict[str, Any]:
        discovered = [item for item in discover_builds(context.workspace)
                      if item["adapter_id"] == "android-gradle"]
        env = self._environment(context)
        return {
            "discovered": bool(discovered) or bool(self._gradle_files(context)),
            "adapters": discovered,
            "environment": env,
            "executed": False,
            "execution_status": "not_configured",
            "note": ("Android build discovery only. The Gradle wrapper, Gradle, Android SDK, adb, "
                     "emulator and device were not executed or contacted."),
        }

    def test(self, context: EngineContext) -> dict[str, Any]:
        result = discover_tests(context.workspace, ["Java", "Kotlin"])
        units = [entry.path for entry in context.workspace.files
                 if "/src/test/" in f"/{entry.path}" and Path(entry.path).suffix.lower() in {".kt", ".java"}][:50]
        instrumentation = [entry.path for entry in context.workspace.files
                           if "/src/androidTest/" in f"/{entry.path}" and Path(entry.path).suffix.lower() in {".kt", ".java"}][:50]
        gradle_text = "\n".join((_read(context.workspace, rel) or "") for rel in self._gradle_files(context)[:20])
        frameworks = [name for name, pattern in (
            ("JUnit", r"junit"), ("Espresso", r"espresso"), ("Robolectric", r"robolectric"),
            ("Compose UI Test", r"compose.*ui.*test"), ("Mockito", r"mockito"),
        ) if re.search(pattern, gradle_text, re.IGNORECASE)]
        runner = re.search(r"\btestInstrumentationRunner\s*=?\s*[\"']([^\"']+)[\"']", gradle_text)
        return {
            **result,
            "unit_test_files": units,
            "instrumentation_test_files": instrumentation,
            "test_instrumentation_runner": runner.group(1) if runner else None,
            "candidate_frameworks": frameworks,
            "executed": False,
            "execution_status": "not_configured",
            "device_operations": "not_attempted",
            "note": "Test discovery only; no unit test, instrumentation test, emulator or device was run.",
        }

    def diagnose(self, context: EngineContext) -> dict[str, Any]:
        candidates = [entry.path for entry in context.workspace.glob("**/lint-results*.xml", limit=10)]
        diagnostics: list[dict[str, Any]] = []
        for rel in candidates[:5]:
            try:
                root = context.workspace.xml(rel)
            except WorkspaceError:
                continue
            for issue in root.iter():
                if _local(issue.tag) != "issue":
                    continue
                location = next((child for child in issue if _local(child.tag) == "location"), None)
                diagnostics.append({
                    "source": rel,
                    "id": issue.attrib.get("id"),
                    "severity": issue.attrib.get("severity"),
                    "message": (issue.attrib.get("message") or "")[:500],
                    "file": location.attrib.get("file") if location is not None else None,
                    "line": location.attrib.get("line") if location is not None else None,
                })
                if len(diagnostics) >= 200:
                    break
        return {
            "diagnostics": diagnostics,
            "lint_reports": candidates,
            "runtime_logs": "not_analyzed",
            "build_logs": "not_analyzed",
            "note": ("Existing Android Lint XML was imported when present. "
                     "No build or device diagnostic was executed."),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _dedupe_components(components: list[Component]) -> list[Component]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[Component] = []
    for component in components:
        key = (component.ecosystem, component.name.lower(), component.constraint, component.source)
        if key in seen:
            continue
        seen.add(key)
        result.append(component)
    return result


def _dependency_conflicts(components: list[Component]) -> dict[str, list[str]]:
    versions: defaultdict[str, set[str]] = defaultdict(set)
    for component in components:
        if component.constraint:
            versions[component.name].add(component.constraint)
    return {name: sorted(values) for name, values in sorted(versions.items()) if len(values) > 1}


def _dedupe_findings(findings: Iterable[Finding]) -> list[Finding]:
    unique: dict[tuple[str, str | None, int | None, str], Finding] = {}
    for finding in findings:
        key = (finding.rule_id, finding.path, finding.line, finding.message)
        existing = unique.get(key)
        if existing is None or _severity_rank(finding.severity) < _severity_rank(existing.severity):
            unique[key] = finding
    return sorted(unique.values(), key=lambda item: (
        _severity_rank(item.severity), item.path or "", item.line or 0, item.rule_id))
