"""Unified authorization policy shared by every OGX interface (ENT-04, UI-06).

One explicit policy engine decides for the CLI, the REST API, the dashboard and
background workers. It is deliberately small so that the decision can be read
and audited:

* a static role matrix (``owner``/``admin``/``developer``/``auditor``/``viewer``),
* an optional per-principal explicit **deny** list that always wins,
* tenant binding, so a principal can never act on another tenant's object,
* scope narrowing for service identities (a token can never exceed its role),
* break-glass that requires an explicit reason and still produces an audit
  record, and
* deny-by-default for unknown actions, unknown roles and missing principals.

The engine is a *policy decision* component only. Enforcement still has to call
``decide``/``require`` on every boundary; the REST API and the CLI commands that
use it are wired that way, and ``tests/test_authz.py`` asserts parity between
interfaces for the same principal and action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Mapping


class Action(str, Enum):
    """Every authorization action the product recognises."""

    ORGANIZATION_READ = "organization.read"
    ORGANIZATION_WRITE = "organization.write"
    ORGANIZATION_DELETE = "organization.delete"

    MEMBER_READ = "member.read"
    MEMBER_WRITE = "member.write"

    PROJECT_READ = "project.read"
    PROJECT_WRITE = "project.write"
    PROJECT_DELETE = "project.delete"

    HOST_READ = "host.read"
    HOST_CONNECT = "host.connect"
    HOST_WRITE = "host.write"

    WORKFLOW_READ = "workflow.read"
    WORKFLOW_RUN = "workflow.run"
    WORKFLOW_APPROVE = "workflow.approve"

    JOB_READ = "job.read"
    JOB_ENQUEUE = "job.enqueue"
    JOB_REQUEUE = "job.requeue"

    PLUGIN_READ = "plugin.read"
    PLUGIN_INSTALL = "plugin.install"
    PLUGIN_GRANT = "plugin.grant"
    PLUGIN_EXECUTE = "plugin.execute"

    AI_PROPOSE = "ai.propose"
    AI_APPROVE = "ai.approve"

    AUDIT_READ = "audit.read"
    AUDIT_EXPORT = "audit.export"

    GOVERNANCE_READ = "governance.read"
    GOVERNANCE_WRITE = "governance.write"

    POLICY_READ = "policy.read"
    POLICY_WRITE = "policy.write"

    SECRET_READ = "secret.read"
    SECRET_WRITE = "secret.write"
    KMS_ROTATE = "kms.rotate"

    SERVICE_ACCOUNT_MANAGE = "service_account.manage"

    DASHBOARD_VIEW = "dashboard.view"

    BREAK_GLASS = "break_glass"


ROLES = ("owner", "admin", "developer", "auditor", "viewer")
SERVICE_ROLES = ("admin", "developer", "auditor", "viewer")

_READ_ACTIONS: frozenset[str] = frozenset(
    action.value
    for action in Action
    if action.value.endswith(".read") or action is Action.DASHBOARD_VIEW
)

_DEVELOPER_ACTIONS = frozenset(
    {
        Action.ORGANIZATION_READ.value,
        Action.MEMBER_READ.value,
        Action.PROJECT_READ.value,
        Action.PROJECT_WRITE.value,
        Action.HOST_READ.value,
        Action.HOST_CONNECT.value,
        Action.WORKFLOW_READ.value,
        Action.WORKFLOW_RUN.value,
        Action.JOB_READ.value,
        Action.JOB_ENQUEUE.value,
        Action.PLUGIN_READ.value,
        Action.AI_PROPOSE.value,
        Action.GOVERNANCE_READ.value,
        Action.GOVERNANCE_WRITE.value,
        Action.POLICY_READ.value,
        Action.DASHBOARD_VIEW.value,
    }
)

_AUDITOR_ACTIONS = frozenset(
    {
        Action.ORGANIZATION_READ.value,
        Action.MEMBER_READ.value,
        Action.PROJECT_READ.value,
        Action.HOST_READ.value,
        Action.WORKFLOW_READ.value,
        Action.JOB_READ.value,
        Action.PLUGIN_READ.value,
        Action.AUDIT_READ.value,
        Action.AUDIT_EXPORT.value,
        Action.GOVERNANCE_READ.value,
        Action.POLICY_READ.value,
        Action.DASHBOARD_VIEW.value,
    }
)

_VIEWER_ACTIONS = frozenset(
    {
        Action.ORGANIZATION_READ.value,
        Action.PROJECT_READ.value,
        Action.HOST_READ.value,
        Action.WORKFLOW_READ.value,
        Action.JOB_READ.value,
        Action.PLUGIN_READ.value,
        Action.GOVERNANCE_READ.value,
        Action.DASHBOARD_VIEW.value,
    }
)

_ALL_ACTIONS = frozenset(action.value for action in Action)

#: The authoritative role/permission matrix. ``owner`` is the only role that may
#: delete a tenant; ``admin`` is the delegated tenant administrator.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": _ALL_ACTIONS,
    "admin": _ALL_ACTIONS - {Action.ORGANIZATION_DELETE.value},
    "developer": _DEVELOPER_ACTIONS,
    "auditor": _AUDITOR_ACTIONS,
    "viewer": _VIEWER_ACTIONS,
}

#: Read-only actions derived from the matrix (used by docs, the dashboard and
#: parity tests so the matrix and the derived list cannot drift).
READ_ONLY_ACTIONS: frozenset[str] = _READ_ACTIONS


class AuthorizationError(PermissionError):
    """Raised when a policy decision denies an action."""

    def __init__(self, decision: "Decision") -> None:
        super().__init__(f"{decision.reason} (action={decision.action})")
        self.decision = decision
        self.code = "authorization_denied"


@dataclass(frozen=True, slots=True)
class Resource:
    """The object an action applies to."""

    kind: str
    id: str | None = None
    tenant_id: str | None = None
    owner_subject: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "tenant_id": self.tenant_id,
            "owner_subject": self.owner_subject,
        }


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated actor: a user membership or a scoped service identity."""

    subject: str
    kind: str = "user"
    roles: tuple[str, ...] = ()
    tenant_id: str | None = None
    scopes: tuple[str, ...] | None = None
    denied: frozenset[str] = field(default_factory=frozenset)
    display_name: str = ""

    def __post_init__(self) -> None:
        if not str(self.subject or "").strip():
            raise ValueError("A principal requires a non-empty subject.")
        if self.kind not in {"user", "service"}:
            raise ValueError("Principal kind must be 'user' or 'service'.")
        if self.kind == "service" and self.scopes is None:
            raise ValueError("A service principal requires explicit scopes (they may be empty).")

    @property
    def permissions(self) -> frozenset[str]:
        granted: set[str] = set()
        for role in self.roles:
            granted.update(ROLE_PERMISSIONS.get(role, frozenset()))
        if self.kind == "service":
            granted &= set(self.scopes or ())
        return frozenset(granted - set(self.denied))

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "kind": self.kind,
            "roles": list(self.roles),
            "tenant_id": self.tenant_id,
            "scopes": list(self.scopes) if self.scopes is not None else None,
            "denied": sorted(self.denied),
            "display_name": self.display_name,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    action: str
    reason: str
    rule: str
    subject: str
    tenant_id: str | None = None
    matched_role: str | None = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "reason": self.reason,
            "rule": self.rule,
            "subject": self.subject,
            "tenant_id": self.tenant_id,
            "matched_role": self.matched_role,
        }


AuditSink = Callable[[dict], None]

BREAK_GLASS_MIN_REASON = 16


def principal_from_membership(subject: str, role: str, tenant_id: str | None,
                              *, display_name: str = "") -> Principal:
    """Build a user principal from a stored membership role."""

    if role not in ROLES:
        raise ValueError(f"Unsupported role: {role}")
    return Principal(
        subject=str(subject).strip(),
        kind="user",
        roles=(role,),
        tenant_id=tenant_id,
        display_name=display_name,
    )


class PolicyEngine:
    """Deny-by-default decision point for every interface."""

    def __init__(self, audit_sink: AuditSink | None = None) -> None:
        self._audit_sink = audit_sink

    # ------------------------------------------------------------- inspection
    @staticmethod
    def actions() -> list[str]:
        return sorted(_ALL_ACTIONS)

    @staticmethod
    def matrix() -> dict[str, list[str]]:
        return {role: sorted(permissions) for role, permissions in ROLE_PERMISSIONS.items()}

    @classmethod
    def explain(cls, principal: Principal, action: str | Action,
                resource: Resource | None = None) -> dict:
        """Return a decision plus the supporting permission set (no audit)."""

        decision = cls()._decide(principal, action, resource, context={})
        return {
            "decision": decision.to_dict(),
            "effective_permissions": sorted(principal.permissions),
            "roles": list(principal.roles),
        }

    # ---------------------------------------------------------------- decide
    def decide(self, principal: Principal | None, action: str | Action,
               resource: Resource | None = None, *,
               context: Mapping[str, object] | None = None) -> Decision:
        decision = self._decide(principal, action, resource, context=dict(context or {}))
        self._emit(principal, resource, decision, dict(context or {}))
        return decision

    def require(self, principal: Principal | None, action: str | Action,
                resource: Resource | None = None, *,
                context: Mapping[str, object] | None = None) -> Decision:
        decision = self.decide(principal, action, resource, context=context)
        if not decision.allowed:
            raise AuthorizationError(decision)
        return decision

    def _decide(self, principal: Principal | None, action: str | Action,
                resource: Resource | None,
                *, context: Mapping[str, object]) -> Decision:
        raw_action = action.value if isinstance(action, Action) else str(action)
        subject = principal.subject if principal is not None else "anonymous"

        def deny(reason: str, rule: str, *, role: str | None = None) -> Decision:
            return Decision(False, raw_action, reason, rule, subject,
                            resource.tenant_id if resource else None, role)

        try:
            act = Action(raw_action).value
        except ValueError:
            return deny("unknown_action", "deny.unknown_action")

        if principal is None:
            return deny("no_authenticated_principal", "deny.no_principal")

        if resource is not None and resource.tenant_id is not None:
            if principal.tenant_id is None:
                return deny("principal_has_no_tenant_binding", "deny.tenant_missing")
            if principal.tenant_id != resource.tenant_id:
                return deny("resource_belongs_to_another_tenant", "deny.tenant_mismatch")

        if act in principal.denied:
            return deny("explicitly_denied_for_principal", "deny.explicit")

        known_roles = [role for role in principal.roles if role in ROLE_PERMISSIONS]
        if not known_roles:
            return deny("principal_has_no_known_role", "deny.unknown_role")

        if act == Action.BREAK_GLASS.value:
            if not any(role in {"owner", "admin"} for role in known_roles):
                return deny("break_glass_requires_owner_or_admin", "deny.role")
            reason = str(context.get("reason") or "").strip()
            if len(reason) < BREAK_GLASS_MIN_REASON:
                return deny(
                    f"break_glass_requires_a_reason_of_at_least_{BREAK_GLASS_MIN_REASON}_characters",
                    "deny.break_glass_reason",
                )
            return Decision(True, act, "break_glass_authorized", "allow.break_glass",
                            subject, principal.tenant_id, "owner" if "owner" in known_roles else "admin")

        matched = next((role for role in known_roles if act in ROLE_PERMISSIONS[role]), None)
        if matched is None:
            return deny("role_does_not_grant_this_action", "deny.role")

        if principal.kind == "service":
            if act not in set(principal.scopes or ()):
                return deny("action_outside_token_scope", "deny.scope")
            return Decision(True, act, "granted_by_role_and_scope", "allow.scope", subject,
                            principal.tenant_id, matched)

        return Decision(True, act, "granted_by_role", f"allow.role:{matched}", subject,
                        principal.tenant_id, matched)

    # ----------------------------------------------------------------- audit
    def _emit(self, principal: Principal | None, resource: Resource | None,
              decision: Decision, context: Mapping[str, object]) -> None:
        if self._audit_sink is None:
            return
        event = {
            "event": "authorization.decision",
            "subject": decision.subject,
            "principal_kind": principal.kind if principal is not None else "anonymous",
            "action": decision.action,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "rule": decision.rule,
            "tenant_id": decision.tenant_id,
            "resource": resource.to_dict() if resource is not None else None,
            "request_id": context.get("request_id"),
            "break_glass_reason": (
                str(context.get("reason"))[:300] if decision.rule == "allow.break_glass" else None
            ),
        }
        try:
            self._audit_sink(event)
        except Exception:  # pragma: no cover - an audit sink must never break a decision
            return


def permissions_for_roles(roles: Iterable[str]) -> frozenset[str]:
    """Union of the permissions granted by ``roles`` (unknown roles ignored)."""

    granted: set[str] = set()
    for role in roles:
        granted.update(ROLE_PERMISSIONS.get(role, frozenset()))
    return frozenset(granted)


def validate_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    """Validate a service-token scope list; unknown actions are refused."""

    cleaned: list[str] = []
    for scope in scopes:
        value = str(scope).strip()
        if not value:
            continue
        try:
            cleaned.append(Action(value).value)
        except ValueError as exc:
            raise ValueError(f"Unknown scope: {value}") from exc
    if not cleaned:
        raise ValueError("A service token requires at least one scope.")
    # De-duplicate while preserving a deterministic order.
    return tuple(sorted(set(cleaned)))


__all__ = [
    "Action",
    "AuthorizationError",
    "BREAK_GLASS_MIN_REASON",
    "Decision",
    "PolicyEngine",
    "Principal",
    "READ_ONLY_ACTIONS",
    "ROLE_PERMISSIONS",
    "ROLES",
    "Resource",
    "SERVICE_ROLES",
    "permissions_for_roles",
    "principal_from_membership",
    "validate_scopes",
]
