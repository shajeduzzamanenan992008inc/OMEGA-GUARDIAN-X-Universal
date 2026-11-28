from __future__ import annotations

import time

from ogx.api.observability import Metrics
from ogx.enterprise.authz import PolicyEngine, Principal, Resource
from ogx.enterprise.backup import create_backup, verify_backup
from ogx.enterprise.store import EnterpriseStore


def test_metrics_write_read_throughput(tmp_path):
    metrics = Metrics(version="bench")
    start = time.perf_counter()
    for _ in range(1000):
        metrics.begin()
        metrics.finish("/api/v1/hosts/abc", 200, 0.001)
    elapsed = time.perf_counter() - start
    snap = metrics.snapshot()
    assert snap["requests_total"] == 1000
    # Synthetic: record elapsed for evidence; no hard threshold.
    print(f"METRICS_1000_OPS_SECONDS={elapsed:.4f}")


def test_authz_check_throughput():
    engine = PolicyEngine()
    p = Principal(subject="u", roles=("admin",), tenant_id="t")
    r = Resource(kind="project", id="p", tenant_id="t")
    start = time.perf_counter()
    for _ in range(5000):
        engine.decide(p, "project.read", r)
    elapsed = time.perf_counter() - start
    print(f"AUTHZ_5000_OPS_SECONDS={elapsed:.4f}")


def test_backup_verify_timing(tmp_path, monkeypatch):
    monkeypatch.setenv("OGX_HOME", str(tmp_path / "home"))
    store = EnterpriseStore(tmp_path / "e.sqlite3")
    store.initialize()
    store.create_organization("acme", "Acme", "owner@example.com")
    backup = create_backup(store, tmp_path / "backups")
    artifact = tmp_path / "backups" / backup["artifact"]

    start = time.perf_counter()
    result = verify_backup(artifact)
    elapsed = time.perf_counter() - start
    assert result["valid"] is True
    print(f"BACKUP_VERIFY_SECONDS={elapsed:.4f}")
