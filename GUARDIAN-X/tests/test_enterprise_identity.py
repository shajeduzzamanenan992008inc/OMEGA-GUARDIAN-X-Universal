"""Service identity and scoped bearer token tests (ENT-05)."""

from __future__ import annotations

import pytest

from ogx.enterprise.authz import Action, PolicyEngine, Principal
from ogx.enterprise.service_identity import (
    InvalidToken,
    ServiceIdentityManager,
    hash_secret,
)
from ogx.enterprise.store import EnterpriseStore


def _setup(tmp_path):
    store = EnterpriseStore(tmp_path / "enterprise.sqlite3")
    store.initialize()
    org = store.create_organization("acme", "Acme", "owner@example.com")
    store.add_member(org["organization_id"], "admin@example.com", "admin", "owner@example.com")
    store.add_member(org["organization_id"], "dev@example.com", "developer", "owner@example.com")
    manager = ServiceIdentityManager(store, PolicyEngine())
    return store, org, manager


def _account(manager, org, scopes=None, name="ci", role="developer", actor="owner@example.com"):
    return manager.create_account(
        org["organization_id"], name, role,
        scopes or [Action.PROJECT_READ.value, Action.PROJECT_WRITE.value],
        actor,
    )


def test_service_account_scopes_must_fit_the_role(tmp_path):
    _, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    assert account["base_role"] == "developer"
    assert Action.PROJECT_WRITE.value in account["scopes"]

    with pytest.raises(ValueError):
        manager.create_account(
            org["organization_id"], "escalate", "viewer",
            [Action.PROJECT_WRITE.value], "owner@example.com",
        )
    with pytest.raises(ValueError):
        manager.create_account(
            org["organization_id"], "unknown", "developer",
            ["project.fly"], "owner@example.com",
        )
    with pytest.raises(ValueError):
        manager.create_account(org["organization_id"], "bad/name", "developer",
                               [Action.PROJECT_READ.value], "owner@example.com")
    with pytest.raises(ValueError):
        manager.create_account(org["organization_id"], "owner-role", "owner",
                               [Action.PROJECT_READ.value], "owner@example.com")


def test_only_authorized_roles_manage_service_accounts(tmp_path):
    _, org, manager = _setup(tmp_path)
    with pytest.raises(PermissionError):
        manager.create_account(org["organization_id"], "dev-made", "developer",
                               [Action.PROJECT_READ.value], "dev@example.com")
    with pytest.raises(PermissionError):
        manager.create_account(org["organization_id"], "stranger", "developer",
                               [Action.PROJECT_READ.value], "nobody@example.com")
    # An admin holds service_account.manage.
    created = manager.create_account(org["organization_id"], "admin-made", "viewer",
                                     [Action.PROJECT_READ.value], "admin@example.com")
    assert created["name"] == "admin-made"


def test_issued_token_authenticates_as_a_tenant_bound_service_principal(tmp_path):
    store, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com", 600)
    assert issued.token.startswith("ogxst.")
    principal = manager.authenticate(issued.token)
    assert isinstance(principal, Principal)
    assert principal.kind == "service"
    assert principal.tenant_id == org["organization_id"]
    assert principal.roles == ("developer",)
    assert set(principal.scopes) == set(account["scopes"])
    assert principal.display_name == "ci"

    stored = store.find_service_token(issued.token_id)
    assert stored is not None
    assert issued.token.split(".")[2] not in str(stored)
    assert stored["token_hash"] == hash_secret(issued.token.split(".")[2])
    assert store.list_service_tokens(org["organization_id"])[0]["fingerprint"].startswith("sha256:")
    assert store.list_service_tokens(org["organization_id"])[0]["last_used_at"] is not None


def test_token_scope_never_exceeds_role(tmp_path):
    store, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com")
    principal = manager.authenticate(issued.token)
    engine = PolicyEngine()
    assert engine.decide(principal, Action.PROJECT_READ).allowed
    assert not engine.decide(principal, Action.MEMBER_WRITE).allowed
    assert not engine.decide(principal, Action.SERVICE_ACCOUNT_MANAGE).allowed


def test_revoked_token_is_refused_and_listed_as_revoked(tmp_path):
    _, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com")
    result = manager.revoke_token(org["organization_id"], issued.token_id, "owner@example.com")
    assert result["revoked"] is True
    with pytest.raises(InvalidToken):
        manager.authenticate(issued.token)
    listed = manager.list_tokens(org["organization_id"], "owner@example.com")
    assert listed[0]["revoked_at"]

    with pytest.raises(PermissionError):
        manager.revoke_token(org["organization_id"], issued.token_id, "dev@example.com")


def test_expired_token_is_refused(tmp_path):
    store, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    store.insert_service_token(
        token_id="a" * 32,
        account_id=account["account_id"],
        token_hash=hash_secret("secret-value-with-enough-length"),
        fingerprint_value="sha256:deadbeef",
        scopes=account["scopes"],
        expires_at="2000-01-01T00:00:00+00:00",
        actor_subject="owner@example.com",
    )
    with pytest.raises(InvalidToken):
        manager.authenticate(f"ogxst.{'a' * 32}.{'secret-value-with-enough-length'}")


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "ogxst.short.secret", "ogxst." + "z" * 32 + ".x" * 40,
     "ogxst." + "b" * 32 + ".short"],
)
def test_malformed_tokens_are_rejected_without_enumeration(tmp_path, token):
    _, org, manager = _setup(tmp_path)
    _account(manager, org)
    with pytest.raises(InvalidToken):
        manager.authenticate(token)


def test_wrong_secret_for_a_valid_token_id_is_rejected(tmp_path):
    _, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com")
    token_id = issued.token.split(".")[1]
    with pytest.raises(InvalidToken):
        manager.authenticate(f"ogxst.{token_id}.{'x' * 43}")


def test_disabled_account_invalidates_tokens(tmp_path):
    store, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com")
    with store._connection() as conn:
        conn.execute("UPDATE service_accounts SET status='disabled' WHERE id=?",
                     (account["account_id"],))
    with pytest.raises(InvalidToken):
        manager.authenticate(issued.token)


def test_token_lifetime_bounds(tmp_path):
    _, org, manager = _setup(tmp_path)
    account = _account(manager, org)
    for ttl in (10, 31 * 24 * 3600):
        with pytest.raises(ValueError):
            manager.issue_token(org["organization_id"], account["account_id"],
                                "owner@example.com", ttl)


def test_duplicate_account_name_is_refused(tmp_path):
    _, org, manager = _setup(tmp_path)
    _account(manager, org)
    with pytest.raises(ValueError):
        _account(manager, org)


def test_tenant_isolation_between_organizations(tmp_path):
    store, org, manager = _setup(tmp_path)
    other = store.create_organization("other", "Other", "owner@other.example")
    account = _account(manager, org)
    issued = manager.issue_token(org["organization_id"], account["account_id"],
                                 "owner@example.com")
    principal = manager.authenticate(issued.token)
    engine = PolicyEngine()
    from ogx.enterprise.authz import Resource

    allowed = engine.decide(
        principal, Action.PROJECT_READ,
        Resource(kind="project", id="p", tenant_id=org["organization_id"]),
    )
    denied = engine.decide(
        principal, Action.PROJECT_READ,
        Resource(kind="project", id="p", tenant_id=other["organization_id"]),
    )
    assert allowed.allowed and not denied.allowed
    assert denied.rule == "deny.tenant_mismatch"
    with pytest.raises(ValueError):
        # The account belongs to the first tenant; the other tenant cannot use it.
        manager.issue_token(other["organization_id"], account["account_id"],
                            "owner@other.example")
