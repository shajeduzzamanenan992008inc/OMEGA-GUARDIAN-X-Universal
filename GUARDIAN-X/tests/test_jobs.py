"""Tests for the bounded durable job queue, retries and DLQ (ENT-07/PRF-03)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from ogx.jobs import (
    JobNotFound,
    JobQueue,
    LeaseLost,
    MAX_PAYLOAD_BYTES,
    QueueError,
    QueueFull,
    default_queue_path,
    run_worker,
)


class Clock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def queue(tmp_path: Path, clock):
    return JobQueue(tmp_path / "queue.sqlite3", max_pending=4, max_attempts=3, backoff_seconds=10, clock=clock)


def test_enqueue_claim_complete(queue):
    job, created = queue.enqueue("scan.run", {"target": "repo"})
    assert created is True
    assert job.state == "pending" and job.attempts == 0

    claimed = queue.claim("worker-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.state == "leased"
    assert claimed.attempts == 1
    assert claimed.lease_until is not None

    done = queue.complete(job.id, "worker-1")
    assert done.state == "done"
    assert queue.stats()["counts"]["done"] == 1
    assert queue.claim("worker-1") is None


def test_capacity_is_bounded_and_rejected(queue):
    for number in range(4):
        queue.enqueue("scan.run", {"n": number})
    with pytest.raises(QueueFull, match="capacity"):
        queue.enqueue("scan.run", {"n": 5})
    assert queue.stats()["saturated"] is True
    assert queue.stats()["utilization"] == 1.0

    queue.claim("worker")
    # A leased job still occupies capacity.
    with pytest.raises(QueueFull):
        queue.enqueue("scan.run", {"n": 6})


def test_dedupe_key_prevents_duplicate_submission(queue):
    first, created = queue.enqueue("scan.run", {"target": "a"}, dedupe_key="scan:a")
    again, created_again = queue.enqueue("scan.run", {"target": "a"}, dedupe_key="scan:a")
    assert created is True and created_again is False
    assert first.id == again.id
    assert len(queue.list_jobs()) == 1


def test_validation_rejects_bad_jobs(queue):
    with pytest.raises(QueueError, match="kind"):
        queue.enqueue("Not A Kind")
    with pytest.raises(QueueError, match="payload"):
        queue.enqueue("scan.run", {"blob": "x" * (MAX_PAYLOAD_BYTES + 1)})
    with pytest.raises(QueueError, match="dedupe_key"):
        queue.enqueue("scan.run", {}, dedupe_key="x" * 201)
    with pytest.raises(QueueError, match="delay"):
        queue.enqueue("scan.run", {}, delay_seconds=-1)


def test_retry_backoff_then_dead_letter(queue, clock):
    job, _ = queue.enqueue("scan.run", {})
    queue.claim("w")
    failed = queue.fail(job.id, "w", "boom")
    assert failed.state == "pending"
    assert failed.attempts == 1
    assert failed.available_at == clock.now + 10  # first backoff

    # Not yet available.
    assert queue.claim("w") is None
    clock.advance(10)
    second = queue.claim("w")
    assert second is not None and second.attempts == 2
    queue.fail(job.id, "w", "boom again")
    clock.advance(20)  # exponential backoff
    third = queue.claim("w")
    assert third is not None and third.attempts == 3
    dead = queue.fail(job.id, "w", "final")
    assert dead.state == "dead"
    assert queue.stats()["dead"] == 1
    assert queue.claim("w") is None


def test_dead_letter_requires_approval_to_retry(queue, clock):
    job, _ = queue.enqueue("scan.run", {})
    queue.claim("w")
    queue.fail(job.id, "w", "fatal")
    clock.advance(10)
    assert queue.claim("w") is not None
    queue.fail(job.id, "w", "fatal")
    clock.advance(20)
    assert queue.claim("w") is not None
    queue.fail(job.id, "w", "fatal")
    assert queue.get(job.id).state == "dead"

    with pytest.raises(QueueError, match="approval"):
        queue.retry_dead(job.id, approved=False)
    requeued = queue.retry_dead(job.id, approved=True)
    assert requeued.state == "pending"
    assert requeued.attempts == 0

    with pytest.raises(JobNotFound):
        queue.retry_dead("missing", approved=True)


def test_error_text_is_truncated_and_url_credentials_are_redacted(queue):
    job, _ = queue.enqueue("scan.run", {})
    queue.claim("w")
    queue.fail(job.id, "w", "clone failed https://user:pass@example.invalid/repo " + "x" * 800)
    stored = queue.get(job.id).last_error
    assert "user:pass" not in stored
    assert "https://***@example.invalid/repo" in stored
    assert len(stored) <= 500


def test_lease_cannot_be_completed_by_another_worker(queue):
    job, _ = queue.enqueue("scan.run", {})
    queue.claim("worker-a")
    with pytest.raises(LeaseLost):
        queue.complete(job.id, "worker-b")
    with pytest.raises(LeaseLost):
        queue.fail(job.id, "worker-b", "nope")
    assert queue.complete(job.id, "worker-a").state == "done"


def test_expired_leases_are_reaped_and_eventually_dead_lettered(queue, clock):
    job, _ = queue.enqueue("scan.run", {})
    queue.claim("w", lease_seconds=5)
    clock.advance(6)
    assert queue.reap_expired() == 1
    pending = queue.get(job.id)
    assert pending.state == "pending"
    assert pending.worker is None
    # attempts already counted the crashed claim
    assert pending.attempts == 1

    queue.claim("w", lease_seconds=5)
    clock.advance(6)
    queue.reap_expired()
    queue.claim("w", lease_seconds=5)
    clock.advance(6)
    assert queue.reap_expired() == 1
    assert queue.get(job.id).state == "dead"


def test_worker_runs_jobs_and_records_handler_failures(queue):
    queue.enqueue("ok.one", {})
    queue.enqueue("bad.one", {})
    seen: list[str] = []

    def handler(job):
        seen.append(job.kind)
        if job.kind == "bad.one":
            raise RuntimeError("handler exploded")

    report = run_worker(queue, handler, worker_id="w1", max_jobs=10)
    assert report["processed"] == 2
    assert report["succeeded"] == 1
    assert report["failed"] == 1
    assert set(seen) == {"ok.one", "bad.one"}
    bad = [job for job in queue.list_jobs() if job.kind == "bad.one"][0]
    assert bad.state == "pending"
    assert "RuntimeError" in bad.last_error


def test_concurrent_workers_never_claim_the_same_job(tmp_path: Path, clock):
    queue = JobQueue(tmp_path / "q.sqlite3", max_pending=100, clock=clock)
    for number in range(20):
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

    threads = [threading.Thread(target=worker, args=(f"w{index}",)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(claimed) == 20
    assert len(set(claimed)) == 20


def test_purge_only_removes_finished_jobs(queue, clock):
    done, _ = queue.enqueue("scan.run", {})
    queue.claim("w")
    queue.complete(done.id, "w")
    waiting, _ = queue.enqueue("scan.run", {})
    clock.advance(60)
    assert queue.purge_done(older_than_seconds=30) == 1
    assert queue.get(done.id) is None
    assert queue.get(waiting.id) is not None


def test_constructor_rejects_unsafe_configuration(tmp_path: Path):
    with pytest.raises(QueueError, match="max_pending"):
        JobQueue(tmp_path / "a.sqlite3", max_pending=0)
    with pytest.raises(QueueError, match="max_attempts"):
        JobQueue(tmp_path / "b.sqlite3", max_attempts=0)
    with pytest.raises(QueueError, match="lease_seconds"):
        JobQueue(tmp_path / "c.sqlite3", lease_seconds=0)
    with pytest.raises(QueueError, match="backoff_seconds"):
        JobQueue(tmp_path / "d.sqlite3", backoff_seconds=0)


def test_default_queue_path_is_outside_the_project_state(tmp_path: Path):
    assert default_queue_path(tmp_path).as_posix().endswith("jobs/queue.sqlite3")
