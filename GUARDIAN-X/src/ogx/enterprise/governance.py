from __future__ import annotations

from typing import Any

PILLARS: list[dict[str, Any]] = [
    {
        "id": "leadership", "title": "Leadership & Accountability",
        "purpose": "Set accountable ownership, ethics, culture, risk appetite and executive outcomes.",
        "capabilities": ["executive ownership", "delegation of authority", "RACI and succession", "ethics and escalation"],
        "decision_boundary": "Sets accountability and risk appetite; does not bypass technical or security controls.",
        "records": ["initiative", "decision", "risk"],
    },
    {
        "id": "direction", "title": "Direction & Strategy",
        "purpose": "Translate mission into measurable strategy, objectives, roadmaps and investment priorities.",
        "capabilities": ["mission and strategy", "OKRs and outcomes", "portfolio prioritization", "roadmap and investment thesis"],
        "decision_boundary": "Defines desired outcomes and priorities; changes to scope or budget require recorded approval.",
        "records": ["objective", "initiative", "decision"],
    },
    {
        "id": "management", "title": "Management & Portfolio Control",
        "purpose": "Plan work, capacity, budget, delivery, dependencies, quality and performance.",
        "capabilities": ["portfolio and project management", "resource and capacity planning", "delivery health", "KPI and benefits tracking"],
        "decision_boundary": "Coordinates approved work; cannot self-approve high-risk production changes.",
        "records": ["initiative", "objective", "risk", "decision"],
    },
    {
        "id": "administration", "title": "Administration & Corporate Control",
        "purpose": "Enforce identities, roles, policies, records, compliance evidence and lifecycle controls.",
        "capabilities": ["RBAC and joiner-mover-leaver", "policy and compliance register", "audit evidence", "license and vendor governance"],
        "decision_boundary": "Enforces least privilege and separation of duties; administrative access is auditable.",
        "records": ["decision", "risk", "change"],
    },
    {
        "id": "steering", "title": "Steering & Assurance",
        "purpose": "Review progress, challenge assumptions, resolve cross-team blockers and steer corrective action.",
        "capabilities": ["steering reviews", "stage gates", "risk acceptance", "independent assurance"],
        "decision_boundary": "Can approve/reject scoped proposals; cannot approve its own request or bypass expiry/evidence.",
        "records": ["decision", "risk", "initiative", "change"],
    },
    {
        "id": "operations", "title": "Operations & Service Reliability",
        "purpose": "Run systems, changes, incidents, service levels, backups, recovery and continual improvement.",
        "capabilities": ["change and release management", "incident/problem management", "SLO/SLA and observability", "backup, restore and continuity"],
        "decision_boundary": "Runs only approved and tested changes; production deployment and destructive actions require separate deployment controls.",
        "records": ["change", "incident", "service_metric", "risk"],
    },
]

RECORD_TYPES = {"objective", "initiative", "risk", "decision", "change", "incident", "service_metric"}
RECORD_STATUSES = {"proposed", "planned", "open", "in_progress", "blocked", "accepted", "mitigated", "resolved", "done", "rejected", "cancelled"}
RECORD_PRIORITIES = {"critical", "high", "medium", "low"}
STATUS_TRANSITIONS = {
    "proposed": {"planned", "accepted", "rejected", "cancelled"},
    "planned": {"in_progress", "blocked", "cancelled"},
    "open": {"in_progress", "blocked", "mitigated", "resolved", "cancelled"},
    "in_progress": {"blocked", "accepted", "mitigated", "resolved", "done", "cancelled"},
    "blocked": {"in_progress", "mitigated", "cancelled"},
    "accepted": {"in_progress", "done", "cancelled"},
    "mitigated": {"resolved", "done"},
    "resolved": {"done"},
    "done": set(), "rejected": set(), "cancelled": set(),
}


def framework_document() -> dict[str, Any]:
    return {
        "name": "OMEGA GUARDIAN X Enterprise Operating Model",
        "version": "1.0",
        "pillars": PILLARS,
        "control_loops": [
            {"name": "strategy-to-execution", "sequence": ["leadership", "direction", "management", "operations", "steering"]},
            {"name": "control-and-assurance", "sequence": ["administration", "steering", "leadership"]},
            {"name": "continuous-improvement", "sequence": ["operations", "management", "steering", "direction"]},
        ],
        "hard_gates": [
            "No mutation before a valid, unexpired, single-use approval when email approval is required.",
            "Requester and approver must be different configured identities.",
            "Approval applies only to the exact allowlisted operation and normalized project path in the request.",
            "Approval never implies merge, deployment, production migration or permission to run arbitrary shell commands.",
            "All governance decisions and execution outcomes are retained in the local audit ledger.",
        ],
        "maturity_note": "Local governance foundation; production enterprise use still requires external IdP/SSO, hardened service deployment, managed database, key management, backups, monitoring and independent security review.",
    }
