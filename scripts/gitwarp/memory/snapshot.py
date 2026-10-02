"""Cheap repository snapshots + explainable risk assessment shared by the session-start and stop hooks."""
from __future__ import annotations

import re
from typing import Optional

from ..core import git
from ..core.config import Config
from ..core.paths import classify_path
from ..core.redact import redact

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean(text, limit: int = 100) -> str:
    """Untrusted text (branch names, commit subjects, paths) → single-line, redacted, bounded."""
    s = redact(_CTRL.sub(" ", str(text or ""))).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def status(cwd, timeout: float = 5.0, untracked: str = "normal") -> Optional[dict]:
    """Parsed ``git status`` with counts, or None if it timed out / failed."""
    try:
        r = git.run(["status", "--porcelain=v1", "-z", f"--untracked-files={untracked}"], cwd=cwd, timeout=timeout)
    except git.GitError:
        return None
    if not r.ok:
        return None
    parts, entries, i = r.stdout.split("\x00"), [], 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        orig = None
        if xy[0] in "RC" or xy[1] in "RC":
            orig = parts[i] if i < len(parts) else None
            i += 1
        entries.append(git.StatusEntry(xy, path, orig))
    return {
        "entries": entries,
        "staged": sum(1 for e in entries if e.staged and not e.conflicted),
        "unstaged": sum(1 for e in entries if e.unstaged and not e.conflicted),
        "untracked": sum(1 for e in entries if e.untracked),
        "conflicted": sum(1 for e in entries if e.conflicted),
        "total": len(entries),
    }


def numstat(cwd, timeout: float = 6.0) -> list:
    """``[{path, added, deleted, binary}]`` of tracked changes vs HEAD (index+worktree). Empty on failure."""
    out: list = []
    seen = {}
    runs = [["diff", "--numstat", "-z", "HEAD"]] if git.head_sha(cwd) else [["diff", "--numstat", "-z", "--cached"], ["diff", "--numstat", "-z"]]
    for args in runs:
        try:
            r = git.run(args, cwd=cwd, timeout=timeout)
        except git.GitError:
            continue
        if not r.ok:
            continue
        parts, i = r.stdout.split("\x00"), 0
        while i < len(parts):
            item = parts[i]
            i += 1
            if not item:
                continue
            a, _, rest = item.partition("\t")
            d, _, path = rest.partition("\t")
            if path == "":
                i += 1
                path = parts[i] if i < len(parts) else ""
                i += 1
            binary = a == "-" or d == "-"
            row = {"path": path, "added": 0 if binary else int(a or 0), "deleted": 0 if binary else int(d or 0), "binary": binary}
            if path in seen:
                seen[path]["added"] += row["added"]
                seen[path]["deleted"] += row["deleted"]
            else:
                seen[path] = row
                out.append(row)
    return out


def assess_risk(entries: list, stats: list, cfg: Config, operation: Optional[str] = None) -> dict:
    """LOW/MEDIUM/HIGH with the concrete drivers.  Rules are listed, not scored; the highest driver wins."""
    drivers: list = []  # (rank, text)
    tag_paths: dict = {}
    for e in entries:
        for t in classify_path(e.path, cfg):
            tag_paths.setdefault(t, []).append(e.path)

    def show(paths, n=3):
        paths = list(dict.fromkeys(paths))
        return ", ".join(clean(p, 60) for p in paths[:n]) + (f" (+{len(paths) - n} more)" if len(paths) > n else "")

    conflicted = [e.path for e in entries if e.conflicted]
    if conflicted:
        drivers.append((2, f"{len(conflicted)} unresolved conflict file(s): {show(conflicted)}"))
    if "secret-file" in tag_paths:
        drivers.append((2, f"secret-like file changed: {show(tag_paths['secret-file'])} (do not commit credentials)"))
    deleted = [e.path for e in entries if "D" in e.xy]
    lines = sum(s["added"] + s["deleted"] for s in stats)
    nfiles = len({e.path for e in entries})
    if len(deleted) >= 10:
        drivers.append((2, f"{len(deleted)} files deleted"))
    elif len(deleted) >= 5:
        drivers.append((1, f"{len(deleted)} files deleted"))
    if lines >= 1000 or nfiles >= 50:
        drivers.append((2, f"very large change ({nfiles} paths, {lines} lines)"))
    elif lines >= 300 or nfiles >= 15:
        drivers.append((1, f"large change ({nfiles} paths, {lines} lines)"))
    sens = [p for p in tag_paths.get("sensitive", []) if p not in tag_paths.get("secret-file", [])]
    if sens:
        drivers.append((1, f"sensitive path(s): {show(sens)}"))
    if "migration" in tag_paths or "schema" in tag_paths:
        drivers.append((1, f"database migration/schema change: {show(tag_paths.get('migration', []) + tag_paths.get('schema', []))}"))
    if "infra" in tag_paths or "ci" in tag_paths:
        drivers.append((1, f"infrastructure/CI change: {show(tag_paths.get('infra', []) + tag_paths.get('ci', []))}"))
    if "lockfile" in tag_paths:
        manifests = [p for p in tag_paths.get("dependency", []) if p not in tag_paths["lockfile"]]
        drivers.append((1, f"lockfile changed ({show(tag_paths['lockfile'])})" + ("" if manifests else " without a manifest change")))
    elif "dependency" in tag_paths:
        drivers.append((1, f"dependency manifest changed: {show(tag_paths['dependency'])}"))
    src = tag_paths.get("source", [])
    if len(src) >= 3 and "test" not in tag_paths:
        drivers.append((1, f"{len(src)} source files changed, no test files touched"))
    if operation:
        drivers.append((1, f"a {operation} is in progress"))
    rank = max((r for r, _ in drivers), default=0)
    return {"level": ("LOW", "MEDIUM", "HIGH")[rank], "drivers": [t for _, t in sorted(drivers, key=lambda d: -d[0])],
            "lines_changed": lines, "paths": nfiles}


def next_step(level: str, st: dict) -> str:
    if st["conflicted"]:
        return "Next: resolve the conflicted files, then `git add` them and continue the operation (or abort it) before anything else."
    if level == "HIGH":
        return "Next: review `git diff` carefully (consider /git-xray) and split risky changes into separate commits before committing."
    if level == "MEDIUM":
        return "Next: review the flagged areas with `git diff`, run the relevant tests, then commit in focused groups."
    return "Next: review `git diff`, then commit when satisfied."


def stat_lines(stats: list, untracked: list, limit: int = 8) -> list:
    rows = sorted(stats, key=lambda s: -(s["added"] + s["deleted"]))
    out = []
    for s in rows[:limit]:
        out.append(f"  {clean(s['path'], 70)} | " + ("binary" if s["binary"] else f"+{s['added']} -{s['deleted']}"))
    room = limit - len(out)
    for p in untracked[: max(0, room)]:
        out.append(f"  {clean(p, 70)} | untracked")
    more = len(rows) - min(len(rows), limit) + max(0, len(untracked) - max(0, room))
    if more > 0:
        out.append(f"  … and {more} more")
    return out
