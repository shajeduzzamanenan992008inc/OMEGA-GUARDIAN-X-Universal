"""Authorization policy tests (ENT-04, UI-06)."""

from __future__ import annotations

import argparse

import pytest

from ogx.cli import cmd_authz
from ogx.enterprise.authz import (
    Action,
    AuthorizationError,
    PolicyEngine,
    Principal,
    READ_ONLY_ACTIONS,
    ROLE_PERMISSIONS,
    Resource,
    permissions_for_roles,
    principal_from_membership,
    validate_scopes,
)


def _user(role: str, tenant: str = "tenant-a") -> Principal:
    return principal_from_membership(f"{role}@example.com", role, tenant)


def test_matrix_is_explicit_and_deny_by_default():
    matrix = PolicyEngine.matrix()
    assert set(matrix) == set(ROLE_PERMISSIONS)
    assert set(matrix["owner"]) == {action.value for action in Action}
    assert Action.PROJECT_DELETE.value in matrix["owner"]
    assert Action.ORGANIZATION_DELETE.value not in matrix["admin"]
    assert Action.PROJECT_DELETE.value in matrix["admin"]
    assert Action.PROJECT_WRITE.value in matrix["developer"]
    assert Action.WORKFLOW_APPROVE.value not in matrix["developer"]
    assert Action.AUDIT_EXPORT.value in matrix["auditor"]
    assert Action.PROJECT_READ.value in matrix["viewer"]
    assert Action.PROJECT_WRITE.value not in matrix["viewer"]


def test_read_only_actions_are_derived_from_the_matrix():
    assert READ_ONLY_ACTIONS
    # Member reads are auditor-and-above, so the union of every read action is
    # covered by owner; the auditor role covers the rest of the read surface.
    for action in READ_ONLY_ACTIONS:
        assert action in PolicyEngine.matrix()["owner"], action
    for action in READ_ONLY_ACTIONS - {"member.read", "secret.read"}:
        assert action in PolicyEngine.matrix()["auditor"], action
    assert Action.PROJECT_DELETE.value not in READ_ONLY_ACTIONS


def test_role_permissions_are_unions_over_roles():
    combined = permissions_for_roles(("viewer", "auditor"))
    assert Action.AUDIT_EXPORT.value in combined
    assert Action.PROJECT_DELETE.value not in combined


@pytest.mark.parametrize(
    "role, allowed, denied",
    [
        ("owner", Action.ORGANIZATION_DELETE, None),
        ("admin", Action.MEMBER_WRITE, Action.ORGANIZATION_DELETE),
        ("developer", Action.PROJECT_WRITE, Action.MEMBER_WRITE),
        ("auditor", Action.AUDIT_EXPORT, Action.PROJECT_WRITE),
        ("viewer", Action.PROJECT_READ, Action.PROJECT_WRITE),
    ],
)
def test_role_decisions(role, allowed, denied):
    engine = PolicyEngine()
    tenant = "tenant-a"
    resource = Resource(kind="organization", id=tenant, tenant_id=tenant)
    assert engine.decide(_user(role, tenant), allowed, resource).allowed
    if denied is not None:
        assert not engine.decide(_user(role, tenant), denied, resource).allowed


def test_unknown_action_and_unknown_role_are_denied():
    engine = PolicyEngine()
    decision = engine.decide(_user("owner"), "project.teleport")
    assert not decision.allowed
    assert decision.rule == "deny.unknown_action"

    impostor = Principal(subject="mallory", roles=("superuser",), tenant_id="tenant-a")
    decision = engine.decide(impostor, Action.PROJECT_READ)
    assert not decision.allowed
    assert decision.rule == "deny.unknown_role"


def test_no_principal_is_denied():
    decision = PolicyEngine().decide(None, Action.PROJECT_READ)
    assert not decision.allowed
    assert decision.rule == "deny.no_principal"


def test_tenant_mismatch_is_denied_even_for_owner():
    engine = PolicyEngine()
    resource = Resource(kind="project", id="p1", tenant_id="tenant-b")
    decision = engine.decide(_user("owner", "tenant-a"), Action.PROJECT_READ, resource)
    assert not decision.allowed
    assert decision.rule == "deny.tenant_mismatch"


def test_unbound_principal_cannot_touch_tenant_resource():
    engine = PolicyEngine()
    principal = Principal(subject="floating", roles=("owner",), tenant_id=None)
    resource = Resource(kind="project", id="p1", tenant_id="tenant-a")
    decision = engine.decide(principal, Action.PROJECT_READ, resource)
    assert not decision.allowed
    assert decision.rule == "deny.tenant_missing"


def test_explicit_deny_wins_over_role_grant():
    principal = Principal(
        subject="contractor@example.com",
        roles=("admin",),
        tenant_id="tenant-a",
        denied=frozenset({Action.MEMBER_WRITE.value}),
    )
    engine = PolicyEngine()
    resource = Resource(kind="organization", id="tenant-a", tenant_id="tenant-a")
    assert not engine.decide(principal, Action.MEMBER_WRITE, resource).allowed
    assert engine.decide(principal, Action.PROJECT_READ, resource).allowed


def test_service_scope_can_only_narrow_the_role():
    engine = PolicyEngine()
    resource = Resource(kind="organization", id="tenant-a", tenant_id="tenant-a")
    scoped = Principal(
        subject="service:tenant-a/ci",
        kind="service",
        roles=("admin",),
        tenant_id="tenant-a",
        scopes=tuple(sorted({Action.PROJECT_READ.value})),
    )
    assert engine.decide(scoped, Action.PROJECT_READ, resource).allowed
    decision = engine.decide(scoped, Action.PROJECT_WRITE, resource)
    assert not decision.allowed
    assert decision.rule == "deny.scope"

    overreach = Principal(
        subject="service:tenant-a/ci",
        kind="service",
        roles=("viewer",),
        tenant_id="tenant-a",
        scopes=(Action.PROJECT_WRITE.value,),
    )
    decision = engine.decide(overreach, Action.PROJECT_WRITE, resource)
    assert not decision.allowed
    assert decision.rule == "deny.role"


def test_service_principal_requires_explicit_scopes():
    with pytest.raises(ValueError):
        Principal(subject="svc", kind="service", roles=("admin",))


def test_break_glass_requires_admin_role_and_reason():
    engine = PolicyEngine()
    owner = _user("owner")
    short = engine.decide(owner, Action.BREAK_GLASS, context={"reason": "emergency"})
    assert not short.allowed
    assert short.rule == "deny.break_glass_reason"

    missing = engine.decide(owner, Action.BREAK_GLASS, context={})
    assert not missing.allowed

    granted = engine.decide(
        owner, Action.BREAK_GLASS, context={"reason": "rotate compromised deploy key now"}
    )
    assert granted.allowed
    assert granted.rule == "allow.break_glass"

    developer = engine.decide(
        _user("developer"), Action.BREAK_GLASS, context={"reason": "rotate compromised key now"}
    )
    assert not developer.allowed


def test_require_raises_authorization_error_with_decision():
    engine = PolicyEngine()
    with pytest.raises(AuthorizationError) as caught:
        engine.require(_user("viewer"), Action.PROJECT_DELETE)
    assert caught.value.decision.allowed is False
    assert caught.value.code == "authorization_denied"


def test_audit_sink_records_every_decision():
    events: list[dict] = []
    engine = PolicyEngine(audit_sink=events.append)
    engine.decide(_user("viewer"), Action.PROJECT_READ)
    engine.decide(_user("viewer"), Action.PROJECT_DELETE)
    assert [event["allowed"] for event in events] == [True, False]
    assert all(event["event"] == "authorization.decision" for event in events)


def test_audit_sink_failure_never_blocks_a_decision():
    def sink(_event):
        raise RuntimeError("audit storage is down")

    decision = PolicyEngine(audit_sink=sink).decide(_user("owner"), Action.PROJECT_READ)
    assert decision.allowed


def test_explain_reports_effective_permissions():
    report = PolicyEngine.explain(_user("developer"), Action.WORKFLOW_RUN)
    assert report["decision"]["allowed"] is True
    assert Action.WORKFLOW_RUN.value in report["effective_permissions"]
    assert Action.MEMBER_WRITE.value not in report["effective_permissions"]


def test_validate_scopes_rejects_unknown_and_empty():
    assert validate_scopes(["project.read", "project.read"]) == ("project.read",)
    with pytest.raises(ValueError):
        validate_scopes(["project.fly"])
    with pytest.raises(ValueError):
        validate_scopes([])


def test_cli_authz_check_exit_codes(capsys):
    allowed = argparse.Namespace(
        authz_action="check", action="project.read", roles="viewer", scopes=None,
        tenant="tenant-a", resource_kind=None, resource_id=None, subject="cli",
        reason=None, json=True,
    )
    assert cmd_authz(allowed) == 0
    denied = argparse.Namespace(
        authz_action="check", action="project.delete", roles="viewer", scopes=None,
        tenant="tenant-a", resource_kind=None, resource_id=None, subject="cli",
        reason=None, json=True,
    )
    assert cmd_authz(denied) == 1
    output = capsys.readouterr().out
    assert '"allowed": false' in output


def test_cli_authz_matrix_outputs_every_role(capsys):
    args = argparse.Namespace(authz_action="matrix", json=True)
    assert cmd_authz(args) == 0
    output = capsys.readouterr().out
    for role in ROLE_PERMISSIONS:
        assert role in output
