# Android Engine — Static Analysis Contract

## 1. Purpose

The Android engine is a first-class platform engine, not a language sidecar. It performs bounded, local, read-only inspection of Android project structure, manifests, Gradle configuration, resources, Kotlin/Java sources, tests and APK/AAB ZIP metadata.

It does **not** build, install, sign, run, emulate or contact a device.

## 2. Current environment status

Validated in this workspace on 2026-11-14:

| Requirement | Observed |
|---|---|
| JDK | Present (`java` on PATH) |
| Android SDK | Not detected |
| Standalone Gradle | Not installed |
| Gradle wrapper scripts | Not present in the validation fixture |
| `adb` | Not installed |
| `sdkmanager` | Not installed |
| `emulator` | Not installed |
| Android build | `not_configured` |
| Device/emulator operations | `not_attempted` |

Capability negotiation reports `build: not_configured` with the missing capabilities. This is deliberate. The engine must not claim that a build or device test passed when no Android toolchain is available.

## 3. Detection

The engine is detected when it finds:

- a file ending in `AndroidManifest.xml`; or
- a Gradle file containing an Android Gradle Plugin marker such as
  `com.android.application`, `com.android.library`, `com.android.dynamic-feature`,
  or `com.android.test`.

Detection is independent of the Python/JavaScript/Java/Kotlin language engines, so an Android project is analyzed even when its language engine is unavailable.

## 4. Static analysis scope

### 4.1 Manifest

- package, `minSdkVersion`, `targetSdkVersion`;
- dangerous and special/policy-sensitive permissions;
- explicit/implicit `android:exported` state;
- components: activity, activity-alias, service, receiver, provider;
- launcher components are recognized and not reported as unsafe exports solely for being launchers;
- HTTP(S) deep links and `autoVerify`;
- cleartext traffic, debuggable, backup, network-security-config reference;
- provider URI-permission grants;
- invalid SDK combinations;
- malformed XML and DTD/entity rejection.

Rule families:

- `OGX-ANDROID-MANIFEST-*`
- `OGX-ANDROID-PERM-*`
- `OGX-ANDROID-EXPORT-*`
- `OGX-ANDROID-DEEPLINK-*`
- `OGX-ANDROID-NET-*`
- `OGX-ANDROID-SDK-*`

### 4.2 Network security configuration

- resolves `@xml/<name>` under `**/res/xml/<name>.xml`;
- reports cleartext permission, production user-CA trust, missing references and parse failures;
- distinguishes `debug-overrides` from production trust.

Rules: `OGX-ANDROID-NSC-001` through `004`.

### 4.3 Gradle, R8 and supply chain

- Gradle/AGP/Kotlin version declarations where statically visible;
- compile/min/target SDK values;
- application ID, namespace, version code/name;
- build types, minification, resource shrinking;
- signing-config presence and literal `storePassword`/`keyPassword` detection with redaction;
- dependency declarations and conflicting versions;
- AndroidX/legacy support mixing, dynamic versions, version catalogs;
- insecure Maven HTTP URLs and `allowInsecureProtocol`;
- Gradle wrapper URL/version/checksum;
- `gradle.properties` settings with credential-key redaction;
- ProGuard/R8 keep rules and obfuscation flags;
- Lint `abortOnError=false`.

Rules: `OGX-ANDROID-GRADLE-*`, `OGX-ANDROID-DEP-*`, `OGX-ANDROID-R8-*`, `OGX-ANDROID-SIGN-*`, `OGX-ANDROID-LINT-*`, `OGX-ANDROID-CATALOG-*`.

### 4.4 Resources and accessibility

- layout count, asset count, resource type inventory;
- large bundled assets;
- `ImageView`/`ImageButton` without `contentDescription` (excluding decorative markers);
- Compose clickable heuristics.

Rules: `OGX-ANDROID-ASSET-001`, `OGX-ANDROID-A11Y-*`.

### 4.5 Sources

For Kotlin/Java under `src/main`:

- shared secret patterns with redacted evidence;
- shared secure-coding patterns;
- WebView JavaScript/bridge/file-access/debugging;
- world-readable/writable storage and shared external storage;
- credential-like values in log statements;
- Compose/WebView inventory;
- delimiter-balance warnings only.

Rules: `OGX-ANDROID-WEBVIEW-*`, `OGX-ANDROID-STORAGE-*`, `OGX-ANDROID-LOG-001`, plus shared `OGX-SEC-*` and `OGX-CODE-*`.

### 4.6 APK/AAB artifacts

- detects `.apk` and `.aab` files outside default-excluded build directories;
- reports size, SHA-256 for files up to 64 MB, ZIP validity, entry count;
- lists `META-INF/*.RSA/.DSA/.EC` signature-entry presence;
- lists `AndroidManifest.xml` entries;
- detects `BundleConfig.pb`;
- flags unreadable ZIPs and APKs without v1 signature entries.

Rules: `OGX-ANDROID-APK-001`, `OGX-ANDROID-APK-002`.

**Not performed:** binary `AndroidManifest.xml` decoding, v2/v3/v4 signature verification, certificate inspection, APK installation or runtime behavior.

## 5. Build and test discovery

The Android `build` operation reports:

- discovered Gradle files and build adapters;
- local SDK/JDK/Gradle/wrapper/adb presence;
- planned commands for review only;
- `executed: false`, `execution_status: not_configured`.

The `test` operation discovers:

- `src/test/**/*.kt|java`;
- `src/androidTest/**/*.kt|java`;
- candidate frameworks from Gradle declarations;
- instrumentation runner;
- `executed: false`, `device_operations: not_attempted`.

No unit test, instrumentation test, emulator, device, `adb` command, APK install or Gradle task is executed by OGX.

## 6. CLI

```powershell
ogx android "D:\Repos\MyAndroidApp" --format both
ogx android "D:\Repos\MyAndroidApp" --format json --strict
ogx engines inspect "D:\Repos\MyAndroidApp" --engines android --json
```

- Reports are written to external `OGX_HOME` state.
- `--strict` returns exit code `1` only when high/critical findings exist.
- The target repository is not modified.
- `ogx analyze` also includes the Android engine when Android is detected.

## 7. Report shape

`ogx android` emits schema `ogx-android-report-1`:

```text
schema, tool, engine_version, generated_at, project_id, project,
fingerprint, analysis_mode, execution_performed, network_access,
detected, engine, capabilities, detection, facts, operations,
findings, summary, warnings, errors, limits
```

`facts` is the Android analysis payload (manifests, gradle, resources, sources, artifacts, environment, tests, build, limitations). `operations` contains other engine operations such as dependency inventory and diagnostics. This keeps the Markdown renderer and external consumers simple while preserving full engine facts.

## 8. Validation evidence

Android tests cover:

- permissions, exported components, launcher false-positive prevention and deep links;
- cleartext traffic, network-security-config cleartext and user-CA trust;
- debug-only user-CA trust not reported as production trust;
- Gradle dependency conflicts, dynamic catalog versions, insecure wrapper URL, checksum absence, R8 rules and signing-secret redaction;
- APK ZIP metadata, missing v1 signature entries and malformed artifacts;
- malformed manifest and DTD/entity rejection;
- report flattening, external-only behavior, `not_configured` build status and no-device-operation statements.

The complete suite result is recorded in `docs/TEST_RESULTS.md`.

## 9. Limitations and next steps

Required before any Android production claim:

1. Run Gradle unit tests and a debug build in an approved isolated environment with a pinned Android SDK.
2. Decode binary manifests and verify APK signatures with approved tools.
3. Add instrumentation/emulator testing only behind an explicit device-operation approval boundary.
4. Expand Compose/accessibility and manifest-merger awareness with fixtures from real projects.
5. Add baseline/suppression support so existing accepted risks can be governed rather than repeatedly reported.

Until then, Android execution remains `not_configured`, and all Android output is static evidence, not proof of buildability, installability, runtime security or store readiness.
