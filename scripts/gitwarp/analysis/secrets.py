"""Secret-looking content detection. Findings NEVER include the secret value."""
from __future__ import annotations

import re

from gitwarp.core import redact as _redact

REDACTED = _redact.REDACTED
# strong, specific patterns from core.redact (skip the two generic bearer/authorization ones: prose false positives)
_STRONG = [p for i, p in enumerate(getattr(_redact, "_PATTERNS", [])) if i not in (1, 2)]
_ASSIGN = getattr(_redact, "_ASSIGN", None)

_SKIP_KEY = re.compile(r"(?i)(url|uri|path|file|dir|name|type|header|field|endpoint|expiry|expires|ttl|timeout|length|len|count|label|prompt|regex|pattern|mode|required|policy|class|env|var|key_id)$")
_PLACEHOLDER = re.compile(r"(?i)^(x+|\*+|\.+|changeme|change[-_]?me|example|dummy|placeholder|your[-_ ].*|<.*>|\$\{?.*|%.*|test|testing|secret|password|redacted|\[redacted\]|none|null|true|false|undefined|todo)$")
_REFERENCE = re.compile(r"^(os\.|process\.|env\b|getenv|config|settings|self\.|this\.|request|args|kwargs|input|ctx\.|secrets\.|vault|ENV\[|System\.getenv)", re.I)


def classify_line(text: str):
    """Return a coarse kind string when ``text`` looks like it contains a secret, else None."""
    if not text or len(text) > 4000:
        return None
    if "BEGIN" in text and _STRONG and _STRONG[0].search(text):
        return "private-key"
    for pat in _STRONG[1:]:
        if pat.search(text):
            return "provider-token-or-url-credentials"
    if _ASSIGN is not None:
        for m in _ASSIGN.finditer(text):
            key, value = m.group(1), m.group(3).strip()
            if _SKIP_KEY.search(key):
                continue
            quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
            inner = value[1:-1] if quoted else value
            if len(inner) < 8 or _PLACEHOLDER.match(inner) or _REFERENCE.match(inner):
                continue
            if not quoted and re.search(r"[()\[\]{}]", inner):
                continue
            if " " in inner and not quoted:
                continue
            return "credential-assignment"
    return None


def safe_snippet(text: str, limit: int = 120) -> str:
    """Redacted, truncated line preview (secret values never survive ``redact``)."""
    return _redact.redact(text.strip())[:limit]


def strong_redact(text: str) -> str:
    out = text or ""
    for pat in getattr(_redact, "_PATTERNS", []):
        out = pat.sub(REDACTED, out)
    return out


def scan_records(records: list, skip_path, max_findings: int = 50) -> dict:
    """Scan ``(path, sign, lineno, text)`` diff records (added lines only)."""
    findings, total = [], 0
    for path, sign, lineno, text in records:
        if sign != "+" or skip_path(path):
            continue
        kind = classify_line(text)
        if kind:
            total += 1
            if len(findings) < max_findings:
                findings.append({"path": path, "line": lineno, "kind": kind, "value": REDACTED})
    return {"count": total, "findings": findings, "truncated": total > len(findings)}


def scan_text(path: str, text: str, max_lines: int = 5000) -> list:
    out = []
    for i, line in enumerate(text.splitlines()[:max_lines], 1):
        kind = classify_line(line)
        if kind:
            out.append({"path": path, "line": i, "kind": kind, "value": REDACTED})
    return out
