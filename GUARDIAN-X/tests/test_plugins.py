"""Tests for PLG-02..PLG-05: plugin trust, permissions, lifecycle, isolation."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ogx.plugins.ed25519 import (
    L,
    P,
    _BASE,
    _point_add,
    _point_mul,
    verify as ed25519_verify,
)
from ogx.plugins.manifest import (
    ManifestError,
    parse_manifest,
    parse_version,
    satisfies,
    verify_files,
)
from ogx.plugins.permissions import (
    PermissionDenied,
    PermissionPolicy,
    make_grant,
    revoke_grant,
)
from ogx.plugins.runner import (
    PluginRunner,
    ProcessIsolationBackend,
    ResourceLimits,
    RunnerError,
)
from ogx.plugins.store import LifecycleError, PluginStore
from ogx.plugins.trust import (
    SignatureEnvelope,
    TrustError,
    TrustPolicy,
    TrustStore,
    TrustedKey,
)

MAIN = b"print('plugin-ok')\n"


# --------------------------------------------------------------------------- #
# Independent Ed25519 signing helper (test fixture generation only)
# --------------------------------------------------------------------------- #


def _compress(point: tuple[int, int, int, int]) -> bytes:
    x, y, z, _ = point
    z_inv = pow(z, P - 2, P)
    x = (x * z_inv) % P
    y = (y * z_inv) % P
    value = y | ((x & 1) << 255)
    return value.to_bytes(32, "little")


def _sign(seed: bytes, message: bytes) -> tuple[bytes, bytes]:
    """RFC 8032 signing used only to generate deterministic test fixtures."""

    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    public = _compress(_point_mul(scalar, _BASE))
    nonce = int.from_bytes(hashlib.sha512(digest[32:] + message).digest(), "little") % L
    r_point = _compress(_point_mul(nonce, _BASE))
    challenge = int.from_bytes(
        hashlib.sha512(r_point + public + message).digest(), "little"
    ) % L
    s = (nonce + challenge * scalar) % L
    return public, r_point + s.to_bytes(32, "little")


SEED = bytes(range(32))
PUBLIC_KEY, _ = _sign(SEED, b"probe")


# --------------------------------------------------------------------------- #
# Manifest helpers
# --------------------------------------------------------------------------- #


def manifest_payload(**overrides):
    payload = {
        "schema_version": 1,
        "name": "sample.plugin",
        "version": "1.2.3",
        "publisher": "acme",
        "description": "A sample plugin.",
        "entrypoint": "main.py",
        "runtime": "python",
        "ogx_requires": ">=0.4.0,<0.6.0",
        "permissions": [],
        "dependencies": [],
        "files": [
            {
                "path": "main.py",
                "sha256": hashlib.sha256(MAIN).hexdigest(),
                "size": len(MAIN),
            }
        ],
    }
    payload.update(overrides)
    return payload


def build_manifest(**overrides):
    return parse_manifest(manifest_payload(**overrides))


def make_envelope(manifest, seed: bytes = SEED) -> SignatureEnvelope:
    body = {
        "schema_version": 1,
        "algorithm": "ed25519",
        "publisher": manifest.publisher,
        "key_id": "acme-2026",
        "digest": manifest.artifact_digest(),
    }
    message = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    public, signature = _sign(seed, message)
    assert public == PUBLIC_KEY
    return SignatureEnvelope.from_dict({**body, "signature": __import__("base64").b64encode(signature).decode()})


def make_trust_store() -> TrustStore:
    store = TrustStore()
    store.add(TrustedKey("acme", "acme-2026", PUBLIC_KEY))
    return store


# --------------------------------------------------------------------------- #
# Ed25519
# --------------------------------------------------------------------------- #


def test_rfc8032_test_vector_1_verifies():
    public_key = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert ed25519_verify(public_key, signature, b"") is True


def test_rfc8032_test_vector_3_verifies():
    public_key = bytes.fromhex(
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025"
    )
    signature = bytes.fromhex(
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
    )
    assert ed25519_verify(public_key, signature, bytes.fromhex("af82")) is True


@pytest.mark.parametrize(
    "public_key, signature",
    [
        (b"", b""),
        (PUBLIC_KEY, b"short"),
        (PUBLIC_KEY, bytes(64)),
        (b"\x00" * 32, bytes(64)),
        (PUBLIC_KEY, PUBLIC_KEY),
    ],
)
def test_malformed_signatures_are_rejected(public_key, signature):
    assert ed25519_verify(public_key, signature, b"message") is False


def test_non_canonical_scalar_is_rejected():
    public, signature = _sign(SEED, b"payload")
    tampered = signature[:32] + (L).to_bytes(32, "little")
    assert ed25519_verify(public, tampered, b"payload") is False


# --------------------------------------------------------------------------- #
# Manifest validation
# --------------------------------------------------------------------------- #


def test_valid_manifest_parses_and_digest_is_stable():
    first = build_manifest()
    second = parse_manifest(json.dumps(manifest_payload()))
    assert first.artifact_digest() == second.artifact_digest()
    assert first.artifact_digest() == build_manifest().artifact_digest()
    assert first.total_bytes() == len(MAIN)


def test_manifest_digest_changes_with_permissions_and_files():
    baseline = build_manifest().artifact_digest()
    with_permission = build_manifest(permissions=["fs.read.package"]).artifact_digest()
    assert baseline != with_permission


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "/etc/passwd",
        "C:/windows/system32/x.py",
        "a\\b.py",
        "dir//file.py",
        "./file.py",
        "con.py",
        "trailing. ",
        "dir/../file.py",
    ],
)
def test_manifest_rejects_unsafe_paths(path):
    payload = manifest_payload()
    payload["files"] = [
        {"path": path, "sha256": hashlib.sha256(MAIN).hexdigest(), "size": len(MAIN)}
    ]
    payload["entrypoint"] = path
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_rejects_unknown_fields():
    payload = manifest_payload()
    payload["allow_shell"] = True
    with pytest.raises(ManifestError, match="Unknown manifest field"):
        parse_manifest(payload)


def test_manifest_rejects_unknown_capability():
    with pytest.raises(ManifestError, match="Unknown capability"):
        build_manifest(permissions=["root.everything"])


def test_manifest_requires_entrypoint_declared_in_files():
    payload = manifest_payload(entrypoint="other.py")
    with pytest.raises(ManifestError, match="entrypoint"):
        parse_manifest(payload)


def test_manifest_rejects_duplicate_files_and_malformed_digest():
    with pytest.raises(ManifestError, match="Duplicate file entry"):
        parse_manifest(
            manifest_payload(
                files=[
                    {"path": "main.py", "sha256": hashlib.sha256(MAIN).hexdigest(), "size": len(MAIN)},
                    {"path": "main.py", "sha256": hashlib.sha256(MAIN).hexdigest(), "size": len(MAIN)},
                ]
            )
        )
    with pytest.raises(ManifestError, match="Invalid SHA-256"):
        parse_manifest(
            manifest_payload(files=[{"path": "main.py", "sha256": "xyz", "size": len(MAIN)}])
        )


def test_manifest_rejects_incompatible_runtime_and_version():
    with pytest.raises(ManifestError, match="runtime"):
        build_manifest(runtime="node")
    with pytest.raises(ManifestError, match="schema version"):
        build_manifest(schema_version=2)
    with pytest.raises(ManifestError, match="semantic version"):
        build_manifest(version="1.2")


def test_version_specifiers():
    assert satisfies(parse_version("1.5.0"), ">=1.0.0,<2.0.0") is True
    assert satisfies(parse_version("2.0.0"), ">=1.0.0,<2.0.0") is False
    assert satisfies(parse_version("1.4.9"), "~=1.4.0") is True
    assert satisfies(parse_version("1.5.0"), "~=1.4.0") is False
    assert satisfies(parse_version("1.0.0-rc1"), ">=1.0.0") is False
    with pytest.raises(ManifestError):
        satisfies(parse_version("1.0.0"), "=>1.0.0")


def test_verify_files_detects_tampering_and_undeclared_files():
    manifest = build_manifest()
    verify_files(manifest, {"main.py": MAIN})
    with pytest.raises(ManifestError, match="Digest mismatch"):
        verify_files(manifest, {"main.py": b"X" * len(MAIN)})
    with pytest.raises(ManifestError, match="undeclared"):
        verify_files(manifest, {"main.py": MAIN, "extra.py": b"x"})


# --------------------------------------------------------------------------- #
# Trust
# --------------------------------------------------------------------------- #


def test_trust_policy_accepts_a_valid_signature():
    manifest = build_manifest()
    policy = TrustPolicy(make_trust_store())
    record = policy.verify(manifest, make_envelope(manifest), contents={"main.py": MAIN})
    assert record.to_dict()["verified"] is True
    assert record.digest == manifest.artifact_digest()


def test_trust_policy_rejects_unsigned_packages():
    manifest = build_manifest()
    with pytest.raises(TrustError, match="unsigned"):
        TrustPolicy(make_trust_store()).verify(manifest, None)


def test_trust_policy_rejects_unknown_publisher_key():
    manifest = build_manifest()
    with pytest.raises(TrustError, match="not present"):
        TrustPolicy(TrustStore()).verify(manifest, make_envelope(manifest))


def test_trust_policy_rejects_digest_mismatch():
    manifest = build_manifest()
    other = build_manifest(permissions=["fs.read.package"])
    with pytest.raises(TrustError, match="digest"):
        TrustPolicy(make_trust_store()).verify(manifest, make_envelope(other))


def test_trust_policy_rejects_tampered_signature():
    manifest = build_manifest()
    envelope = make_envelope(manifest)
    tampered = SignatureEnvelope(
        publisher=envelope.publisher,
        key_id=envelope.key_id,
        digest=envelope.digest,
        signature=bytes([envelope.signature[0] ^ 0x01]) + envelope.signature[1:],
    )
    with pytest.raises(TrustError, match="verification failed"):
        TrustPolicy(make_trust_store()).verify(manifest, tampered)


def test_trust_policy_rejects_revoked_and_expired_keys():
    manifest = build_manifest()
    store = make_trust_store()
    store.revoke("acme", "acme-2026", "compromised")
    with pytest.raises(TrustError, match="revoked"):
        TrustPolicy(store).verify(manifest, make_envelope(manifest))

    expired = TrustStore()
    expired.add(
        TrustedKey(
            "acme",
            "acme-2026",
            PUBLIC_KEY,
            not_after=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
    )
    with pytest.raises(TrustError, match="expired"):
        TrustPolicy(expired).verify(manifest, make_envelope(manifest))


def test_trust_store_round_trips_and_rejects_malformed_entries(tmp_path):
    path = tmp_path / "trust.json"
    store = make_trust_store()
    store.path = path
    store.save()
    reloaded = TrustStore(path)
    assert len(reloaded) == 1
    assert reloaded.get("acme", "acme-2026").public_key == PUBLIC_KEY

    path.write_text(json.dumps({"schema_version": 1, "keys": [{"publisher": "x"}]}))
    with pytest.raises(TrustError):
        TrustStore(path)


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


def test_permission_policy_denies_by_default():
    manifest = build_manifest(permissions=["fs.read.package"])
    with pytest.raises(PermissionDenied, match="no grant"):
        PermissionPolicy().effective(manifest, None)


def test_permission_policy_returns_intersection_without_escalation():
    manifest = build_manifest(permissions=["fs.read.package"])
    grant = make_grant(manifest, ["fs.read.package", "fs.write.package"], granted_by="owner")
    assert PermissionPolicy().effective(manifest, grant) == frozenset({"fs.read.package"})


def test_permission_policy_rejects_ungranted_declared_capability():
    manifest = build_manifest(permissions=["fs.read.package", "fs.write.package"])
    grant = make_grant(manifest, ["fs.read.package"], granted_by="owner")
    with pytest.raises(PermissionDenied, match="not granted"):
        PermissionPolicy().effective(manifest, grant)


def test_sensitive_capabilities_require_distinct_approver():
    manifest = build_manifest(permissions=["network.outbound"])
    with pytest.raises(PermissionDenied, match="approver"):
        make_grant(manifest, ["network.outbound"], granted_by="owner")
    with pytest.raises(PermissionDenied, match="two distinct"):
        make_grant(
            manifest, ["network.outbound"], granted_by="owner", approved_by="owner"
        )
    grant = make_grant(
        manifest, ["network.outbound"], granted_by="owner", approved_by="security"
    )
    assert PermissionPolicy().effective(manifest, grant) == frozenset({"network.outbound"})


def test_expired_and_revoked_grants_authorize_nothing():
    manifest = build_manifest(permissions=["fs.read.package"])
    grant = make_grant(manifest, ["fs.read.package"], granted_by="owner")
    revoked = revoke_grant(grant, reason="no longer needed")
    with pytest.raises(PermissionDenied, match="revoked"):
        PermissionPolicy().effective(manifest, revoked)

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    expired = make_grant(
        manifest, ["fs.read.package"], granted_by="owner", expires_at=past
    )
    with pytest.raises(PermissionDenied, match="expired"):
        PermissionPolicy().effective(manifest, expired)


def test_grant_is_bound_to_exact_plugin_version():
    manifest = build_manifest(permissions=["fs.read.package"])
    grant = make_grant(manifest, ["fs.read.package"], granted_by="owner")
    upgraded = build_manifest(version="1.2.4", permissions=["fs.read.package"])
    with pytest.raises(PermissionDenied, match="version"):
        PermissionPolicy().effective(upgraded, grant)


def test_unknown_capability_grant_is_rejected():
    manifest = build_manifest()
    with pytest.raises(PermissionDenied, match="Unknown capability"):
        make_grant(manifest, ["root"], granted_by="owner")


# --------------------------------------------------------------------------- #
# Lifecycle store
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    plugin_store = PluginStore(tmp_path / "plugins.db")
    plugin_store.initialize()
    return plugin_store


def _install(store, manifest=None, verified=True):
    manifest = manifest or build_manifest()
    record = {
        "publisher": manifest.publisher,
        "key_id": "acme-2026",
        "digest": manifest.artifact_digest(),
    } if verified else None
    return store.install(manifest, actor="operator", verified=record)


def test_lifecycle_happy_path_and_audit(store):
    manifest = build_manifest()
    _install(store, manifest)
    assert store.get(manifest.name)["status"] == "installed"

    store.validate(manifest.name, actor="operator")
    store.enable(manifest.name, actor="operator")
    store.disable(manifest.name, actor="operator")
    store.enable(manifest.name, actor="operator")
    store.revoke(manifest.name, actor="security", reason="policy violation")
    store.uninstall(manifest.name, actor="operator")

    events = [event["event"] for event in store.events(manifest.name)]
    assert events[0] == "uninstalled"
    assert "revoked" in events
    assert store.get(manifest.name)["status"] == "uninstalled"


def test_lifecycle_rejects_illegal_transitions(store):
    manifest = build_manifest()
    _install(store, manifest)
    with pytest.raises(LifecycleError, match="Illegal"):
        store.enable(manifest.name, actor="operator")
    store.validate(manifest.name, actor="operator")
    store.enable(manifest.name, actor="operator")
    with pytest.raises(LifecycleError, match="Illegal"):
        store.validate(manifest.name, actor="operator")
    with pytest.raises(LifecycleError, match="reason"):
        store.revoke(manifest.name, actor="security", reason=" ")


def test_validation_requires_a_verified_trust_record(store):
    manifest = build_manifest()
    _install(store, manifest, verified=False)
    with pytest.raises(LifecycleError, match="trust record"):
        store.validate(manifest.name, actor="operator")


def test_enable_checks_dependencies(store):
    dependent = build_manifest(
        name="dependent.plugin",
        dependencies=[{"name": "sample.plugin", "specifier": ">=1.0.0,<2.0.0"}],
    )
    _install(store, dependent)
    store.validate(dependent.name, actor="operator")
    with pytest.raises(LifecycleError, match="not installed"):
        store.enable(dependent.name, actor="operator")

    base = build_manifest(version="1.1.0")
    _install(store, base)
    store.validate(base.name, actor="operator")
    store.enable(dependent.name, actor="operator")
    assert store.get(dependent.name)["status"] == "enabled"


def test_enable_rejects_incompatible_dependency_version(store):
    dependent = build_manifest(
        name="dependent.plugin",
        dependencies=[{"name": "sample.plugin", "specifier": ">=2.0.0"}],
    )
    _install(store, dependent)
    store.validate(dependent.name, actor="operator")
    base = build_manifest(version="1.5.0")
    _install(store, base)
    store.validate(base.name, actor="operator")
    with pytest.raises(LifecycleError, match="does not satisfy"):
        store.enable(dependent.name, actor="operator")


def test_grant_round_trip(store):
    manifest = build_manifest(permissions=["fs.read.package"])
    _install(store, manifest)
    grant = make_grant(manifest, ["fs.read.package"], granted_by="owner")
    store.set_grant(grant, actor="owner")
    stored = store.get_grant(manifest.name)
    assert stored is not None
    assert stored.capabilities == frozenset({"fs.read.package"})


def test_plugin_store_rejects_symlink_database(tmp_path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("Symlinks unavailable")
    target = tmp_path / "real.db"
    target.write_bytes(b"")
    link = tmp_path / "link.db"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation requires privileges on Windows")
    with pytest.raises(LifecycleError, match="symlink"):
        PluginStore(link).initialize()


# --------------------------------------------------------------------------- #
# Isolation runner
# --------------------------------------------------------------------------- #


def test_process_backend_runs_a_plugin_in_a_scrubbed_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_TEST_SECRET", "super-secret-value")
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "main.py").write_text(
        "import os\n"
        "print('plugin-ok')\n"
        "print('secret=' + os.environ.get('OGX_TEST_SECRET', ''))\n"
        "print('cwd=' + os.path.basename(os.getcwd()))\n",
        encoding="utf-8",
    )
    manifest = build_manifest()
    backend = ProcessIsolationBackend()
    result = backend.run(
        root, "main.py", [], limits=ResourceLimits(timeout_seconds=30)
    )
    assert result.ok is True
    assert result.exit_code == 0
    normalized = result.stdout.replace("\r\n", "\n")
    assert "plugin-ok" in normalized
    assert "secret=\n" in normalized
    assert "cwd=" in normalized
    assert result.backend == "process"


def test_process_backend_enforces_timeout(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "main.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    result = ProcessIsolationBackend().run(
        root, "main.py", [], limits=ResourceLimits(timeout_seconds=0.5)
    )
    assert result.timed_out is True
    assert result.ok is False


def test_process_backend_bounds_output(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "main.py").write_text("print('x' * 50000)\n", encoding="utf-8")
    result = ProcessIsolationBackend().run(
        root, "main.py", [], limits=ResourceLimits(timeout_seconds=10, max_output_bytes=1024)
    )
    assert result.output_truncated is True
    assert len(result.stdout) <= 1024


def test_process_backend_refuses_entrypoint_escape(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('x')\n", encoding="utf-8")
    with pytest.raises(RunnerError, match="escapes"):
        ProcessIsolationBackend().run(
            root, "../outside.py", [], limits=ResourceLimits(timeout_seconds=5)
        )


def test_runner_fails_closed_without_a_backend():
    manifest = build_manifest()
    runner = PluginRunner(allow_process_backend=False)
    with pytest.raises(RunnerError, match="No plugin isolation backend"):
        runner.execute(manifest, None, plugin_root=Path("."))


def test_runner_refuses_network_plugin_on_process_backend(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "main.py").write_text("print('x')\n", encoding="utf-8")
    manifest = build_manifest(permissions=["network.outbound"])
    grant = make_grant(
        manifest, ["network.outbound"], granted_by="owner", approved_by="security"
    )
    runner = PluginRunner(backend=ProcessIsolationBackend())
    with pytest.raises(RunnerError, match="network"):
        runner.execute(manifest, grant, plugin_root=root)


def test_runner_refuses_unpermitted_execution(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "main.py").write_text("print('x')\n", encoding="utf-8")
    manifest = build_manifest(permissions=["fs.read.package"])
    runner = PluginRunner(backend=ProcessIsolationBackend())
    with pytest.raises(PermissionDenied):
        runner.execute(manifest, None, plugin_root=root)


def test_runner_executes_a_granted_plugin(tmp_path):
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "main.py").write_text("print('granted-run')\n", encoding="utf-8")
    manifest = build_manifest(permissions=["fs.read.package"])
    grant = make_grant(manifest, ["fs.read.package"], granted_by="owner")
    runner = PluginRunner(backend=ProcessIsolationBackend())
    result = runner.execute(manifest, grant, plugin_root=root)
    assert result.ok is True
    assert "granted-run" in result.stdout
    assert result.network_isolated is False
    assert result.filesystem_isolated is False


def test_docker_backend_fails_closed_without_docker(tmp_path):
    from ogx.sandbox import docker_path

    if docker_path():
        pytest.skip("Docker is present on this machine")
    from ogx.plugins.runner import DockerIsolationBackend

    root = tmp_path / "plugin"
    root.mkdir()
    (root / "main.py").write_text("print('x')\n", encoding="utf-8")
    manifest = build_manifest()
    backend = DockerIsolationBackend(
        "ghcr.io/example/sandbox@sha256:" + "0" * 64
    )
    runner = PluginRunner(backend=backend)
    with pytest.raises(RunnerError, match="unavailable"):
        runner.execute(manifest, None, plugin_root=root)
