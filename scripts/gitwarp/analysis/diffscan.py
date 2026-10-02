"""Unified-diff parsing and added-line heuristics (debug leftovers, exported-API changes)."""
from __future__ import annotations

import posixpath
import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_ESC = {"n": 10, "t": 9, "\\": 92, '"': 34, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11}


def _unquote(s: str) -> str:
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    body, out, i = s[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in _ESC:
                out.append(_ESC[nxt])
                i += 2
                continue
            if len(body[i + 1:i + 4]) == 3 and body[i + 1:i + 4].isdigit():
                out.append(int(body[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="replace")


def _clean(p: str):
    p = p.rstrip("\t")
    if p == "/dev/null":
        return None
    p = _unquote(p)
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_diff(text: str, max_lines_per_file: int = 3000) -> tuple:
    """Return ``(records, truncated_files)``; records are ``(path, '+'|'-', lineno, text)``.

    Combined (merge) diffs are skipped.  Lines beyond ``max_lines_per_file`` per file are dropped.
    """
    records, truncated = [], set()
    counts: dict = {}
    old = new = None
    in_hunk = combined = False
    o = n = 0
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            in_hunk = combined = False
            old = new = None
            continue
        if line.startswith("diff --cc ") or line.startswith("diff --combined "):
            in_hunk, combined = False, True
            continue
        if combined:
            continue
        if not in_hunk:
            if line.startswith("--- "):
                old = _clean(line[4:])
            elif line.startswith("+++ "):
                new = _clean(line[4:])
            elif line.startswith("@@"):
                in_hunk = True
            if not in_hunk:
                continue
        if line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                o, n = int(m.group(1)), int(m.group(2))
            continue
        if not line or line[0] not in "+-":
            continue
        path = new or old
        if path is None:
            continue
        c = counts.get(path, 0)
        if c >= max_lines_per_file:
            truncated.add(path)
            continue
        counts[path] = c + 1
        if line[0] == "+":
            records.append((path, "+", n, line[1:]))
            n += 1
        else:
            records.append((path, "-", o, line[1:]))
            o += 1
    return records, truncated


# ---------------------------------------------------------------- debug leftovers
_JS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
_PY = {".py"}
_RULES = [
    # (kind, regex, extensions or None, skip_test_files)
    ("console-log", re.compile(r"\bconsole\.(?:log|debug|trace)\s*\("), _JS, False),
    ("debugger-statement", re.compile(r"^\s*debugger\s*;?\s*$"), _JS, False),
    ("python-print", re.compile(r"^\s*print\s*\("), _PY, True),
    ("pdb-breakpoint", re.compile(r"\bi?pdb\.set_trace\s*\(|^\s*import\s+i?pdb\b|^\s*breakpoint\s*\(\s*\)"), _PY, False),
    ("todo-marker", re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b"), None, False),
    ("test-only", re.compile(r"\b(?:it|test|describe|context)\.only\s*\(|\b(?:fit|fdescribe)\s*\("), _JS, False),
    ("test-skip", re.compile(r"@pytest\.mark\.skip|\bpytest\.skip\s*\(|@unittest\.skip|\b(?:it|test|describe)\.skip\s*\(|\b(?:xit|xdescribe)\s*\("), _JS | _PY, False),
    ("ruby-debugger", re.compile(r"\bbinding\.pry\b|\bbyebug\b"), {".rb"}, False),
    ("rust-dbg", re.compile(r"\bdbg!\s*\("), {".rs"}, False),
    ("php-dump", re.compile(r"\b(?:var_dump|dd)\s*\("), {".php"}, False),
]


def find_debug_leftovers(records: list, ctx, snippet, per_kind_cap: int = 15) -> dict:
    """Added-line debug leftovers. ``snippet`` redacts+truncates a line for display."""
    counts: dict = {}
    items: dict = {}
    for path, sign, lineno, text in records:
        if sign != "+":
            continue
        tags = ctx.tags(path)
        if tags & {"generated", "lockfile"}:
            continue
        ext = posixpath.splitext(path)[1].lower()
        for kind, rx, exts, skip_tests in _RULES:
            if exts is not None and ext not in exts:
                continue
            if skip_tests and "test" in tags:
                continue
            if kind == "todo-marker" and "docs" in tags:
                continue
            if rx.search(text):
                counts[kind] = counts.get(kind, 0) + 1
                lst = items.setdefault(kind, [])
                if len(lst) < per_kind_cap:
                    lst.append({"path": path, "line": lineno, "snippet": snippet(text)})
    return {
        "heuristic": True,
        "added_lines_only": True,
        "counts": counts,
        "total": sum(counts.values()),
        "items": items,
        "truncated": any(counts[k] > len(items[k]) for k in counts),
    }


# ---------------------------------------------------------------- exported API heuristics
_JS_EXPORT = re.compile(r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum|abstract\s+class)\s+([A-Za-z_$][\w$]*)")
_PY_DEF = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z][\w]*)")


def api_surface_changes(records: list, ctx, cap: int = 30) -> dict:
    """Heuristic public-symbol changes: JS/TS ``export ...`` and top-level non-underscore Python def/class."""
    added: dict = {}
    removed: dict = {}
    for path, sign, lineno, text in records:
        tags = ctx.tags(path)
        if tags & {"test", "generated"}:
            continue
        ext = posixpath.splitext(path)[1].lower()
        if ext in _JS:
            m = _JS_EXPORT.match(text)
        elif ext in _PY:
            m = _PY_DEF.match(text)
        else:
            continue
        if not m:
            continue
        key = (path, m.group(1))
        (added if sign == "+" else removed).setdefault(key, (lineno, " ".join(text.split())))
    changed, only_added, only_removed = [], [], []
    for key, (ln, txt) in added.items():
        if key in removed:
            if removed[key][1] != txt:
                changed.append({"path": key[0], "symbol": key[1], "line": ln})
        else:
            only_added.append({"path": key[0], "symbol": key[1], "line": ln})
    for key, (ln, _t) in removed.items():
        if key not in added:
            only_removed.append({"path": key[0], "symbol": key[1], "line": ln})
    return {
        "heuristic": True,
        "note": "Regex heuristic on diff lines (JS/TS export, top-level Python def/class); moved symbols appear as removed+added. Verify by reading the diff.",
        "possibly_breaking_removed": only_removed[:cap],
        "signature_changed": changed[:cap],
        "added": only_added[:cap],
        "counts": {"removed": len(only_removed), "signature_changed": len(changed), "added": len(only_added)},
    }
