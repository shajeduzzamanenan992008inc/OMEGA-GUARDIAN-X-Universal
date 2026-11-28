"""Real PostgreSQL verification for ENT-02, ENT-07 and the API readiness probe.

These tests are skipped unless ``OGX_TEST_POSTGRES_URL`` points at a PostgreSQL
server where the role may create databases. They create a throwaway database,
never touch an existing application database, and drop it afterwards.

Run locally:

    $env:OGX_TEST_POSTGRES_URL = "postgresql://postgres@127.0.0.1:55432/ogx_test"
    python -m pytest -q tests/test_postgres_backend.py
"""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from ogx.api.server import ApiContext, create_server  # noqa: E402
from ogx.enterprise.authz import PolicyEngine  # noqa: E402
from ogx.enterprise.database import (  # noqa: E402
    DatabaseError,
    PostgresDatabase,
    redact_database_url,
)
from ogx.enterprise.service_identity import ServiceIdentityManager  # noqa: E402
from ogx.enterprise.store import EnterpriseStore  # noqa: E402
from ogx.jobs import (  # noqa: E402
    JobNotFound,
    LeaseLost,
    PostgresJobQueue,
    QueueError,
    QueueFull,
)

ADMIN_URL = os.environ.get("OGX_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="OGX_TEST_POSTGRES_URL is not configured"
)


def _admin_connection():
    connection = psycopg2.connect(ADMIN_URL, connect_timeout=5)
    connection.autocommit = True
    return connection


def _database_url(database: str) -> str:
    parsed = urlsplit(ADMIN_URL)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", "", ""))


def _terminate(connection, database: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname=%s AND pid<>pg_backend_pid()",
            (database,),
        )


@pytest.fixture(scope="session")
def pg_url():
    admin = _admin_connection()
    name = f"ogx_verify_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{name}"')
    url = _database_url(name)
    try:
        yield url
    finally:
        _terminate(admin, name)
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


@pytest.fixture()
def store(pg_url):
    instance = EnterpriseStore(database_url=pg_url)
    instance.initialize()
    yield instance
    # Clean every application table between tests without recreating the DB.
    tables = sorted(instance.table_names() - {"schema_migrations"})
    if tables:
        with instance._connection() as connection:
            connection.execute(
                "TRUNCATE " + ", ".join(f'"{table}"' for table in tables) + " CASCADE"
            )
    instance.close()


@pytest.fixture()
def queue(pg_url):
    instance = PostgresJobQueue(pg_url, max_pending=100, max_attempts=3,
                                backoff_seconds=5, lease_seconds=60)
    with instance._db.connection() as connection:
        connection.execute("TRUNCATE jobs")
    yield instance
    with instance._db.connection() as connection:
        connection.execute("TRUNCATE jobs")
    instance.close()


class Clock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------- store
def test_store_crud_health_and_schema_version(store):
    assert store.backend == "postgresql"
    health = store.health()
    assert health["ok"] is True
    assert health["schema_version"] == 1
    assert health["tables"] >= 10

    org = store.create_organization("acme", "Acme", "owner@example.com")
    store.add_member(org["organization_id"], "dev@example.com", "developer",
                     "owner@example.com")
    project = store.register_project(org["organization_id"], "api", "github",
                                     "https://github.com/acme/api", "owner@example.com")
    record = store.create_governance_record(
        org["organization_id"], "risk", "Postgres failover", "owner@example.com",
        "high", "open", "owner@example.com",
    )
    assert project["project_id"]
    assert record["record_id"]
    assert [item["name"] for item in store.list_projects(org["organization_id"],
                                                         "owner@example.com")] == ["api"]
    assert store.list_governance_records(org["organization_id"],
                                         "owner@example.com")[0]["title"] == "Postgres failover"
    events = store.list_audit_events(org["organization_id"], "owner@example.com")
    assert {event["action"] for event in events} >= {
        "organization.create", "membership.create", "project.register",
        "governance.risk.create",
    }


def test_migrations_are_idempotent(store):
    first = store.schema_version()
    store.initialize()
    store.initialize()
    assert store.schema_version() == first == 1
    assert "schema_migrations" in store.table_names()


def test_initialize_completes_a_partial_legacy_schema(pg_url):
    admin = _admin_connection()
    name = f"ogx_partial_{uuid.uuid4().hex[:10]}"
    with admin.cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{name}"')
    url = _database_url(name)
    try:
        connection = psycopg2.connect(url)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE organizations (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,"
                " display_name TEXT NOT NULL, created_at TEXT NOT NULL,"
                " status TEXT NOT NULL DEFAULT 'active')"
            )
        connection.close()
        instance = EnterpriseStore(database_url=url)
        instance.initialize()
        assert instance.schema_version() == 1
        assert {"users", "projects", "audit_events"} <= instance.table_names()
        instance.close()
    finally:
        _terminate(admin, name)
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def test_connection_loss_is_detected_and_the_pool_recovers(pg_url):
    database = PostgresDatabase(pg_url, pool_size=2)
    try:
        assert database.ping()["ok"] is True
        admin = _admin_connection()
        try:
            database_name = urlsplit(pg_url).path.lstrip("/")
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname=%s AND pid<>pg_backend_pid()",
                    (database_name,),
                )
        finally:
            admin.close()
        with pytest.raises(DatabaseError):
            database.ping()
        assert database.ping()["ok"] is True
    finally:
        database.close()


def test_cross_tenant_reads_and_writes_are_isolated(store):
    alpha = store.create_organization("alpha", "Alpha", "owner@alpha.test")
    beta = store.create_organization("beta", "Beta", "owner@beta.test")
    store.register_project(alpha["organization_id"], "secret", "local", None,
                           "owner@alpha.test")
    store.register_project(beta["organization_id"], "public", "local", None,
                           "owner@beta.test")

    alpha_items = store.list_projects_scoped(alpha["organization_id"])
    beta_items = store.list_projects_scoped(beta["organization_id"])
    assert [item["name"] for item in alpha_items["items"]] == ["secret"]
    assert [item["name"] for item in beta_items["items"]] == ["public"]
    with pytest.raises(PermissionError):
        store.list_projects(alpha["organization_id"], "owner@beta.test")
    with pytest.raises(PermissionError):
        store.register_project(alpha["organization_id"], "intrusion", "local", None,
                               "owner@beta.test")


def test_service_identity_token_and_idempotency(store):
    org = store.create_organization("acme", "Acme", "owner@example.com")
    manager = ServiceIdentityManager(store, PolicyEngine())
    account = manager.create_account(
        org["organization_id"], "ci", "developer", ["project.read"],
        "owner@example.com",
    )
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com", ttl_seconds=600)
    assert issued.token.startswith("ogxst.")
    principal = manager.authenticate(issued.token)
    assert principal.tenant_id == org["organization_id"]

    store.save_idempotent(org["organization_id"], "key-12345678", "POST /projects",
                          "digest-1", {"ok": True}, 201)
    first = store.get_idempotent(org["organization_id"], "key-12345678")
    store.save_idempotent(org["organization_id"], "key-12345678", "POST /projects",
                          "digest-2", {"ok": False}, 409)
    second = store.get_idempotent(org["organization_id"], "key-12345678")
    assert first["response"] == {"ok": True}
    assert second["request_digest"] == "digest-2"
    assert second["status_code"] == 409


def test_failed_transaction_does_not_leave_partial_rows(store):
    org = store.create_organization("acme", "Acme", "owner@example.com")
    before = store.list_members_scoped(org["organization_id"])["total"]
    with pytest.raises(Exception):
        store.create_organization("acme", "Duplicate", "second@example.com")
    after = store.list_members_scoped(org["organization_id"])["total"]
    assert after == before
    # The second owner must not have been inserted by the failed transaction.
    with store._connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM users WHERE external_subject=?",
            ("second@example.com",),
        ).fetchone()
    assert row["total"] == 0


def test_connection_pool_and_health_degradation(pg_url):
    database = PostgresDatabase(pg_url, pool_size=2)
    assert database.ping()["ok"] is True
    first = database.connect()
    second = database.connect()
    first.execute("SELECT 1").fetchone()
    second.execute("SELECT 1").fetchone()
    first.close()
    second.close()
    database.close()
    with pytest.raises(DatabaseError):
        database.connect()
    assert redact_database_url(pg_url).count("@") == 0


# --------------------------------------------------------------------- queue
def test_queue_dedupe_capacity_and_lifecycle(queue):
    job, created = queue.enqueue("scan.run", {"target": "a"}, dedupe_key="scan:a")
    again, created_again = queue.enqueue("scan.run", {"target": "a"}, dedupe_key="scan:a")
    assert created is True and created_again is False
    assert job.id == again.id

    claimed = queue.claim("worker-1")
    assert claimed is not None and claimed.state == "leased" and claimed.attempts == 1
    extended = queue.heartbeat(claimed.id, "worker-1", extend_seconds=120)
    assert extended.lease_until > claimed.lease_until
    with pytest.raises(LeaseLost):
        queue.heartbeat(claimed.id, "worker-2")
    done = queue.complete(claimed.id, "worker-1")
    assert done.state == "done"
    assert queue.claim("worker-1") is None
    with pytest.raises(LeaseLost):
        queue.complete(claimed.id, "worker-1")


def test_queue_retry_backoff_and_dead_letter(pg_url):
    clock = Clock()
    queue = PostgresJobQueue(pg_url, max_pending=10, max_attempts=2,
                             backoff_seconds=5, clock=clock)
    try:
        with queue._db.connection() as connection:
            connection.execute("TRUNCATE jobs")
        job, _ = queue.enqueue("scan.run", {"x": 1})
        first = queue.claim("w")
        assert first is not None
        retried = queue.fail(first.id, "w", "boom")
        assert retried.state == "pending"
        assert retried.available_at == clock.now + 5
        assert retried.last_error == "boom"

        clock.advance(5)
        second = queue.claim("w")
        assert second is not None
        dead = queue.fail(second.id, "w", "boom again")
        assert dead.state == "dead"
        with pytest.raises(QueueError, match="approval"):
            queue.retry_dead(job.id, approved=False)
        revived = queue.retry_dead(job.id, approved=True)
        assert revived.state == "pending" and revived.attempts == 0
    finally:
        queue.close()


def test_queue_capacity_is_exact_under_concurrency(pg_url):
    queue = PostgresJobQueue(pg_url, max_pending=20, max_attempts=3)
    try:
        with queue._db.connection() as connection:
            connection.execute("TRUNCATE jobs")
        results: list[bool] = []
        lock = threading.Lock()

        def submit(number: int) -> None:
            try:
                queue.enqueue("scan.run", {"n": number})
                accepted = True
            except QueueFull:
                accepted = False
            with lock:
                results.append(accepted)

        threads = [threading.Thread(target=submit, args=(number,)) for number in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(results) == 20
        assert queue.stats()["pending"] == 20
        with pytest.raises(QueueFull):
            queue.enqueue("scan.run", {"n": 999})
    finally:
        queue.close()


def test_concurrent_claiming_never_hands_out_a_job_twice(queue):
    for number in range(40):
        queue.enqueue("scan.run", {"n": number})
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        while True:
            job = queue.claim(name)
            if job is None:
                return
            with lock:
                claimed.append(job.id)
            queue.complete(job.id, name)

    threads = [threading.Thread(target=worker, args=(f"w{number}",)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == 40
    assert len(set(claimed)) == 40
    assert queue.stats()["counts"]["done"] == 40


def _claim_process(url: str, output: str) -> None:
    queue = PostgresJobQueue(url, max_pending=200)
    claimed: list[str] = []
    while True:
        job = queue.claim(f"pid-{os.getpid()}")
        if job is None:
            break
        claimed.append(job.id)
        queue.complete(job.id, f"pid-{os.getpid()}")
    Path(output).write_text(json.dumps(claimed), encoding="utf-8")
    queue.close()


def test_multi_process_workers_claim_each_job_once(pg_url, tmp_path):
    queue = PostgresJobQueue(pg_url, max_pending=100)
    with queue._db.connection() as connection:
        connection.execute("TRUNCATE jobs")
    for number in range(24):
        queue.enqueue("scan.run", {"n": number})
    queue.close()

    context = multiprocessing.get_context("spawn")
    outputs = [tmp_path / f"worker-{number}.json" for number in range(4)]
    processes = [
        context.Process(target=_claim_process, args=(pg_url, str(output)))
        for output in outputs
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0
    claimed: list[str] = []
    for output in outputs:
        claimed.extend(json.loads(output.read_text(encoding="utf-8")))
    assert len(claimed) == 24
    assert len(set(claimed)) == 24


def test_lease_expiry_reaps_to_pending_or_dead(pg_url):
    clock = Clock()
    queue = PostgresJobQueue(pg_url, max_pending=10, max_attempts=1, clock=clock,
                             lease_seconds=10)
    try:
        with queue._db.connection() as connection:
            connection.execute("TRUNCATE jobs")
        queue.enqueue("scan.run", {"x": 1})
        claimed = queue.claim("w")
        assert claimed is not None
        clock.advance(11)
        assert queue.reap_expired() == 1
        assert queue.get(claimed.id).state == "dead"
    finally:
        queue.close()


# ----------------------------------------------------------------------- API
def test_api_readiness_and_project_route_over_postgres(store):
    org = store.create_organization("acme", "Acme", "owner@example.com")
    store.register_project(org["organization_id"], "api", "local", None,
                           "owner@example.com")
    manager = ServiceIdentityManager(store, store.policy)
    account = manager.create_account(
        org["organization_id"], "dash", "viewer", ["project.read", "dashboard.view"],
        "owner@example.com",
    )
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com", ttl_seconds=600)
    context = ApiContext(store=store)
    server = create_server(context, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import http.client

        port = server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/ready")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert {check["name"] for check in payload["checks"]} >= {"database", "schema"}
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", f"/api/v1/organizations/{org['organization_id']}/projects",
                           headers={"Authorization": f"Bearer {issued.token}"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert [item["name"] for item in payload["items"]] == ["api"]
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
