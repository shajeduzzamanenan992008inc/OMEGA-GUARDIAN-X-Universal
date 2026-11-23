import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ogx.detector import detect_project
from ogx.engines.android import AndroidEngine
from ogx.engines.contract import CapabilityStatus, EngineContext
from ogx.pipeline import analyze_android, render_android_markdown
from ogx.workspace import Workspace


def make_context(root: Path) -> EngineContext:
    workspace = Workspace.open(root)
    files = [workspace.root / entry.path for entry in workspace.files]
    profile = detect_project(workspace.root, files)
    fingerprint = workspace.fingerprint()
    return EngineContext(
        workspace=workspace,
        profile=profile,
        project_id=fingerprint["project_id"],
        fingerprint=fingerprint,
    )


def write_android_fixture(root: Path) -> None:
    main = root / "app" / "src" / "main"
    (main / "java" / "com" / "example").mkdir(parents=True)
    (main / "res" / "xml").mkdir(parents=True)
    (main / "res" / "layout").mkdir(parents=True)
    (main / "AndroidManifest.xml").write_text(
        """<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.demo">
  <uses-sdk android:minSdkVersion="21" android:targetSdkVersion="33" />
  <uses-permission android:name="android.permission.CAMERA" />
  <uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" />
  <application android:debuggable="true" android:usesCleartextTraffic="true"
      android:networkSecurityConfig="@xml/network_security_config" android:allowBackup="true">
    <activity android:name=".MainActivity" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
    <service android:name=".ExportedService" android:exported="true" />
    <activity android:name=".LinkActivity">
      <intent-filter>
        <action android:name="android.intent.action.VIEW" />
        <category android:name="android.intent.category.BROWSABLE" />
        <data android:scheme="https" android:host="example.com" />
      </intent-filter>
    </activity>
  </application>
</manifest>
""",
        encoding="utf-8",
    )
    (main / "res" / "xml" / "network_security_config.xml").write_text(
        '<network-security-config>'
        '<base-config cleartextTrafficPermitted="true">'
        '<trust-anchors><certificates src="user"/></trust-anchors>'
        '</base-config></network-security-config>',
        encoding="utf-8",
    )
    (main / "res" / "layout" / "activity_main.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<ImageView android:id="@+id/logo" /></LinearLayout>',
        encoding="utf-8",
    )
    (main / "java" / "com" / "example" / "MainActivity.kt").write_text(
        """package com.example.demo

import android.webkit.WebView

class MainActivity {
    fun setup(view: WebView) {
        view.settings.setJavaScriptEnabled(true)
        view.addJavascriptInterface(Object(), "bridge")
        android.util.Log.d("auth", "token=superSecretValue123")
    }
}
""",
        encoding="utf-8",
    )
    (root / "settings.gradle").write_text("pluginManagement { repositories { google() } }\n", encoding="utf-8")
    (root / "app" / "build.gradle.kts").write_text(
        """plugins {
    id("com.android.application") version "8.2.0"
    kotlin("android") version "1.9.0"
}
android {
    namespace = "com.example.demo"
    compileSdk = 34
    defaultConfig { minSdk = 21; targetSdk = 33 }
    buildTypes { release { isMinifyEnabled = false } }
    signingConfigs { release { storePassword = "literalSigningSecret123" } }
}
dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("com.squareup.okhttp3:okhttp:4.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.android.support:appcompat-v7:28.0.0")
}
""",
        encoding="utf-8",
    )
    (root / "gradle" / "wrapper").mkdir(parents=True)
    (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
        "distributionUrl=http://services.gradle.org/distributions/gradle-7.6-bin.zip\n", encoding="utf-8"
    )
    (root / "gradle" / "libs.versions.toml").write_text('[versions]\nokhttp = "4.12.+"\n', encoding="utf-8")
    (root / "proguard-rules.pro").write_text("-keep class * { *; }\n-dontobfuscate\n", encoding="utf-8")
    (root / "app" / "src" / "test" / "java" / "com" / "example").mkdir(parents=True)
    (root / "app" / "src" / "test" / "java" / "com" / "example" / "MainTest.kt").write_text(
        "class MainTest\n", encoding="utf-8"
    )
    with zipfile.ZipFile(root / "fixture.apk", "w") as archive:
        archive.writestr("AndroidManifest.xml", b"binary-not-decoded")
        archive.writestr("classes.dex", b"dex")


class AndroidEngineTests(unittest.TestCase):
    def evaluate(self, root: Path):
        return AndroidEngine().evaluate(make_context(root))

    def test_manifest_permissions_exported_components_and_deep_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_android_fixture(root)
            result = self.evaluate(root)
            rules = {item.rule_id for item in result.findings}
            self.assertTrue(result.detected)
            for rule in ("OGX-ANDROID-PERM-001", "OGX-ANDROID-PERM-002",
                         "OGX-ANDROID-EXPORT-001", "OGX-ANDROID-EXPORT-002",
                         "OGX-ANDROID-DEEPLINK-001", "OGX-ANDROID-MANIFEST-003"):
                self.assertIn(rule, rules)
            exported = [item for item in result.findings if item.rule_id == "OGX-ANDROID-EXPORT-001"]
            self.assertTrue(exported)
            self.assertTrue(all("MainActivity" not in (item.evidence or "") for item in exported))
            manifest = result.facts["analyze"]["facts"]["manifests"][0]
            self.assertEqual(manifest["dangerous_permissions"], ["CAMERA"])
            self.assertEqual(manifest["special_permissions"], ["SYSTEM_ALERT_WINDOW"])
            self.assertEqual(manifest["deep_links"][0]["host"], "example.com")
            self.assertTrue(any(item["launcher"] for item in manifest["components"]))

    def test_network_security_config_and_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_android_fixture(root)
            result = self.evaluate(root)
            facts = result.facts["analyze"]["facts"]
            nsc = facts["manifests"][0]["network_security_config"]
            self.assertTrue(nsc["cleartext_permitted"])
            self.assertTrue(nsc["user_ca_trusted"])
            self.assertIn("OGX-ANDROID-NSC-001", {item.rule_id for item in result.findings})
            self.assertIn("OGX-ANDROID-NSC-002", {item.rule_id for item in result.findings})
            environment = facts["environment"]
            self.assertEqual(environment["build_execution"], "not_configured")
            self.assertEqual(environment["device_operations"], "not_attempted")
            self.assertIn(result.capabilities["build"].status,
                          {CapabilityStatus.NOT_CONFIGURED, CapabilityStatus.PARTIAL})
            self.assertFalse(result.execution_performed)

    def test_debug_only_user_ca_is_not_reported_as_production_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "app" / "src" / "main"
            (main / "res" / "xml").mkdir(parents=True)
            (main / "AndroidManifest.xml").write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                'package="com.example.debugonly">'
                '<application android:networkSecurityConfig="@xml/network_security_config" />'
                '</manifest>',
                encoding="utf-8",
            )
            (main / "res" / "xml" / "network_security_config.xml").write_text(
                '<network-security-config><base-config cleartextTrafficPermitted="false" />'
                '<debug-overrides><trust-anchors><certificates src="user"/></trust-anchors></debug-overrides>'
                '</network-security-config>',
                encoding="utf-8",
            )
            result = self.evaluate(root)
            nsc = result.facts["analyze"]["facts"]["manifests"][0]["network_security_config"]
            self.assertTrue(nsc["debug_overrides"])
            self.assertFalse(nsc["user_ca_trusted"])
            self.assertNotIn("OGX-ANDROID-NSC-002", {item.rule_id for item in result.findings})

    def test_gradle_analysis_and_signing_secret_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_android_fixture(root)
            result = self.evaluate(root)
            rules = {item.rule_id for item in result.findings}
            for rule in ("OGX-ANDROID-SIGN-001", "OGX-ANDROID-DEP-001", "OGX-ANDROID-DEP-003",
                         "OGX-ANDROID-DEP-004", "OGX-ANDROID-DEP-006", "OGX-ANDROID-GRADLE-001",
                         "OGX-ANDROID-GRADLE-002", "OGX-ANDROID-R8-001", "OGX-ANDROID-R8-002",
                         "OGX-ANDROID-R8-003"):
                self.assertIn(rule, rules)
            gradle = result.facts["analyze"]["facts"]["gradle"]
            self.assertEqual(gradle["compile_sdk"], 34)
            self.assertEqual(gradle["conflicts"]["com.squareup.okhttp3:okhttp"], ["4.12.0", "4.9.0"])
            serialized = json.dumps(result.to_dict())
            self.assertNotIn("literalSigningSecret123", serialized)
            self.assertNotIn("superSecretValue123", serialized)

    def test_apk_metadata_and_malformed_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_android_fixture(root)
            (root / "broken.apk").write_bytes(b"not a zip")
            result = self.evaluate(root)
            rules = {item.rule_id for item in result.findings}
            self.assertIn("OGX-ANDROID-APK-001", rules)
            self.assertIn("OGX-ANDROID-APK-002", rules)
            artifacts = result.facts["analyze"]["facts"]["artifacts"]
            good = next(item for item in artifacts if item["path"] == "fixture.apk")
            self.assertTrue(good["zip_valid"])
            self.assertFalse(good["signature_verified"])
            self.assertFalse(any(item.get("signature_verified") for item in artifacts))

    def test_malformed_manifest_and_dtd_are_reported_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "app" / "src" / "main"
            main.mkdir(parents=True)
            (main / "AndroidManifest.xml").write_text(
                '<!DOCTYPE manifest [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android">&xxe;</manifest>',
                encoding="utf-8",
            )
            result = self.evaluate(root)
            self.assertTrue(result.detected)
            self.assertIn("OGX-ANDROID-MANIFEST-001", {item.rule_id for item in result.findings})
            self.assertFalse(result.execution_performed)

    def test_pipeline_android_report_is_flat_and_renderable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_android_fixture(root)
            report = analyze_android(root)
            self.assertEqual(report["schema"], "ogx-android-report-1")
            self.assertTrue(report["detected"])
            self.assertIn("manifests", report["facts"])
            self.assertIn("environment", report["facts"])
            self.assertFalse(report["execution_performed"])
            self.assertFalse(report["network_access"])
            rendered = render_android_markdown(report)
            self.assertIn("## Manifest summary", rendered)
            self.assertIn("## Gradle summary", rendered)
            self.assertIn("not_configured", rendered)
            self.assertNotIn("literalSigningSecret123", rendered)


if __name__ == "__main__":
    unittest.main()
