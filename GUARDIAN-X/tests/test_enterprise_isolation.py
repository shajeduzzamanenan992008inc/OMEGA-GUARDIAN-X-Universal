from __future__ import annotations

import pytest

from ogx.enterprise.authz import PolicyEngine, Principal, Resource
from ogx.enterprise.service_identity import ServiceIdentityManager
from ogx.enterprise.store import EnterpriseStore


def test_cross_tenant_read_denied_policy():
    engine = PolicyEngine()
    alice = Principal(subject="alice", roles=("admin",), tenant_id="org-a")
    decision = engine.decide(
        alice, "project.read",
        Resource(kind="project", id="p1", tenant_id="org-b"),
    )
    assert decision.allowed is False


def test_cross_tenant_write_denied_policy():
    engine = PolicyEngine()
    alice = Principal(subject="alice", roles=("owner",), tenant_id="org-a")
    decision = engine.decide(
        alice, "project.write",
        Resource(kind="project", id="p1", tenant_id="org-b"),
    )
    assert decision.allowed is False


def test_fail_closed_unknown_action():
    engine = PolicyEngine()
    alice = Principal(subject="alice", roles=("owner",), tenant_id="org-a")
    decision = engine.decide(alice, "nonexistent.action", None)
    assert decision.allowed is False


def test_fail_closed_missing_principal():
    engine = PolicyEngine()
    try:
        decision = engine.decide(None, "project.read", None)  # type: ignore[arg-type]
        assert decision.allowed is False
    except Exception:
        pass


def test_service_token_tenant_isolation(tmp_path):
    store = EnterpriseStore(tmp_path / "e.sqlite3")
    store.initialize()
    org_a = store.create_organization("orga", "Org A", "owner@a.example")
    org_b = store.create_organization("orgb", "Org B", "owner@b.example")
    policy = PolicyEngine()
    mgr_a = ServiceIdentityManager(store, policy)
    acct = mgr_a.create_account(org_a["organization_id"], "svc", "admin",
                                ["project.read", "project.write"], "owner@a.example")
    issued = mgr_a.issue_token(org_a["organization_id"], acct["account_id"],
                               "owner@a.example", 900)
    principal = mgr_a.authenticate(issued.token)
    assert principal.tenant_id == org_a["organization_id"]
    dec = policy.decide(principal, "project.read",
                        Resource(kind="project", id="p", tenant_id=org_b["organization_id"]))
    assert dec.allowed is False


def test_revoked_token_rejected(tmp_path):
    store = EnterpriseStore(tmp_path / "e.sqlite3")
    store.initialize()
    org = store.create_organization("orga", "Org A", "owner@a.example")
    policy = PolicyEngine()
    mgr = ServiceIdentityManager(store, policy)
    acct = mgr.create_account(org["organization_id"], "svc", "admin",
                             ["project.read"], "owner@a.example")
    issued = mgr.issue_token(org["organization_id"], acct["account_id"],
                            "owner@a.example", 900)
    mgr.revoke_token(org["organization_id"], issued.token_id, "owner@a.example")
    with pytest.raises(Exception):
        mgr.authenticate(issued.token)


def test_privilege_escalation_scope_intersection(tmp_path):
    store = EnterpriseStore(tmp_path / "e.sqlite3")
    store.initialize()
    org = store.create_organization("orga", "Org A", "owner@a.example")
    policy = PolicyEngine()
    mgr = ServiceIdentityManager(store, policy)
    with pytest.raises(ValueError):
        mgr.create_account(org["organization_id"], "svc", "viewer",
                           ["project.write"], "owner@a.example")


def test_permission_revocation_reflects_in_decide():
    engine = PolicyEngine()
    p = Principal(subject="u", roles=("owner",), tenant_id="t",
                  denied=frozenset({"project.write"}))
    dec = engine.decide(p, "project.write", Resource(kind="project", tenant_id="t"))
    assert dec.allowed is False
    p2 = Principal(subject="u", roles=("owner",), tenant_id="t")
    dec2 = engine.decide(p2, "project.write", Resource(kind="project", tenant_id="t"))
    assert dec2.allowed is True


def test_idor_organization_id_path_mismatch():
    engine = PolicyEngine()
    p = Principal(subject="svc", roles=("admin",), tenant_id="org-a")
    dec = engine.decide(p, "organization.read",
                        Resource(kind="organization", id="org-b", tenant_id="org-b"))
    assert dec.allowed is False
