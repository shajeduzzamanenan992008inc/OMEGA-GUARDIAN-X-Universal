"""Framework-neutral request/decision model and path normalisation for the runtime guard."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, unquote_plus

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@dataclass(slots=True)
class Request:
    method: str
    path: str                                   # raw path as received (still percent-encoded), no query
    query: str = ""                             # raw query string, no leading '?'
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    client_ip: str = ""                         # direct peer address
    scheme: str = "http"
    declared_length: int | None = None          # Content-Length declared by the client (body may be unread)

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return None

    def headers_named(self, name: str) -> list[str]:
        lowered = name.lower()
        return [value for key, value in self.headers if key.lower() == lowered]


@dataclass(slots=True)
class Match:
    rule_id: str
    name: str
    severity: str
    action: str                                 # "block" or "log"
    status: int
    reason: str
    target: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class Decision:
    request_id: str
    client_ip: str = ""
    action: str = "allow"                       # allow | block
    status: int = 200
    rule_id: str = ""
    reason: str = ""
    severity: str = "info"
    headers: list[tuple[str, str]] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)   # every rule that fired, incl. monitor-only ones

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    def as_dict(self) -> dict:
        return {"request_id": self.request_id, "client_ip": self.client_ip, "action": self.action, "status": self.status,
                "rule_id": self.rule_id, "reason": self.reason, "severity": self.severity,
                "matches": [{"rule_id": m.rule_id, "name": m.name, "severity": m.severity, "action": m.action,
                             "target": m.target, "reason": m.reason} for m in self.matches]}


def decode_repeated(text: str, rounds: int = 3, plus: bool = False) -> str:
    """Percent-decode until stable (defeats double/triple encoding), bounded."""
    fn = unquote_plus if plus else unquote
    for _ in range(rounds):
        decoded = fn(text, errors="replace")
        if decoded == text:
            break
        text = decoded
    return text


def normalize_path(raw: str) -> str:
    """Decode, unify separators, drop NULs and resolve dot-segments, so access rules cannot be bypassed."""
    path = decode_repeated(raw.split("?", 1)[0].split("#", 1)[0]).replace("\\", "/").replace("\x00", "")
    out: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if out:
                out.pop()
            continue
        out.append(segment)
    return "/" + "/".join(out) + ("/" if path.endswith("/") and out else "")
