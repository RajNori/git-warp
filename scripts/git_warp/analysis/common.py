"""Shared bounded Git reads and parsing helpers for analysis services."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..git import DEFAULT_TIMEOUT_SECONDS, repo_root, run_git
from ..models import GitCommandError
from .models import Evidence


def read(cwd: str | Path, args: Iterable[str], timeout: float = DEFAULT_TIMEOUT_SECONDS, *, check: bool = True) -> str:
    result = run_git(tuple(args), cwd=cwd, timeout=timeout)
    if check and result.returncode:
        raise GitCommandError(result)
    return result.stdout


def root(cwd: str | Path, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Path:
    """Resolve nested input directories to the owning worktree root."""
    return repo_root(cwd=cwd, timeout=timeout)


def head(cwd: str | Path, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str | None:
    value = read(cwd, ("rev-parse", "--verify", "--quiet", "--end-of-options", "HEAD^{commit}"), timeout, check=False).strip()
    return value or None


def changed_paths(cwd: str | Path, base: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[str, ...]:
    if base:
        raw = read(cwd, ("diff", "--name-only", "-z", "--no-ext-diff", base, "--"), timeout)
        paths = list(p for p in raw.split("\0") if p)
        # A revision diff does not include untracked files; include those as
        # independent worktree evidence without treating them as diff content.
        status = read(cwd, ("status", "--porcelain=v1", "-z", "--untracked-files=all"), timeout)
        parts = status.split("\0")
        i = 0
        while i < len(parts) and parts[i]:
            entry = parts[i]
            path = entry[3:] if len(entry) > 3 else ""
            if path and entry.startswith("??") and path not in paths:
                paths.append(path)
            i += 1
            if entry[:2].find("R") >= 0 or entry[:2].find("C") >= 0:
                i += 1
        return tuple(paths)
    raw = read(cwd, ("status", "--porcelain=v1", "-z", "--untracked-files=all"), timeout)
    parts = raw.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(parts) and parts[i]:
        entry = parts[i]
        path = entry[3:] if len(entry) > 3 else ""
        if path:
            paths.append(path)
        i += 1
        if entry[:2].find("R") >= 0 or entry[:2].find("C") >= 0:
            i += 1
    return tuple(paths)


def diff_stat(cwd: str | Path, base: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    args = ["diff", "--stat", "--no-ext-diff"]
    if base:
        args.extend((base, "--"))
    return read(cwd, args, timeout)


def log_records(cwd: str | Path, revs: str = "HEAD", limit: int = 100, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[tuple[str, str, str], ...]:
    # Unit-separator fields and NUL record separators keep subject/path text intact.
    raw = read(cwd, ("log", "--format=%H%x1f%s%x1f%an%x00", f"-n{limit}", revs, "--"), timeout, check=False)
    records = []
    for item in raw.split("\0"):
        fields = item.strip("\r\n").split("\x1f")
        if len(fields) >= 3 and re.fullmatch(r"[0-9a-fA-F]{7,40}", fields[0]):
            records.append((fields[0], fields[1], fields[2]))
    return tuple(records)


def evidence_paths(paths: Iterable[str], source: str = "changed path") -> tuple[Evidence, ...]:
    return tuple(Evidence(source, p) for p in paths)


def porcelain_entries(cwd: str | Path, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[tuple[str, str], ...]:
    """Return (XY, path) entries, including rename destinations, from -z status."""
    parts = read(cwd, ("status", "--porcelain=v1", "-z", "--untracked-files=all"), timeout).split("\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(parts) and parts[i]:
        entry = parts[i]
        xy = entry[:2]
        path = entry[3:] if len(entry) > 3 else ""
        if path:
            entries.append((xy, path))
        i += 1
        if "R" in xy or "C" in xy:
            if i < len(parts) and parts[i]:
                entries.append((xy, parts[i]))
            i += 1
    return tuple(entries)


def source_files(root: Path, *, limit: int = 5000) -> tuple[Path, ...]:
    ignored = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__"}
    result = []
    for path in root.rglob("*"):
        if len(result) >= limit:
            break
        if path.is_file() and not any(part in ignored for part in path.relative_to(root).parts) and path.suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            result.append(path)
    return tuple(result)
