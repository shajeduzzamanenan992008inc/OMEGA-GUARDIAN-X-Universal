"""Tamper-evident platform audit log.

Each record carries an integrity envelope::

    "integrity": {"algorithm": "sha256", "seq": N,
                  "prev_hash": "<hash of the previous record>",
                  "hash": "<sha256(prev_hash + canonical record body)>"}

Verification recomputes the chain and reports the first inconsistency. A hash
chain detects accidental or naive edits and deletions in the middle of the log.
It is **not** immutable storage: an actor who can rewrite the whole file and
recompute every hash is not prevented from doing so, and truncating the newest
records cannot be detected without an externally stored head. Those limits are
reported by :func:`verify_chain` callers rather than hidden.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from .security import redact, redact_mapping

GENESIS_HASH = "0" * 64
ALGORITHM = "sha256"


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(previous_hash: str, body: dict[str, Any]) -> str:
    material = f"{previous_hash}\n{_canonical(body)}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _iter_records(path: Path) -> Iterator[tuple[int, dict[str, Any] | None]]:
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                yield number, None
                continue
            yield number, value if isinstance(value, dict) else None


def audit_head(path: Path | str) -> tuple[int, str]:
    """Return ``(sequence, hash)`` of the last chained record."""

    target = Path(path)
    sequence, head = 0, GENESIS_HASH
    if not target.exists() or target.is_symlink():
        return sequence, head
    for _, record in _iter_records(target):
        integrity = (record or {}).get("integrity")
        if isinstance(integrity, dict) and isinstance(integrity.get("hash"), str):
            try:
                sequence = int(integrity.get("seq", sequence + 1))
            except (TypeError, ValueError):
                sequence += 1
            head = integrity["hash"]
    return sequence, head


def append_chained(path: Path | str, event: dict[str, Any]) -> dict[str, Any]:
    """Append one redacted, hash-chained record and return it."""

    target = Path(path)
    if target.is_symlink():
        raise ValueError("Platform audit log must not be a symlink.")
    target.parent.mkdir(parents=True, exist_ok=True)

    sequence, previous = audit_head(target)
    body = redact_mapping(event)
    digest = _digest(previous, body)
    record = {
        **body,
        "integrity": {
            "algorithm": ALGORITHM,
            "seq": sequence + 1,
            "prev_hash": previous,
            "hash": digest,
        },
    }
    line = redact(json.dumps(record, sort_keys=True, ensure_ascii=False))
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return record


def verify_chain(path: Path | str) -> dict[str, Any]:
    """Recompute the chain and report integrity, coverage and head.

    ``unchained`` counts legacy or malformed lines; the chain restarts at the
    genesis hash after each one, so a legacy file is not reported as valid
    evidence beyond the point where chaining began.
    """

    target = Path(path)
    result: dict[str, Any] = {
        "valid": True,
        "records": 0,
        "chained": 0,
        "unchained": 0,
        "errors": [],
        "head": GENESIS_HASH,
        "seq": 0,
        "path": str(target),
    }
    if not target.exists():
        return result
    if target.is_symlink():
        result["valid"] = False
        result["errors"].append("audit path is a symlink")
        return result

    previous_seq, previous_hash = 0, GENESIS_HASH
    for number, record in _iter_records(target):
        result["records"] += 1
        if record is None:
            result["valid"] = False
            result["unchained"] += 1
            result["errors"].append(f"line {number}: invalid JSON record")
            previous_seq, previous_hash = 0, GENESIS_HASH
            continue
        integrity = record.get("integrity")
        if not isinstance(integrity, dict) or not isinstance(integrity.get("hash"), str):
            result["unchained"] += 1
            previous_seq, previous_hash = 0, GENESIS_HASH
            continue

        body = {key: value for key, value in record.items() if key != "integrity"}
        expected = _digest(previous_hash, body)
        declared_seq = integrity.get("seq")
        if declared_seq != previous_seq + 1:
            result["valid"] = False
            result["errors"].append(
                f"line {number}: sequence {declared_seq!r} does not follow {previous_seq}"
            )
        if integrity.get("prev_hash") != previous_hash:
            result["valid"] = False
            result["errors"].append(f"line {number}: previous-hash mismatch")
        if integrity.get("hash") != expected:
            result["valid"] = False
            result["errors"].append(f"line {number}: content hash mismatch")

        previous_seq += 1
        previous_hash = expected
        result["chained"] += 1
        result["head"] = expected
        result["seq"] = previous_seq

    return result
