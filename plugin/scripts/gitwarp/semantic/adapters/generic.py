"""Generic fallback: filename/symbol-stem text search.

Uses ``git grep`` (through ``core.git``; this repo allows no other subprocess users) and degrades
to a bounded pure-Python scan of the supplied file list when ``git grep`` is unavailable or fails.
"""
from __future__ import annotations

import re
import time
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from ...core import git

MAX_FILE_BYTES = 1_000_000
MAX_SCAN_BYTES = 40_000_000
GENERIC_STEMS = {"index", "main", "util", "utils", "types", "type", "common", "base", "init", "app", "test", "tests", "config", "mod", "lib", "core", "helpers", "constants", "__init__", "setup"}


def search_token(root: Path, token: str, files: Iterable[str], deadline: Optional[float] = None, exclude: Iterable[str] = (), limit: int = 40) -> tuple:
    """Return ``(paths, method)`` of files mentioning ``token`` as a whole word.

    ``method`` is ``"git-grep"`` or ``"python-scan"``.  Never raises.
    """
    excl = set(exclude)
    try:
        hits = _git_grep(root, token, deadline)
        method = "git-grep"
    except Exception:  # noqa: BLE001 - any failure degrades to the pure-Python scan
        hits = _py_scan(root, token, files, deadline)
        method = "python-scan"
    out = []
    for h in hits:
        if h in excl:
            continue
        out.append(h)
        if len(out) >= limit:
            break
    return out, method


def _git_grep(root: Path, token: str, deadline: Optional[float]) -> list:
    timeout = 10.0 if deadline is None else max(1.0, deadline - time.monotonic())
    r = git.run(["grep", "-l", "-I", "-w", "-F", "-z", "-e", token, "--"], cwd=root, timeout=timeout)
    if r.returncode not in (0, 1):
        raise git.GitError("git grep failed", r.args, r.returncode, r.stderr)
    return sorted(p for p in r.stdout.split("\x00") if p)


def _py_scan(root: Path, token: str, files: Iterable[str], deadline: Optional[float]) -> list:
    rx = re.compile(r"(?<![\w])" + re.escape(token) + r"(?![\w])")
    hits, total = [], 0
    for rel in files:
        if deadline is not None and time.monotonic() > deadline:
            break
        p = root / rel
        try:
            size = p.stat().st_size
            if size > MAX_FILE_BYTES or not p.is_file() or p.is_symlink():
                continue
            total += size
            if total > MAX_SCAN_BYTES:
                break
            data = p.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue
        if rx.search(data.decode("utf-8", errors="replace")):
            hits.append(rel)
    return sorted(hits)


class GenericAdapter:
    """Adapter-shaped wrapper: no import parsing; dependants come from :func:`search_token`."""
    name = "generic"
    extensions: set = set()

    def imports(self, path: str, text: str) -> list:
        return []

    def exports(self, path: str, text: str) -> list:
        return []

    def resolve(self, spec: str, from_path: str, files) -> Optional[str]:
        return None

    @staticmethod
    def stem_token(path: str) -> Optional[str]:
        stem = PurePosixPath(path).stem
        if stem.lower() in GENERIC_STEMS or len(stem) < 4:
            parent = PurePosixPath(path).parent.name
            if stem.lower() in GENERIC_STEMS and parent and len(parent) >= 4:
                return parent
            return None
        return stem
