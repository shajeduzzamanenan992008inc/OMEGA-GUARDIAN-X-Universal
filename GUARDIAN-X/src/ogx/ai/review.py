"""Structured review artifacts and approval binding (AI-07).

A review artifact is complete enough for a human to decide without reading the
whole plan file: unified diff, changed files with hashes, rationale, risk and
severity, security and compatibility impact, required tests and rollback.

Approvals are bound to:

* the immutable patch-plan digest (hunks, before/after hashes, project id), and
* the exact pre-image hashes of every touched file (the "base revision").

Applying through :func:`apply_reviewed` re-checks both, so an approval becomes
stale the moment the plan or the working tree changes.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from ..patching import PatchError, apply_patch, load_plan

_REVIEW_DIR = "reviews"
_APPROVAL_DIR = "approvals"

_SENSITIVE_PARTS = ("auth", "crypto", "secret", "policy", "guard/", ".github/")
_COMPAT_SUFFIXES = (
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "Dockerfile",
)


class ReviewError(RuntimeError):
    """Raised when a review artifact cannot be produced."""


class ApprovalMissing(ReviewError):
    """Raised when no valid approval exists for a plan."""


class ApprovalStale(ReviewError):
    """Raised when the plan or working tree changed after approval."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _review_root(state_home: Path) -> Path:
    return state_home / "ai-review"


def _read_bytes(path: Path) -> bytes | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def _git_revision(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    try:
        if head.is_file():
            content = head.read_text(encoding="utf-8").strip()
            if content.startswith("ref: "):
                ref = root / ".git" / content[5:]
                if ref.is_file():
                    return ref.read_text(encoding="utf-8").strip()[:40]
                return content
            return content[:40]
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _unified_diff(path: str, before: bytes, after: bytes) -> str:
    try:
        before_text = before.decode("utf-8")
        after_text = after.decode("utf-8")
    except UnicodeDecodeError:
        return f"Binary file changed: {path}"
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


def _risk(paths: list[str], diff_bytes: int) -> tuple[int, str]:
    score = 15 + min(35, diff_bytes // 200)
    for path in paths:
        lowered = path.lower()
        if any(part in lowered for part in _SENSITIVE_PARTS):
            score += 20
        if lowered.endswith(_COMPAT_SUFFIXES):
            score += 10
        if lowered.startswith(".env") or "credential" in lowered:
            score += 25
    score = min(100, score)
    band = "critical" if score >= 80 else "high" if score >= 60 else "medium" if score >= 35 else "low"
    return score, band


def plan_digest(plan: Mapping[str, Any]) -> str:
    body = {
        "schema": plan.get("schema"),
        "project_id": plan.get("project_id"),
        "changes": [
            {
                "path": change.get("path"),
                "before_sha256": change.get("before_sha256"),
                "after_sha256": change.get("after_sha256"),
                "old": change.get("old"),
                "new": change.get("new"),
            }
            for change in plan.get("changes", [])
        ],
    }
    return _sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def build_review(plan_id: str, *, state_home: Path | str) -> dict[str, Any]:
    """Produce a review artifact from a stored patch plan (read-only)."""

    home = Path(state_home)
    plan = load_plan(plan_id, state_home=home)
    root = Path(str(plan.get("project_root", "")))
    if not root.is_dir():
        raise ReviewError("The plan's project root is no longer available.")

    files: list[dict[str, Any]] = []
    diffs: list[str] = []
    stale: list[str] = []
    total_diff = 0
    for change in plan.get("changes", []):
        relative = str(change.get("path", ""))
        target = root / relative
        current = _read_bytes(target)
        recorded_before = str(change.get("before_sha256", ""))
        current_hash = _sha256(current) if current is not None else None
        is_stale = current_hash != recorded_before
        if is_stale:
            stale.append(relative)
        old_text = str(change.get("old", ""))
        new_text = str(change.get("new", ""))
        before = current if current is not None else old_text.encode("utf-8")
        try:
            index = before.index(old_text.encode("utf-8"))
            after = before[:index] + new_text.encode("utf-8") + before[index + len(old_text.encode("utf-8")) :]
        except ValueError:
            after = new_text.encode("utf-8")
        diff = _unified_diff(relative, before, after)
        total_diff += len(diff)
        diffs.append(diff)
        files.append(
            {
                "path": relative,
                "before_sha256": recorded_before,
                "after_sha256": str(change.get("after_sha256", "")),
                "current_sha256": current_hash,
                "size_before": int(change.get("size_before", 0)),
                "size_after": int(change.get("size_after", 0)),
                "stale": is_stale,
            }
        )

    paths = [str(change.get("path", "")) for change in plan.get("changes", [])]
    risk_score, risk_band = _risk(paths, total_diff)
    review_id = uuid.uuid4().hex
    review = {
        "schema": 1,
        "review_id": review_id,
        "plan_id": plan_id,
        "plan_digest": plan_digest(plan),
        "project_id": plan.get("project_id"),
        "project_root": str(root),
        "base_revision": {
            "git_head": _git_revision(root),
            "files": {item["path"]: item["before_sha256"] for item in files},
        },
        "state": plan.get("state"),
        "stale": bool(stale),
        "stale_files": stale,
        "changed_files": paths,
        "files": files,
        "diff": "\n".join(diffs)[:200_000],
        "risk_score": risk_score,
        "risk_band": risk_band,
        "security_impact": [
            f"touches security-sensitive path: {path}"
            for path in paths
            if any(part in path.lower() for part in _SENSITIVE_PARTS)
        ],
        "compatibility_impact": [
            f"changes build/dependency metadata: {path}"
            for path in paths
            if path.lower().endswith(_COMPAT_SUFFIXES)
        ],
        "required_tests": sorted(
            {
                f"pytest tests/test_{Path(path).stem}.py"
                for path in paths
                if not path.startswith("tests/")
            }
        )
        + ["pytest"],
        "rollback": (
            f"ogx patch rollback --plan {plan_id} --confirm"
            " (restores recorded pre-image bytes and modes)"
        ),
        "approval_required": True,
        "generated_at": _iso(_now()),
    }
    directory = _review_root(home)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{review_id}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(review, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return review


@dataclass(frozen=True, slots=True)
class Approval:
    plan_id: str
    plan_digest: str
    base_revision: Mapping[str, Any]
    approver: str
    approved_at: str
    expires_at: str
    scope: str = "apply"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "base_revision": dict(self.base_revision),
            "approver": self.approver,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }


def _approval_path(plan_id: str, home: Path) -> Path:
    if not plan_id or "/" in plan_id or "\\" in plan_id or ".." in plan_id:
        raise ApprovalMissing("Invalid plan id for an approval record.")
    return _review_root(home) / _APPROVAL_DIR / f"{plan_id}.json"


def approve_plan(
    plan_id: str,
    *,
    approver: str,
    state_home: Path | str,
    ttl_minutes: int = 60,
) -> dict[str, Any]:
    if not approver.strip():
        raise ReviewError("An approval requires an explicit approver identity.")
    if not 1 <= ttl_minutes <= 24 * 60:
        raise ReviewError("Approval TTL must be between 1 minute and 24 hours.")
    home = Path(state_home)
    plan = load_plan(plan_id, state_home=home)
    if plan.get("state") != "planned":
        raise ReviewError("Only a planned patch can be approved.")
    review = build_review(plan_id, state_home=home)
    if review["stale"]:
        raise ApprovalStale(
            "The working tree changed after the plan was created; re-plan before approving."
        )
    moment = _now()
    approval = Approval(
        plan_id=plan_id,
        plan_digest=review["plan_digest"],
        base_revision=review["base_revision"],
        approver=approver.strip(),
        approved_at=_iso(moment),
        expires_at=_iso(moment + timedelta(minutes=ttl_minutes)),
    )
    path = _approval_path(plan_id, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(approval.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return approval.to_dict()


def verify_approval(
    plan_id: str,
    *,
    state_home: Path | str,
    approver: str | None = None,
    now: datetime | None = None,
) -> Approval:
    home = Path(state_home)
    path = _approval_path(plan_id, home)
    if path.is_symlink() or not path.is_file():
        raise ApprovalMissing("No approval record exists for this plan.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApprovalMissing("Approval record is unreadable.") from exc
    approval = Approval(
        plan_id=str(payload.get("plan_id", "")),
        plan_digest=str(payload.get("plan_digest", "")),
        base_revision=dict(payload.get("base_revision", {})),
        approver=str(payload.get("approver", "")),
        approved_at=str(payload.get("approved_at", "")),
        expires_at=str(payload.get("expires_at", "")),
        scope=str(payload.get("scope", "apply")),
    )
    if approval.plan_id != plan_id:
        raise ApprovalStale("Approval record belongs to a different plan.")
    if approver is not None and approval.approver != approver:
        raise ApprovalMissing("Approval was granted by a different identity.")
    moment = now or _now()
    try:
        expiry = datetime.fromisoformat(approval.expires_at)
    except ValueError as exc:
        raise ApprovalStale("Approval expiry is malformed.") from exc
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if moment >= expiry:
        raise ApprovalStale("Approval has expired; request a new review.")

    plan = load_plan(plan_id, state_home=home)
    if plan_digest(plan) != approval.plan_digest:
        raise ApprovalStale("The patch plan changed after approval.")
    root = Path(str(plan.get("project_root", "")))
    for relative, expected in (approval.base_revision.get("files") or {}).items():
        target = root / str(relative)
        current = _read_bytes(target)
        if current is None or _sha256(current) != expected:
            raise ApprovalStale(
                f"File {relative!r} changed after approval; re-plan before applying."
            )
    return approval


def apply_reviewed(
    plan_id: str,
    *,
    state_home: Path | str,
    actor_subject: str | None = None,
) -> dict[str, Any]:
    """Apply a plan only with a valid, unexpired, non-stale approval."""

    home = Path(state_home)
    approval = verify_approval(plan_id, state_home=home)
    plan = load_plan(plan_id, state_home=home)
    try:
        result = apply_patch(
            plan_id,
            approved=True,
            actor_subject=actor_subject or approval.approver,
            state_home=home,
        )
    except PatchError as exc:
        raise ReviewError(f"Reviewed patch could not be applied: {exc}") from exc
    # An approval is single-use: it must not authorize a later replay.
    try:
        _approval_path(plan_id, home).unlink(missing_ok=True)
    except OSError:
        pass
    return {
        **result,
        "review_plan_digest": plan_digest(plan),
        "approved_by": approval.approver,
    }
