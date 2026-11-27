"""REL-02: release artifact checksum manifests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ogx.artifacts import (
    ArtifactError,
    describe_artifacts,
    render_manifest,
    render_sums,
    verify_sums,
    write_manifests,
)

ROOT = Path(__file__).resolve().parents[1]


def make_artifacts(tmp_path: Path) -> list[Path]:
    first = tmp_path / "ogx-0.8.0a1.whl"
    second = tmp_path / "omega-shield-0.8.0a1-dropin.zip"
    first.write_bytes(b"wheel-bytes")
    second.write_bytes(b"zip-bytes")
    return [first, second]


def test_manifest_records_exact_names_sizes_and_digests(tmp_path):
    artifacts = make_artifacts(tmp_path)
    result = write_manifests(
        artifacts,
        version="0.8.0a1",
        sums_path=tmp_path / "dist" / "SHA256SUMS",
        manifest_path=tmp_path / "dist" / "SHA256SUMS.json",
    )

    names = [entry["name"] for entry in result["artifacts"]]
    assert names == sorted(["ogx-0.8.0a1.whl", "omega-shield-0.8.0a1-dropin.zip"])
    sizes = {entry["name"]: entry["size"] for entry in result["artifacts"]}
    assert sizes["ogx-0.8.0a1.whl"] == len(b"wheel-bytes")
    assert all(len(entry["sha256"]) == 64 for entry in result["artifacts"])

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.0"
    assert manifest["version"] == "0.8.0a1"
    assert manifest["algorithm"] == "sha256"
    assert [entry["name"] for entry in manifest["artifacts"]] == names


def test_sums_file_is_sha256sum_compatible(tmp_path):
    artifacts = make_artifacts(tmp_path)
    result = write_manifests(artifacts, version="1", sums_path=tmp_path / "SHA256SUMS")
    text = Path(result["sums"]).read_bytes().decode("utf-8")
    assert "\r\n" not in text
    for line in text.splitlines():
        digest, name = line.split("  ")
        assert len(digest) == 64 and digest == digest.lower()
        assert name in {"ogx-0.8.0a1.whl", "omega-shield-0.8.0a1-dropin.zip"}


def test_manifest_is_deterministic_for_the_same_inputs(tmp_path):
    artifacts = make_artifacts(tmp_path)
    entries = describe_artifacts(artifacts)
    assert render_manifest(entries, version="0.8.0a1") == render_manifest(entries, version="0.8.0a1")
    assert "generated_at" not in render_manifest(entries, version="0.8.0a1")


def test_verification_passes_for_untouched_artifacts(tmp_path):
    artifacts = make_artifacts(tmp_path)
    result = write_manifests(artifacts, version="1", sums_path=tmp_path / "SHA256SUMS")
    report = verify_sums(Path(result["sums"]).read_text(encoding="utf-8"), tmp_path)
    assert report["all_ok"] is True
    assert report["checked"] == 2
    assert report["mismatched"] == []


def test_tampered_artifact_is_reported(tmp_path):
    artifacts = make_artifacts(tmp_path)
    result = write_manifests(artifacts, version="1", sums_path=tmp_path / "SHA256SUMS")
    (tmp_path / "ogx-0.8.0a1.whl").write_bytes(b"tampered")

    report = verify_sums(Path(result["sums"]).read_text(encoding="utf-8"), tmp_path)
    assert report["all_ok"] is False
    assert [item["name"] for item in report["mismatched"]] == ["ogx-0.8.0a1.whl"]


def test_missing_artifact_is_reported(tmp_path):
    artifacts = make_artifacts(tmp_path)
    result = write_manifests(artifacts, version="1", sums_path=tmp_path / "SHA256SUMS")
    (tmp_path / "omega-shield-0.8.0a1-dropin.zip").unlink()

    report = verify_sums(Path(result["sums"]).read_text(encoding="utf-8"), tmp_path)
    assert report["all_ok"] is False
    assert report["missing"] == ["omega-shield-0.8.0a1-dropin.zip"]


@pytest.mark.parametrize(
    "line",
    [
        "0" * 64 + "  ../outside.bin",
        "0" * 64 + "  sub/dir.bin",
        "0" * 64 + "  C:\\windows\\system32\\evil.dll",
        "0" * 64 + "  .",
        "not-a-digest  file.bin",
        "0" * 63 + "  file.bin",
        "0" * 64 + "  SHA256SUMS",
    ],
)
def test_hostile_or_malformed_manifest_lines_are_rejected(line):
    with pytest.raises(ArtifactError):
        verify_sums(line + "\n", ".")


def test_duplicate_names_are_rejected(tmp_path):
    artifacts = make_artifacts(tmp_path)
    with pytest.raises(ArtifactError):
        describe_artifacts([artifacts[0], artifacts[0]])


def test_empty_manifest_is_rejected():
    with pytest.raises(ArtifactError):
        verify_sums("\n\n", ".")


def test_symlinked_artifact_is_refused(tmp_path):
    target = tmp_path / "real.whl"
    target.write_bytes(b"x")
    link = tmp_path / "link.whl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(ArtifactError):
        describe_artifacts([link])


def test_directory_artifact_is_refused(tmp_path):
    with pytest.raises(ArtifactError):
        describe_artifacts([tmp_path])


def test_no_artifacts_is_an_error():
    with pytest.raises(ArtifactError):
        describe_artifacts([])


def test_render_sums_matches_entries(tmp_path):
    entries = describe_artifacts(make_artifacts(tmp_path))
    text = render_sums(entries)
    assert text.count("\n") == 2
    assert text.startswith(entries[0]["sha256"])


# ------------------------------------------------------------------- CLI
def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; from ogx.cli import main; raise SystemExit(main(sys.argv[1:]))", *args],
        capture_output=True, text=True, cwd=ROOT, env={**_env(), "PYTHONPATH": str(ROOT / "src")},
    )


def _env() -> dict[str, str]:
    import os

    return {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}


def test_cli_manifest_then_verify_round_trip(tmp_path):
    artifacts = make_artifacts(tmp_path)
    dist = tmp_path / "dist"
    made = _run_cli(
        "release", "manifest", *(str(item) for item in artifacts),
        "--sums", str(dist / "SHA256SUMS"),
        "--manifest", str(dist / "SHA256SUMS.json"),
        "--version", "0.8.0a1",
    )
    assert made.returncode == 0, made.stderr
    payload = json.loads(made.stdout)
    assert len(payload["artifacts"]) == 2

    verified = _run_cli("release", "verify", str(dist / "SHA256SUMS"), "--directory", str(tmp_path))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    report = json.loads(verified.stdout)
    assert report["all_ok"] is True

    (tmp_path / "ogx-0.8.0a1.whl").write_bytes(b"tampered")
    failed = _run_cli("release", "verify", str(dist / "SHA256SUMS"), "--directory", str(tmp_path))
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["all_ok"] is False


def test_cli_rejects_missing_artifact(tmp_path):
    result = _run_cli("release", "manifest", str(tmp_path / "absent.whl"), "--sums", str(tmp_path / "SHA256SUMS"))
    assert result.returncode == 2
    assert "Not a regular file" in result.stderr
