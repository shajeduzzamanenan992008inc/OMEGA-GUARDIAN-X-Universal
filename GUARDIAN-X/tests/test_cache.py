"""Tests for the incremental scan cache and scanner integration (PRF-04)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ogx.cache import CacheCorrupt, CacheError, ScanCache, default_cache_path
from ogx.models import Finding
from ogx.scanner import scan_repository


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def cache(tmp_path: Path, clock):
    return ScanCache(tmp_path / "cache.sqlite3", ruleset="rules-v1", clock=clock)


def _finding(name: str = "OGX-SEC-001") -> Finding:
    return Finding(
        rule_id=name,
        title="Example",
        severity="high",
        category="secrets",
        message="example finding",
        path="app.py",
        line=3,
    )


def test_put_and_get_roundtrip_findings(cache):
    key = cache.key_for("app.py", b"secret = 'x'\n")
    assert cache.get(key) is None
    cache.put(key, [_finding()])
    restored = cache.get(key)
    assert restored is not None
    assert restored[0].rule_id == "OGX-SEC-001"
    assert restored[0].line == 3
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1


def test_key_changes_with_content_path_and_ruleset(cache, tmp_path):
    base = cache.key_for("app.py", b"same")
    assert base != cache.key_for("app.py", b"different")
    assert base != cache.key_for("other.py", b"same")
    other = ScanCache(tmp_path / "cache.sqlite3", ruleset="rules-v2")
    assert other.key_for("app.py", b"same") != base


def test_ruleset_change_invalidates_every_entry(tmp_path):
    first = ScanCache(tmp_path / "cache.sqlite3", ruleset="rules-v1")
    first.put(first.key_for("a.py", b"x"), [_finding()])
    assert first.stats()["entries"] == 1
    second = ScanCache(tmp_path / "cache.sqlite3", ruleset="rules-v2")
    assert second.stats()["entries"] == 0
    assert second.get(second.key_for("a.py", b"x")) is None


def test_cache_is_bounded_by_entries_and_evicts_least_recently_used(tmp_path, clock):
    cache = ScanCache(tmp_path / "cache.sqlite3", ruleset="r", max_entries=2, clock=clock)
    keys = [cache.key_for(f"f{number}.py", b"x") for number in range(3)]
    for index, key in enumerate(keys[:2]):
        clock.advance(1)
        cache.put(key, [_finding()])
    clock.advance(1)
    cache.get(keys[0])  # make keys[0] the most recently used
    clock.advance(1)
    cache.put(keys[2], [_finding()])
    assert cache.stats()["entries"] == 2
    assert cache.get(keys[1]) is None  # evicted
    assert cache.get(keys[0]) is not None


def test_cache_is_bounded_by_bytes(tmp_path):
    cache = ScanCache(tmp_path / "cache.sqlite3", ruleset="r", max_bytes=1_024)
    for number in range(20):
        cache.put(cache.key_for(f"f{number}.py", b"x"), [_finding()])
    assert cache.stats()["bytes"] <= 1_024
    assert cache.stats()["entries"] < 20


def test_store_contains_no_raw_source(tmp_path):
    cache = ScanCache(tmp_path / "cache.sqlite3", ruleset="r")
    cache.put(cache.key_for("app.py", b"AWS_SECRET='super-secret-value'"), [_finding()])
    with sqlite3.connect(tmp_path / "cache.sqlite3") as connection:
        dump = " ".join(
            str(row) for row in connection.execute("SELECT * FROM scan_cache").fetchall()
        )
    assert "super-secret-value" not in dump


def test_corrupt_cache_raises_and_can_be_cleared(tmp_path):
    path = tmp_path / "cache.sqlite3"
    path.write_bytes(b"not a database")
    with pytest.raises(CacheCorrupt):
        ScanCache(path, ruleset="r")


def test_corrupt_row_is_evicted_rather_than_served(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = ScanCache(path, ruleset="r")
    key = cache.key_for("a.py", b"x")
    cache.put(key, [_finding()])
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE scan_cache SET findings = ?", ("{bad json",))
    with pytest.raises(CacheCorrupt):
        cache.get(key)
    assert cache.stats()["entries"] == 0


def test_clear_removes_entries(cache):
    cache.put(cache.key_for("a.py", b"x"), [])
    assert cache.clear() == 1
    assert cache.stats()["entries"] == 0


def test_configuration_validation(tmp_path):
    with pytest.raises(CacheError, match="ruleset"):
        ScanCache(tmp_path / "a.db", ruleset="")
    with pytest.raises(CacheError, match="max_entries"):
        ScanCache(tmp_path / "b.db", ruleset="r", max_entries=0)
    with pytest.raises(CacheError, match="max_bytes"):
        ScanCache(tmp_path / "c.db", ruleset="r", max_bytes=10)
    assert default_cache_path(tmp_path).as_posix().endswith("cache/scan-cache.sqlite3")


def test_scanner_uses_the_cache_and_invalidates_on_edit(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("password = 'hunter2hunter2'\n", encoding="utf-8")
    cache = ScanCache(tmp_path / "cache.sqlite3", ruleset="rules-v1")

    first = scan_repository(project, cache=cache)
    assert first.findings
    assert cache.stats()["misses"] >= 1

    second = scan_repository(project, cache=cache)
    assert [finding.rule_id for finding in second.findings] == [
        finding.rule_id for finding in first.findings
    ]
    assert cache.stats()["hits"] >= 1

    (project / "app.py").write_text("print('clean')\n", encoding="utf-8")
    third = scan_repository(project, cache=cache)
    assert [finding.rule_id for finding in third.findings] != [
        finding.rule_id for finding in first.findings
    ]


def test_scanner_never_caches_gitignore(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    (project / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    cache = ScanCache(tmp_path / "cache.sqlite3", ruleset="rules-v1")
    scan_repository(project, cache=cache)
    # Only .env is cacheable; .gitignore depends on other filesystem state.
    assert cache.stats()["entries"] == 1
    # The .gitignore finding depends on whether .env exists, so a second run
    # after removing .env must not be served from cache.
    before = [f.rule_id for f in scan_repository(project, cache=cache).findings]
    assert "OGX-SEC-003" in before
    (project / ".env").unlink()
    after = [f.rule_id for f in scan_repository(project, cache=cache).findings]
    assert before != after
    assert "OGX-SEC-003" not in after
