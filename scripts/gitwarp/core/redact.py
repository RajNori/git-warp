"""Secret redaction for anything Git Warp persists or prints from untrusted input."""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"
MAX_REDACT_CHARS = 8000   # hooks have hard timeouts; text beyond this is dropped (never passed through unredacted)

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
    r"(?i)\b([A-Za-z0-9_.-]{0,64}(?:secret|token|passw(?:or)?d|passwd|pwd|api[_-]?key|apikey|access[_-]?key|private[_-]?key|credential|auth(?!or))[A-Za-z0-9_.-]{0,64})"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&|]+)"
)
_FLAG = re.compile(r"(?i)(--?(?:password|passwd|token|secret|api-?key|auth)[= ])(\S+)")
# Extra credential spellings (all bounded / linear: no nested unbounded quantifiers around alternation).
_EXTRA = [
    # curl -u user:pass / --user user:pass
    (re.compile(r"(?<![\w-])(-u|--user)(\s+|=)[\"']?[^\s:'\"]+:[^\s'\"]+[\"']?"), lambda m: m.group(1) + m.group(2) + REDACTED),
    # curl --cookie VALUE
    (re.compile(r"(?<![\w-])(--cookie)(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)"), lambda m: m.group(1) + m.group(2) + REDACTED),
    # mysql -phunter2 / sshpass -p x / docker login -p x   (--password-stdin is not matched: needs whitespace before -p)
    (re.compile(r"(?i)\b(mysql|mysqldump|mysqladmin|mariadb|sshpass|(?:docker|podman)\s+login)\b([^\n|;&]{0,200}?\s)(-p)\s*(\"[^\"]*\"|'[^']*'|[^\s-]\S*)"),
     lambda m: m.group(1) + m.group(2) + m.group(3) + " " + REDACTED),
    # redis-cli -a x / openssl enc -k x / -pass x
    (re.compile(r"(?i)\b(redis-cli|openssl)\b([^\n|;&]{0,200}?\s)(-a|-k|-pass|-passin|-passout)(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)"),
     lambda m: m.group(1) + m.group(2) + m.group(3) + m.group(4) + REDACTED),
    # ssh/scp/sftp/rsync identity file: the key path can itself name a secret (-i PATH, -oIdentityFile=PATH)
    (re.compile(r"(?i)\b(ssh|scp|sftp|rsync|ssh-add)\b([^\n|;&]{0,200}?\s)(-i|--identity-file)(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)"),
     lambda m: m.group(1) + m.group(2) + m.group(3) + m.group(4) + REDACTED),
    (re.compile(r"(?i)\b(IdentityFile)(\s*=\s*|\s+)(\"[^\"]*\"|'[^']*'|[^\s'\"]+)"), lambda m: m.group(1) + m.group(2) + REDACTED),
    # Cookie: / Set-Cookie: header values (to end of the quoted string / line)
    (re.compile(r"(?i)\b(set-cookie|cookie)(\s*[:=]\s*)[^\r\n'\"]+"), lambda m: m.group(1) + m.group(2) + REDACTED),
    # "aws_secret_access_key VALUE", "password VALUE"
    (re.compile(r"(?i)\b(aws_secret_access_key|aws_session_token|passw(?:or)?d|passwd)(\s+)(\"[^\"]*\"|'[^']*'|[^\s,;&|=:'\"]+)"),
     lambda m: m.group(1) + m.group(2) + REDACTED),
    # provider token prefixes: HuggingFace, SendGrid, Google OAuth, npm, DigitalOcean
    (re.compile(r"\b(?:hf_[A-Za-z0-9]{16,}|SG\.[A-Za-z0-9_-]{16,}(?:\.[A-Za-z0-9_-]{8,})?|ya29\.[A-Za-z0-9_-]{16,}|npm_[A-Za-z0-9]{16,}|dop_v1_[A-Za-z0-9]{16,})"),
     lambda m: REDACTED),
]


def redact(text: str) -> str:
    """Replace probable secrets in ``text`` with ``[REDACTED]``."""
    if not text:
        return text
    if len(text) > MAX_REDACT_CHARS:
        text = text[:MAX_REDACT_CHARS] + f"…[+{len(text) - MAX_REDACT_CHARS} chars truncated]"
    out = _PATTERNS[0].sub(REDACTED, text)
    out = _PATTERNS[1].sub(lambda m: m.group(1) + ": " + REDACTED, out)
    for pat in _PATTERNS[2:11]:
        out = pat.sub(REDACTED, out)
    out = _PATTERNS[11].sub(lambda m: m.group(1) + REDACTED + "@", out)
    for pat, repl in _EXTRA:
        out = pat.sub(repl, out)
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


def redact_long(text: str) -> str:
    """Redact text of any length without dropping content (unlike :func:`redact`): split at line breaks into bounded chunks."""
    if len(text) <= MAX_REDACT_CHARS:
        return redact(text)
    out, buf, size = [], [], 0
    for line in text.splitlines(keepends=True):
        while len(line) > MAX_REDACT_CHARS:           # a single enormous line: cut at the bound (secrets are far shorter)
            if buf:
                out.append(redact("".join(buf)))
                buf, size = [], 0
            out.append(redact(line[:MAX_REDACT_CHARS]))
            line = line[MAX_REDACT_CHARS:]
        if size + len(line) > MAX_REDACT_CHARS and buf:
            out.append(redact("".join(buf)))
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        out.append(redact("".join(buf)))
    return "".join(out)


def scrub(obj):
    """Deep-copy JSON-like data redacting every string value (keys, numbers and structure untouched, nothing truncated)."""
    if isinstance(obj, str):
        return redact_long(obj)
    if isinstance(obj, dict):
        return {(k if isinstance(k, str) else str(k)): scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return redact_long(str(obj))                      # what json.dumps(default=str) would have printed
