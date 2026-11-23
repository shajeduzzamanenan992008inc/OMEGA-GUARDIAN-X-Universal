"""Attack-signature catalogue (heuristic, deliberately conservative) and the normalised views it runs on.

This is defence in depth for small and medium apps, not a replacement for a managed WAF.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterator

from ogx.runtime.request import Match, Request, decode_repeated

SCANNED_HEADERS = {"user-agent", "referer", "x-forwarded-for", "x-forwarded-host", "x-original-url", "x-rewrite-url"}
TEXT_TYPES = {"application/xml", "application/graphql", "application/javascript", "application/x-www-form-urlencoded"}


@dataclass(frozen=True, slots=True)
class Signature:
    rule_id: str
    name: str
    pattern: re.Pattern[str]
    targets: tuple[str, ...]                 # path | query | headers | allheaders | body
    severity: str
    strict_targets: tuple[str, ...] = ()     # extra targets under signature_profile=strict
    action: str = "block"                    # action under the balanced profile
    strict_action: str = "block"


def _sig(rule_id: str, name: str, pattern: str, targets: tuple[str, ...], severity: str, **kw) -> Signature:
    return Signature(rule_id, name, re.compile(pattern), targets, severity, **kw)


SIGNATURES: tuple[Signature, ...] = (
    _sig("OGX-RT-1001", "path-traversal",
         r"(?:(?:^|[^.\w])\.\.(?:;[^/\\]*)?[\\/])|(?:[\\/]\.\.$)", ("path", "query"), "high"),
    _sig("OGX-RT-1002", "sensitive-file-probe",
         r"(?i)(?:/etc/(?:passwd|shadow|group|hosts)\b|/proc/self/|(?<![\w.])(?:boot|win)\.ini\b|(?:^|/)\.git(?:/|$)"
         r"|(?:^|/)\.env(?:\.[\w-]+)?(?:$|[/?#])|wp-config\.php|\.aws/credentials|(?:^|/)id_(?:rsa|dsa|ed25519)\b"
         r"|\.htpasswd\b|(?:^|/)\.ssh(?:/|$)|/web\.config\b)", ("path", "query"), "high"),
    _sig("OGX-RT-1010", "sql-injection",
         r"""(?i)(?:\bunion\b[\s/*()+]{1,20}(?:all\b[\s/*()+]{1,20})?select\b[\s/*()+]{0,20}(?:[\w@.'"`*]+\s*,|null\b|\d|\*|@@|from\b|--)"""
         r"""|['"]\s*(?:or|and)\s+['"]?\w+['"]?\s*[=<>]"""
         r"""|['"]\s*--(?:\s|$)|\b(?:sleep|benchmark|pg_sleep)\s*\(\s*\d|\bwaitfor\s+delay\s+['"]"""
         r"""|;\s*(?:drop|truncate|alter)\s+(?:table|database)\b|\binformation_schema\b|\bxp_cmdshell\b|\bload_file\s*\("""
         r"""|\binto\s+(?:out|dump)file\b)""", ("path", "query", "headers", "body"), "high"),
    _sig("OGX-RT-1020", "cross-site-scripting",
         r"(?i)(?:<\s*script\b|<[^>\n]{0,200}\bon[a-z]{3,20}\s*=|\b(?:javascript|vbscript):[^\s]|\bdata\s*:\s*text/html"
         r"|<\s*(?:iframe|object|embed|svg)\b|\bexpression\s*\()", ("path", "query", "headers"), "high",
         strict_targets=("body",)),
    _sig("OGX-RT-1030", "command-injection",
         r"(?i)(?:(?:;|\|\||&&|\|)\s*(?:cat|whoami|uname|curl|wget|bash|powershell|pwsh|cmd|ncat|python[23]?|perl)\b"
         r"|\$\([^)\n]{1,120}\)|`[^`\n]{1,120}`|/bin/(?:ba|z|da)?sh\b|\bcmd(?:\.exe)?\s*/c\b"
         r"|\bpowershell(?:\.exe)?\s+-(?:enc|e|c)\b)", ("path", "query", "headers"), "high"),
    _sig("OGX-RT-1050", "ssrf-probe",
         r"(?i)(?:169\.254\.169\.254|metadata\.google\.internal|\blocalhost\b|\b127\.\d+\.\d+\.\d+\b|\b0\.0\.0\.0\b|\[::1?\]"
         r"|\b(?:10|192\.168)\.\d+\.\d+\b|\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b|\bfile:/{2,3})",
         ("query", "body"), "medium", action="log", strict_action="block"),
    _sig("OGX-RT-1060", "jndi-or-template-injection",
         r"(?i)(?:\$\{\s*jndi\s*:|\$\{\s*\$\{|\{\{\s*\d+\s*[*+\-]\s*\d+\s*\}\}|<%=?\s*\d+\s*[*+\-]\s*\d+\s*%>"
         r"|#\{\s*\d+\s*[*+\-]\s*\d+\s*\})", ("path", "query", "allheaders", "body"), "critical"),
    _sig("OGX-RT-1065", "shellshock",
         r"\(\)\s*\{[^}]*\}\s*;", ("allheaders",), "critical"),
    _sig("OGX-RT-1070", "dangerous-wrapper",
         r"(?i)\b(?:php|phar|expect|zip|glob|ssh2|rar)://", ("path", "query"), "high"),
    _sig("OGX-RT-1080", "xml-external-entity",
         r"(?i)(?:<!DOCTYPE[^>]{0,200}\[[^\]]{0,500}<!ENTITY|<!ENTITY\s+(?:%\s*)?\S+\s+(?:SYSTEM|PUBLIC)\b)",
         ("body",), "high"),
)


def _json_strings(node: object, budget: list[int], depth: int = 0) -> Iterator[str]:
    if budget[0] <= 0 or depth > 12:
        return
    if isinstance(node, str):
        budget[0] -= 1
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            budget[0] -= 1
            yield str(key)
            yield from _json_strings(value, budget, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _json_strings(item, budget, depth + 1)


def _body_texts(request: Request, limit: int) -> list[str]:
    if not request.body or limit <= 0:
        return []
    ctype = (request.header("Content-Type") or "").split(";")[0].strip().lower()
    raw = request.body[:limit].decode("utf-8", errors="replace")
    if ctype in {"", "application/x-www-form-urlencoded"}:
        return [decode_repeated(raw, plus=True)]
    if ctype == "application/json" or ctype.endswith("+json"):
        try:
            return [decode_repeated(s) for s in _json_strings(json.loads(raw), [5000])] or [raw]
        except ValueError:
            return [raw]
    if ctype.startswith("text/") or ctype in TEXT_TYPES:
        return [raw]
    return []  # multipart / binary bodies are not inspected (documented limitation)


def build_views(request: Request, norm_path: str, inspect_limit: int) -> dict[str, list[str]]:
    raw_path = request.path.split("?", 1)[0]
    return {
        "path": [decode_repeated(raw_path), norm_path],
        "query": [decode_repeated(request.query, plus=True)] if request.query else [],
        "headers": [v for k, v in request.headers if k.lower() in SCANNED_HEADERS],
        "allheaders": [v for _k, v in request.headers],
        "body": _body_texts(request, inspect_limit),
    }


def run_signatures(views: dict[str, list[str]], profile: str, disabled: set[str]) -> list[Match]:
    strict = profile == "strict"
    matches: list[Match] = []
    for sig in SIGNATURES:
        if sig.rule_id in disabled:
            continue
        targets = sig.targets + (sig.strict_targets if strict else ())
        action = sig.strict_action if strict else sig.action
        hit = next((t for t in targets for text in views.get(t, ()) if sig.pattern.search(text)), None)
        if hit:
            matches.append(Match(sig.rule_id, sig.name, sig.severity, action, 403, f"{sig.name} signature in {hit}", hit))
    return matches
