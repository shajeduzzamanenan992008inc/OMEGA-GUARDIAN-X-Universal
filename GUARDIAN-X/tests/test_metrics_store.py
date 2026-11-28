from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ogx.api.metrics_store import (
    InMemoryMetricsStorage,
    SqliteMetricsStorage,
    create_storage,
)
from ogx.api.observability import Metrics


def test_inmemory_roundtrip():
    s = InMemoryMetricsStorage()
    s.record_request("/x", 200, 0.1)
    s.record_request("/x", 500, 0.2)
    s.record_error("boom")
    snap = s.load_snapshot()
    assert snap["requests_total"] == 2
    assert snap["routes"]["/x"]["count"] == 2
    assert snap["routes"]["/x"]["errors_5xx"] == 1
    assert snap["error_codes"]["boom"] == 1
    assert s.healthy is True


def test_sqlite_persists_and_recovers(tmp_path):
    db = tmp_path / "metrics.sqlite3"
    s = SqliteMetricsStorage(db)
    s.record_request("/a", 200, 0.05)
    s.record_request("/a", 200, 0.1)
    s.record_error("e1")
    s.close()

    s2 = SqliteMetricsStorage(db)
    snap = s2.load_snapshot()
    assert snap["requests_total"] == 2
    assert snap["routes"]["/a"]["count"] == 2
    assert snap["error_codes"]["e1"] == 1
    assert s2.healthy is True


def test_sqlite_retention_cleanup(tmp_path):
    db = tmp_path / "metrics.sqlite3"
    s = SqliteMetricsStorage(db, retention_seconds=1, max_rows=2, cleanup_interval=0.0)
    for i in range(5):
        s.record_request(f"/r{i}", 200, 0.01)
    # Force cleanup by waiting past retention? Instead test max_rows bound.
    snap = s.load_snapshot()
    assert len(snap["routes"]) <= 2
    removed = s.cleanup()
    assert isinstance(removed, int)


def test_sqlite_cardinality_bound(tmp_path):
    db = tmp_path / "metrics.sqlite3"
    s = SqliteMetricsStorage(db, max_routes=3, cleanup_interval=1000.0)
    for i in range(10):
        s.record_request(f"/route{i}", 200, 0.01)
    snap = s.load_snapshot()
    assert len(snap["routes"]) <= 3 or "{overflow}" in snap["routes"]


def test_sqlite_health_on_bad_path(monkeypatch, tmp_path):
    # Point at a path that cannot be created as dir file? Use symlink guard.
    db = tmp_path / "metrics.sqlite3"
    # create a directory with same name to force failure
    db.mkdir()
    s = SqliteMetricsStorage(db)
    # storage should be unhealthy
    assert s.healthy is False
    # record_request must not raise
    s.record_request("/x", 200, 0.1)
    assert s.load_snapshot()["requests_total"] == 0


def test_metrics_hydrates_from_storage(tmp_path):
    db = tmp_path / "metrics.sqlite3"
    s = SqliteMetricsStorage(db)
    s.record_request("/h", 200, 0.05)
    s.record_request("/h", 200, 0.05)
    m = Metrics(version="v", storage=s)
    snap = m.snapshot()
    assert snap["requests_total"] == 2
    assert snap["routes"]["/h"]["count"] == 2


def test_metrics_persists_writes(tmp_path):
    db = tmp_path / "metrics.sqlite3"
    s = SqliteMetricsStorage(db)
    m = Metrics(version="v", storage=s)
    m.begin()
    m.finish("/api/v1/hosts/abc", 200, 0.02)
    m.record_error("bad")
    snap = s.load_snapshot()
    assert snap["requests_total"] == 1
    assert snap["error_codes"].get("bad") == 1


def test_concurrent_writes(tmp_path):
    db = tmp_path / "metrics.sqlite3"
    s = SqliteMetricsStorage(db)
    errors = []

    def worker():
        try:
            for _ in range(50):
                s.record_request("/c", 200, 0.001)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    snap = s.load_snapshot()
    assert snap["requests_total"] == 200
    assert snap["routes"]["/c"]["count"] == 200


def test_create_storage_factory(tmp_path):
    s = create_storage("memory")
    assert isinstance(s, InMemoryMetricsStorage)
    s2 = create_storage("sqlite", db_path=tmp_path / "m.sqlite3")
    assert isinstance(s2, SqliteMetricsStorage)


def test_metrics_storage_failure_does_not_break_finish(tmp_path):
    # unhealthy storage (directory path) must not raise during finish
    db = tmp_path / "metrics.sqlite3"
    db.mkdir()
    s = SqliteMetricsStorage(db)
    m = Metrics(version="v", storage=s)
    m.begin()
    m.finish("/health", 200, 0.01)  # must not raise
    assert m.snapshot()["requests_total"] >= 1
