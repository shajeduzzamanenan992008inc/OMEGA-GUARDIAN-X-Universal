"""HOST-13: durable cross-process host session leases, fencing and recovery."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from uuid import uuid4

import pytest

from ogx.hosts.leases import (
    HostLeaseStore,
    LeaseError,
    LeaseHeldError,
    LeaseLostError,
    LeaseNotFoundError,
    default_owner,
)
from ogx.hosts.lifecycle import (
    HostSessionManager,
    HostSessionConfig,
    HostLifecycleState,
    HostAdapter,
    HealthState,
    HostHealth,
)
from ogx.hosts.registry import HostRegistry
from ogx.registry import RegistryV2


SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def _store(tmp_path, *, name="leases.db", clock=None):
    if clock is None:
        return HostLeaseStore(tmp_path / name)
    return HostLeaseStore(tmp_path / name, clock=clock)


# --------------------------------------------------------------- lease basics
def test_acquire_release_roundtrip(tmp_path):
    store = _store(tmp_path)
    lease = store.acquire("host-a", ttl_seconds=30, owner="machine:1")

    assert lease.fencing_token == 1
    assert lease.owner == "machine:1"
    assert store.inspect("host-a") == lease
    assert store.validate("host-a", lease.lease_id, lease.fencing_token) is True

    assert store.release("host-a", lease.lease_id, lease.fencing_token) is True
    assert store.inspect("host-a") is None
    # Releasing twice is a no-op, not a crash.
    assert store.release("host-a", lease.lease_id, lease.fencing_token) is False


def test_second_owner_is_rejected_while_lease_is_live(tmp_path):
    store = _store(tmp_path)
    first = store.acquire("host-a", ttl_seconds=30, owner="machine:1")

    with pytest.raises(LeaseHeldError) as excinfo:
        store.acquire("host-a", ttl_seconds=30, owner="machine:2")

    assert excinfo.value.holder.lease_id == first.lease_id
    assert excinfo.value.holder.owner == "machine:1"
    assert excinfo.value.details["holder"]["fencing_token"] == first.fencing_token


def test_independent_hosts_do_not_block_each_other(tmp_path):
    store = _store(tmp_path)
    a = store.acquire("host-a", ttl_seconds=30)
    b = store.acquire("host-b", ttl_seconds=30)

    assert a.fencing_token == 1 and b.fencing_token == 1
    assert store.inspect("host-a").lease_id != store.inspect("host-b").lease_id


def test_invalid_ttl_and_host_id_are_rejected(tmp_path):
    store = _store(tmp_path)
    for ttl in (0, -5, float("nan")):
        with pytest.raises(LeaseError) as excinfo:
            store.acquire("host-a", ttl_seconds=ttl)
        assert excinfo.value.code == "invalid_ttl"
    with pytest.raises(LeaseError):
        store.acquire("   ")
    with pytest.raises(LeaseError):
        store.acquire("host\nX")
    with pytest.raises(LeaseError):
        store.acquire("host-a", ttl_seconds=30, owner="  ")


def test_ttl_is_clamped_to_the_supported_maximum(tmp_path):
    store = _store(tmp_path, clock=lambda: 1000.0)
    lease = store.acquire("host-a", ttl_seconds=10**9)
    assert lease.expires_at - lease.acquired_at <= 3600.0


def test_renew_requires_the_current_owner_and_an_unexpired_lease(tmp_path):
    now = [1000.0]
    store = _store(tmp_path, clock=lambda: now[0])
    lease = store.acquire("host-a", ttl_seconds=10, owner="machine:1")

    renewed = store.renew("host-a", lease.lease_id, lease.fencing_token, ttl_seconds=50)
    assert renewed.fencing_token == lease.fencing_token
    assert renewed.expires_at == pytest.approx(1050.0)

    with pytest.raises(LeaseLostError):
        store.renew("host-a", lease.lease_id, lease.fencing_token + 1, ttl_seconds=5)

    now[0] = 2000.0
    with pytest.raises(LeaseLostError) as excinfo:
        store.renew("host-a", lease.lease_id, lease.fencing_token)
    assert "expired" in str(excinfo.value)

    with pytest.raises(LeaseNotFoundError):
        store.renew("host-missing", "lease", 1)


def test_expired_lease_is_stolen_with_a_higher_fencing_token(tmp_path):
    now = [1000.0]
    store = _store(tmp_path, clock=lambda: now[0])
    first = store.acquire("host-a", ttl_seconds=10, owner="machine:1")

    now[0] = 1005.0
    with pytest.raises(LeaseHeldError):
        store.acquire("host-a", ttl_seconds=10, owner="machine:2")

    now[0] = 1011.0
    second = store.acquire("host-a", ttl_seconds=10, owner="machine:2")

    assert second.fencing_token == first.fencing_token + 1
    assert store.current_fencing_token("host-a") == second.fencing_token
    # The stale holder is fenced out on every path.
    assert store.validate("host-a", first.lease_id, first.fencing_token) is False
    assert store.release("host-a", first.lease_id, first.fencing_token) is False
    with pytest.raises(LeaseLostError):
        store.renew("host-a", first.lease_id, first.fencing_token)
    assert store.inspect("host-a").lease_id == second.lease_id


def test_fencing_tokens_are_monotonic_across_repeated_steals(tmp_path):
    now = [1000.0]
    store = _store(tmp_path, clock=lambda: now[0])
    tokens = []
    for index in range(5):
        tokens.append(store.acquire("host-a", ttl_seconds=1, owner=f"m:{index}").fencing_token)
        now[0] += 5
    assert tokens == [1, 2, 3, 4, 5]
    assert store.current_fencing_token("other-host") == 0


def test_sweep_expired_removes_only_expired_leases(tmp_path):
    now = [1000.0]
    store = _store(tmp_path, clock=lambda: now[0])
    store.acquire("host-a", ttl_seconds=1)
    store.acquire("host-b", ttl_seconds=100)

    now[0] = 1002.0
    assert store.sweep_expired() == 1
    assert store.inspect("host-a") is None
    assert store.inspect("host-b") is not None


def test_store_rejects_a_symlinked_database(tmp_path):
    target = tmp_path / "real.db"
    target.write_bytes(b"")
    link = tmp_path / "link.db"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(LeaseError) as excinfo:
        HostLeaseStore(link)
    assert excinfo.value.code == "unsafe_lease_path"


def test_default_owner_identifies_this_process():
    owner = default_owner()
    assert owner.endswith(f":{os.getpid()}")


def test_a_dead_local_owner_is_recoverable_without_waiting_for_the_ttl(tmp_path):
    import socket

    store = _store(tmp_path)
    # The context manager closes the Windows process handle on exit; without
    # that a terminated process still looks open to OpenProcess.
    with subprocess.Popen([sys.executable, "-c", "pass"]) as dead:
        dead_pid = dead.pid
        dead.wait(timeout=30)

    stale = store.acquire(
        "host-a", ttl_seconds=600, owner=f"{socket.gethostname()}:{dead_pid}"
    )
    assert stale.owner.endswith(f":{dead_pid}")

    replacement = store.acquire("host-a", ttl_seconds=60, owner=default_owner())
    assert replacement.fencing_token == stale.fencing_token + 1


def test_a_live_local_owner_still_holds_the_lease(tmp_path):
    store = _store(tmp_path)
    mine = store.acquire("host-a", ttl_seconds=600)
    assert mine.owner.endswith(f":{os.getpid()}")
    with pytest.raises(LeaseHeldError):
        # This process is alive, so its lease is not recoverable.
        store.acquire("host-a", ttl_seconds=600, owner="another:1")


def test_a_foreign_host_owner_is_only_recoverable_by_expiry(tmp_path):
    now = [1000.0]
    store = _store(tmp_path, clock=lambda: now[0])
    remote = store.acquire("host-a", ttl_seconds=30, owner="elsewhere.invalid:4242")

    with pytest.raises(LeaseHeldError):
        store.acquire("host-a", ttl_seconds=30, owner="local:1")

    now[0] += 31
    stolen = store.acquire("host-a", ttl_seconds=30, owner="local:1")
    assert stolen.fencing_token == remote.fencing_token + 1


# ------------------------------------------------------ real OS processes
def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_a_second_process_is_refused_while_the_first_holds_the_lease(tmp_path):
    db = tmp_path / "leases.db"
    # A child acquires then waits so the parent can observe a live holder.
    env = _child_env()
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import time
                from ogx.hosts.leases import HostLeaseStore
                lease = HostLeaseStore(r"{db}").acquire("host-a", ttl_seconds=60)
                print(lease.fencing_token, lease.lease_id, lease.owner, flush=True)
                time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = process.stdout.readline().strip()
        assert line, process.stderr.read()
        token, lease_id, owner = line.split(" ", 2)
        assert token == "1"

        store = HostLeaseStore(db)
        with pytest.raises(LeaseHeldError) as excinfo:
            store.acquire("host-a", ttl_seconds=60)
        assert excinfo.value.holder.lease_id == lease_id
        assert excinfo.value.holder.owner == owner
        assert owner.endswith(f":{process.pid}")
    finally:
        process.kill()
        process.wait(timeout=30)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def test_a_crashed_holder_is_recovered_after_its_lease_expires(tmp_path):
    db = tmp_path / "leases.db"
    env = _child_env()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import time
                from ogx.hosts.leases import HostLeaseStore
                lease = HostLeaseStore(r"{db}").acquire("host-a", ttl_seconds=2)
                print(lease.fencing_token, flush=True)
                time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert process.stdout.readline().strip() == "1"
    finally:
        process.kill()
        process.wait(timeout=30)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()

    store = HostLeaseStore(db)
    # The dead process cannot renew, so the lease expires and is recoverable.
    deadline = time.time() + 15
    recovered = None
    while time.time() < deadline:
        try:
            recovered = store.acquire("host-a", ttl_seconds=30, owner="recovery:1")
            break
        except LeaseHeldError:
            time.sleep(0.2)
    assert recovered is not None, "expired lease was never recoverable"
    assert recovered.fencing_token == 2
    assert store.inspect("host-a").lease_id == recovered.lease_id


def test_concurrent_processes_produce_exactly_one_winner(tmp_path):
    db = tmp_path / "leases.db"
    env = _child_env()
    script = textwrap.dedent(
        f"""
        import sys
        from ogx.hosts.leases import HostLeaseStore, LeaseHeldError
        store = HostLeaseStore(r"{db}")
        try:
            lease = store.acquire("host-race", ttl_seconds=60, owner=sys.argv[1])
        except LeaseHeldError:
            print("held")
        else:
            print(f"won {{lease.fencing_token}}")
        """
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, f"proc-{index}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for index in range(6)
    ]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        results.append(stdout.strip())

    winners = [result for result in results if result.startswith("won")]
    assert len(winners) == 1, results
    assert winners[0] == "won 1"
    assert results.count("held") == 5
    store = HostLeaseStore(db)
    assert store.current_fencing_token("host-race") == 1


# ------------------------------------------------- manager integration
class _StubAdapter(HostAdapter):
    host_type = "local_filesystem"

    def __init__(self, config: HostSessionConfig) -> None:
        super().__init__(config)
        self.connected = False

    def validate(self) -> None:
        return None

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def health(self) -> HostHealth:
        return HostHealth(state=HealthState.HEALTHY, checked_at=_now())

    def capabilities(self):
        return {"file_read": "supported"}


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _manager(tmp_path, store, registry_db="registry.db", ttl=30.0, hosts=None):
    registry = RegistryV2(tmp_path / registry_db)
    registry.initialize()
    if hosts is None:
        hosts = HostRegistry(registry=registry)
    events: list[dict] = []
    manager = HostSessionManager(
        registry=hosts,
        adapter_factory=lambda host_type, config: _StubAdapter(config),
        audit_sink=events.append,
        lease_store=store,
        lease_ttl_seconds=ttl,
        lease_owner="test-owner:1",
    )
    return manager, hosts, registry, events


def _register(hosts, tmp_path, name="root"):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    return hosts.register_host(
        host_id=uuid4(),
        host_type="local_filesystem",
        provider="local",
        display_name=name,
        normalized_identifier=str(root),
    )


def test_two_managers_cannot_own_the_same_host(tmp_path):
    store_one = HostLeaseStore(tmp_path / "leases.db")
    store_two = HostLeaseStore(tmp_path / "leases.db")
    manager_one, hosts, registry_one, _ = _manager(tmp_path, store_one)
    try:
        host = _register(hosts, tmp_path)
        session = manager_one.create_session(host.host_id)
        assert session.fencing_token == 1
        assert session.to_dict()["lease"]["owner"] == "test-owner:1"

        # A second process shares the same host registry rows.
        manager_two, _, registry_two, _ = _manager(
            tmp_path, store_two, registry_db="registry2.db", hosts=hosts
        )
        try:
            with pytest.raises(LeaseHeldError):
                manager_two.create_session(host.host_id)
            assert manager_two.list_sessions() == []
        finally:
            manager_two.shutdown()
            registry_two.close()

        manager_one.connect(session.session_id)
        manager_one.disconnect(session.session_id)
        assert store_one.inspect(host.host_id) is None
        manager_one.dispose(session.session_id)

        successor, _, registry_three, _ = _manager(
            tmp_path, store_two, registry_db="registry3.db", hosts=hosts
        )
        try:
            reclaimed = successor.create_session(host.host_id)
            assert reclaimed.fencing_token == 2
        finally:
            successor.shutdown()
            registry_three.close()
    finally:
        manager_one.shutdown()
        registry_one.close()


def test_stale_owner_is_fenced_out_of_health_and_recovery(tmp_path):
    now = [1000.0]
    clock = lambda: now[0]
    store = HostLeaseStore(tmp_path / "leases.db", clock=clock)
    manager, hosts, registry, events = _manager(tmp_path, store, ttl=5.0)
    try:
        host = _register(hosts, tmp_path)
        session = manager.create_session(host.host_id)
        manager.connect(session.session_id)
        assert session.state is HostLifecycleState.CONNECTED

        # Simulate a crash of the owner and another process recovering the host.
        now[0] += 60
        thief = HostLeaseStore(tmp_path / "leases.db", clock=clock)
        stolen = thief.acquire(host.host_id, ttl_seconds=30, owner="other-process:9")
        assert stolen.fencing_token == 2

        with pytest.raises(LeaseLostError):
            manager.health(session.session_id)
        with pytest.raises(LeaseLostError):
            manager.connect(session.session_id)

        assert any(event["action"] == "host.session.lease_lost" for event in events)
    finally:
        manager.shutdown()
        registry.close()


def test_shutdown_releases_the_lease(tmp_path):
    store = HostLeaseStore(tmp_path / "leases.db")
    manager, hosts, registry, _ = _manager(tmp_path, store)
    try:
        host = _register(hosts, tmp_path)
        session = manager.create_session(host.host_id)
        manager.connect(session.session_id)
        assert store.inspect(host.host_id) is not None
        manager.shutdown()
        assert store.inspect(host.host_id) is None
    finally:
        registry.close()


def test_manager_without_a_lease_store_keeps_previous_behaviour(tmp_path):
    registry = RegistryV2(tmp_path / "registry.db")
    registry.initialize()
    hosts = HostRegistry(registry=registry)
    manager = HostSessionManager(
        registry=hosts,
        adapter_factory=lambda host_type, config: _StubAdapter(config),
    )
    try:
        host = _register(hosts, tmp_path)
        session = manager.create_session(host.host_id)
        assert session.lease is None
        assert session.fencing_token is None
        manager.connect(session.session_id)
        assert session.state is HostLifecycleState.CONNECTED
    finally:
        manager.shutdown()
        registry.close()


def test_initialise_waits_out_a_locked_database(tmp_path):
    """A concurrent initialiser that holds the database lock must not fail the store."""

    import sqlite3
    import threading

    db = tmp_path / "hosts" / "leases.db"
    db.parent.mkdir(parents=True)
    blocker = sqlite3.connect(str(db), isolation_level=None, timeout=1.0, check_same_thread=False)
    blocker.execute("CREATE TABLE IF NOT EXISTS warmup (id INTEGER)")
    blocker.execute("BEGIN EXCLUSIVE")
    released = threading.Event()

    def release_soon() -> None:
        time.sleep(0.4)
        blocker.execute("COMMIT")
        blocker.close()
        released.set()

    thread = threading.Thread(target=release_soon, daemon=True)
    thread.start()
    try:
        store = HostLeaseStore(db)
    finally:
        thread.join(timeout=5)

    assert released.is_set(), "the store did not wait for the competing initialiser"
    assert store.path == db
    lease = store.acquire("host-lock-wait", owner="owner", ttl_seconds=60)
    assert lease.fencing_token == 1
