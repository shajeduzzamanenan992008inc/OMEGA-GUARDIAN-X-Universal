"""Tests for REL-04 provenance and REL-05 reproducible packaging."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from ogx.provenance import (
    ProvenanceError,
    SigningNotPermitted,
    attach_signature,
    build_statement,
    render_statement,
    sign_statement,
    signing_payload,
    verify_statement,
)

TOOLS = Path(__file__).resolve().parents[1] / "_dev" / "tools"
sys.path.insert(0, str(TOOLS))

from make_dist import build_archive  # noqa: E402


def _artifacts(tmp_path: Path) -> list[Path]:
    directory = tmp_path / "artifacts"
    directory.mkdir(exist_ok=True)
    (directory / "omega-shield.zip").write_bytes(b"PK\x03\x04archive")
    (directory / "notes.txt").write_text("release notes\n", encoding="utf-8")
    return [directory / "omega-shield.zip", directory / "notes.txt"]


def _statement(tmp_path: Path, **overrides):
    kwargs = {
        "builder_id": "local://ogx-dev",
        "build_type": "https://ogx.dev/build/dropin/v1",
        "source_uri": "git+https://example.invalid/omega-guardian-x@refs/heads/main",
        "source_digest": "a" * 64,
        "materials": [{"uri": "git+https://example.invalid/base", "sha256": "b" * 64}],
        "invocation": {"python": "3.12"},
    }
    kwargs.update(overrides)
    return build_statement(_artifacts(tmp_path), **kwargs)


def test_statement_is_deterministic_and_explicitly_unsigned(tmp_path):
    first = _statement(tmp_path)
    second = _statement(tmp_path)
    assert render_statement(first) == render_statement(second)
    assert first["signature"] is None
    assert first["schema_version"] == "1.0"
    names = [subject["name"] for subject in first["subject"]]
    assert names == sorted(names)
    assert first["predicate"]["buildDefinition"]["internalParameters"]["reproducibleCommand"]


def test_statement_rejects_unsafe_inputs(tmp_path):
    with pytest.raises(ProvenanceError, match="looks like key material"):
        _statement(tmp_path, builder_id="bearer token for builder")
    with pytest.raises(ProvenanceError, match="source_digest"):
        _statement(tmp_path, source_digest="not-a-digest")
    with pytest.raises(ProvenanceError, match="invalid SHA-256"):
        _statement(tmp_path, materials=[{"uri": "git+x", "sha256": "zz"}])
    with pytest.raises(ProvenanceError, match="at least one subject"):
        build_statement(
            [],
            builder_id="b",
            build_type="t",
            source_uri="u",
            source_digest="a" * 64,
        )


def test_verify_detects_tampering_and_missing_subjects(tmp_path):
    statement = _statement(tmp_path)
    base = tmp_path / "artifacts"
    report = verify_statement(statement, base)
    assert report["all_ok"] is True
    assert report["unsigned"] is True
    assert report["checked"] == 2

    (base / "notes.txt").write_text("tampered\n", encoding="utf-8")
    tampered = verify_statement(statement, base)
    assert tampered["all_ok"] is False
    assert tampered["mismatched"][0]["name"] == "notes.txt"

    (base / "notes.txt").unlink()
    missing = verify_statement(statement, base)
    assert missing["missing"] == ["notes.txt"]


def test_verify_refuses_unsafe_statements(tmp_path):
    statement = _statement(tmp_path)
    statement["subject"][0]["name"] = "../escape.bin"
    with pytest.raises(ProvenanceError, match="Unsafe subject"):
        verify_statement(statement, tmp_path / "artifacts")
    bad_type = _statement(tmp_path)
    bad_type["_type"] = "https://example.invalid/other"
    with pytest.raises(ProvenanceError, match="statement type"):
        verify_statement(bad_type, tmp_path / "artifacts")


def test_externally_produced_signature_is_verified(tmp_path):
    from tests.test_plugins import _sign

    statement = _statement(tmp_path)
    seed = bytes(range(32))
    public, signature = _sign(seed, signing_payload(statement))
    signed = attach_signature(
        statement,
        key_id="release-key-1",
        signature_hex=signature.hex(),
        public_key_hex=public.hex(),
    )
    report = verify_statement(signed, tmp_path / "artifacts", expected_public_key=public.hex())
    assert report["signature_valid"] is True
    assert report["all_ok"] is True

    other_public, _ = _sign(b"\x01" * 32, b"probe")
    with pytest.raises(ProvenanceError, match="unexpected public key"):
        verify_statement(signed, tmp_path / "artifacts", expected_public_key=other_public.hex())

    # A modified statement no longer matches the detached signature.
    tampered = json.loads(json.dumps(signed))
    tampered["predicate"]["runDetails"]["builder"]["id"] = "local://someone-else"
    report = verify_statement(tampered, tmp_path / "artifacts")
    assert report["signature_valid"] is False
    assert report["all_ok"] is False


def test_attach_signature_rejects_malformed_material(tmp_path):
    statement = _statement(tmp_path)
    with pytest.raises(ProvenanceError, match="signature_hex"):
        attach_signature(statement, key_id="k", signature_hex="zz", public_key_hex="00" * 32)
    with pytest.raises(ProvenanceError, match="public_key_hex"):
        attach_signature(statement, key_id="k", signature_hex="00" * 64, public_key_hex="00")
    with pytest.raises(ProvenanceError, match="key_id"):
        attach_signature(statement, key_id="bad key id", signature_hex="00" * 64, public_key_hex="00" * 32)


def test_project_never_signs_without_owner_authorization():
    with pytest.raises(SigningNotPermitted, match="owner authorization"):
        sign_statement({"anything": True})


def _mini_project(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "init.py").write_text("print('hi')\n", encoding="utf-8")
    (destination / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    core = destination / "core" / "ogx"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("__version__ = '1.0.0'\n", encoding="utf-8")
    (destination / "docs").mkdir()
    (destination / "docs" / "README.md").write_text("# doc\n", encoding="utf-8")
    # Excluded material must not influence the archive digest.
    (destination / "tests").mkdir()
    (destination / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (destination / ".env").write_text("SECRET=1\n", encoding="utf-8")
    return destination


def test_dropin_archive_is_byte_identical_across_copies_and_mtimes(tmp_path):
    first_root = _mini_project(tmp_path / "copy-one")
    second_root = _mini_project(tmp_path / "deep" / "copy-two")
    for number, path in enumerate(sorted(second_root.rglob("*")), start=1):
        if path.is_file():
            stamp = 1_600_000_000 + number * 997
            os.utime(path, (stamp, stamp))
    first = tmp_path / "a.zip"
    second = tmp_path / "b.zip"
    count_a = build_archive(first_root, first)
    count_b = build_archive(second_root, second)
    assert count_a == count_b
    assert first.read_bytes() == second.read_bytes()

    # Adding excluded material must not change the digest either.
    (second_root / "_dev").mkdir()
    (second_root / "_dev" / "tool.py").write_text("x = 1\n", encoding="utf-8")
    third = tmp_path / "c.zip"
    build_archive(second_root, third)
    assert third.read_bytes() == first.read_bytes()


def test_archive_entries_are_normalised(tmp_path):
    import zipfile

    root = _mini_project(tmp_path / "project")
    archive = tmp_path / "dist.zip"
    build_archive(root, archive)
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
    assert [info.filename for info in infos] == sorted(info.filename for info in infos)
    for info in infos:
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.create_system == 3
        assert info.external_attr == (0o100644 << 16)
    names = {info.filename for info in infos}
    assert all(name.startswith("omega-shield/") for name in names)
    assert not any("tests/" in name or ".env" in name for name in names)


def test_repro_tool_reports_identical_builds(tmp_path):
    import subprocess

    root = _mini_project(tmp_path / "project")
    result = subprocess.run(
        [sys.executable, str(TOOLS / "repro_check.py"), "--root", str(root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["identical"] is True
    assert report["entries"] >= 3
