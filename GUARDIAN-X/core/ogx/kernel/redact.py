"""Credential redaction shared by log diagnostics and the runtime observability layer.

Layer 0: stdlib only.
"""
from __future__ import annotations

import re
from typing import Any

_ASSIGNMENT = re.compile(r"(?i)(token|password|secret|api[_-]?key)(\s*[=:]\s*)([^\s,;]+)")
_BEARER = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*")
_AUTH_HEADER = re.compile(r"""(?i)(Authorization["']?\s*[:=]\s*["']?)([^\s,;"']+)""")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
_URL_CRED = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@")
SENSITIVE_KEY_RE = re.compile(r"(?i)(pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|private[_-]?key|credential|session)")


def redact_credentials(text: str) -> str:
    """The three legacy log rules: key=value secrets, Bearer tokens, Authorization headers."""
    text = _ASSIGNMENT.sub(r"\1\2<redacted>", text)
    text = _BEARER.sub(r"\1<redacted>", text)
    return _AUTH_HEADER.sub(r"\1<redacted>", text)


def redact_extended(text: str) -> str:
    """Legacy rules plus private-key blocks, JWTs, AWS key ids and credentials embedded in URLs."""
    text = redact_credentials(text)
    text = _PRIVATE_KEY.sub("<redacted-private-key>", text)
    text = _JWT.sub("<redacted-jwt>", text)
    text = _AWS_KEY.sub("<redacted-aws-key>", text)
    return _URL_CRED.sub(r"\1\2:<redacted>@", text)


def redact_mapping(data: Any, _depth: int = 0) -> Any:
    """Recursively redact structured data: sensitive keys lose their value, strings are scrubbed."""
    if _depth > 6:
        return "<truncated>"
    if isinstance(data, dict):
        return {str(k): ("<redacted>" if SENSITIVE_KEY_RE.search(str(k)) else redact_mapping(v, _depth + 1))
                for k, v in data.items()}
    if isinstance(data, (list, tuple, set)):
        return [redact_mapping(v, _depth + 1) for v in list(data)[:100]]
    if isinstance(data, str):
        return redact_extended(data)
    return data
