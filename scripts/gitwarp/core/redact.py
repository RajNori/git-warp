"""Secret redaction for anything Git Warp persists or prints from untrusted input."""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic|token)?\s*[^\s'\"]+(?:\s+[^\s'\"]{8,})?"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|ant|proj)?[-_]?[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWT
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@"),                    # url credentials
]
# KEY=value / key: value where the key name looks secret-ish
_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:secret|token|passw(?:or)?d|passwd|pwd|api[_-]?key|apikey|access[_-]?key|private[_-]?key|credential|auth)[A-Za-z0-9_.-]*)"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&|]+)"
)
_FLAG = re.compile(r"(?i)(--?(?:password|passwd|token|secret|api-?key|auth)[= ])(\S+)")


def redact(text: str) -> str:
    """Replace probable secrets in ``text`` with ``[REDACTED]``."""
    if not text:
        return text
    out = _PATTERNS[0].sub(REDACTED, text)
    out = _PATTERNS[1].sub(lambda m: m.group(1) + ": " + REDACTED, out)
    for pat in _PATTERNS[2:11]:
        out = pat.sub(REDACTED, out)
    out = _PATTERNS[11].sub(lambda m: m.group(1) + REDACTED + "@", out)
    out = _ASSIGN.sub(lambda m: m.group(1) + m.group(2) + REDACTED, out)
    out = _FLAG.sub(lambda m: m.group(1) + REDACTED, out)
    return out


def redact_obj(obj, max_str: int = 500):
    """Recursively redact strings (and truncate long ones) in JSON-like data."""
    if isinstance(obj, str):
        s = redact(obj)
        return s if len(s) <= max_str else s[:max_str] + f"…[+{len(s) - max_str} chars]"
    if isinstance(obj, dict):
        return {str(k): (REDACTED if _secret_key(str(k)) and isinstance(v, str) else redact_obj(v, max_str)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, max_str) for v in obj]
    return obj


def _secret_key(k: str) -> bool:
    return bool(re.search(r"(?i)secret|token|passw|api[_-]?key|credential|authorization|private[_-]?key", k))
