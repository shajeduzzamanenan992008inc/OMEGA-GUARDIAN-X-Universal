"""Hash-chained audit log tests."""

from __future__ import annotations

import json
import sys

import pytest

from ogx.platform.audit import GENESIS_HASH, append_chained, verify_chain
from ogx.platform.operations import append_audit, verify_audit


def test_chain_links_records(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_chained(path, {"event": "one", "actor": "a"})
    append_chained(path, {"event": "two", "actor": "b"})
    append_chained(path, {"event": "three", "actor": "c"})

    result = verify_chain(path)
    assert result["valid"] is True
    assert result["records"] == 3
    assert result["chained"] == 3
    assert result["seq"] == 3
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["integrity"]["prev_hash"] == GENESIS_HASH
    assert records[1]["integrity"]["prev_hash"] == records[0]["integrity"]["hash"]


def test_modified_record_is_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_chained(path, {"event": "one"})
    append_chained(path, {"event": "two", "result": "ok"})
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["result"] = "denied"
    lines[1] = json.dumps(tampered, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_chain(path)
    assert result["valid"] is False
    assert any("hash mismatch" in error for error in result["errors"])


def test_reordered_records_are_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_chained(path, {"event": "one"})
    append_chained(path, {"event": "two"})
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[1], lines[0]]) + "\n", encoding="utf-8")

    result = verify_chain(path)
    assert result["valid"] is False


def test_legacy_unchained_records_restart_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"event": "legacy"}\n', encoding="utf-8")
    append_chained(path, {"event": "chained"})

    result = verify_chain(path)
    assert result["valid"] is True
    assert result["records"] == 2
    assert result["unchained"] == 1
    assert result["chained"] == 1


def test_missing_file_is_an_empty_valid_log(tmp_path):
    result = verify_chain(tmp_path / "missing.jsonl")
    assert result["valid"] is True
    assert result["records"] == 0


def test_audit_secrets_are_redacted(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_audit({"event": "x", "token": "ghp_" + "a" * 30}, path=path)
    text = path.read_text(encoding="utf-8")
    assert "ghp_" not in text
    assert verify_audit(path=path)["valid"] is True


def test_malformed_line_marks_the_log_invalid(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_chained(path, {"event": "one"})
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    result = verify_chain(path)
    assert result["valid"] is False
    assert result["unchained"] == 1


def test_tail_truncation_needs_an_external_head(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_chained(path, {"event": "one"})
    append_chained(path, {"event": "two"})
    _, head = __import__("ogx.platform.audit", fromlist=["audit_head"]).audit_head(path)

    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n", encoding="utf-8")

    # Documented limitation: the remaining prefix is self-consistent, so only
    # comparing against the externally recorded head reveals the truncation.
    assert verify_chain(path)["valid"] is True
    assert verify_chain(path)["head"] != head


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require privileges")
def test_audit_rejects_symlink_target(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_text("", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    with pytest.raises(ValueError):
        append_chained(link, {"event": "x"})
