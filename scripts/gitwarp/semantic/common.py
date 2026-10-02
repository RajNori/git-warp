"""Shared helpers for the semantic CLI commands (argument parsing, repo state, bounded file reads)."""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Optional

from ..core import git

MAX_FILE_BYTES = 1_000_000


class ArgError(Exception):
    pass


class JsonArgParser(argparse.ArgumentParser):
    """argparse that raises instead of printing usage and exiting."""

    def error(self, message):  # noqa: D401
        raise ArgError(message)

    def exit(self, status=0, message=None):
        raise ArgError(message or "invalid arguments")


def make_parser(prog: str) -> JsonArgParser:
    p = JsonArgParser(prog=f"warp.py {prog}", add_help=False)
    p.add_argument("--repo", default=None, help="repository path (default: cwd)")
    return p


def resolve_repo(path: Optional[str]):
    """Return ``(root, error_message)``; root is the work-tree top level."""
    cwd = path or os.getcwd()
    try:
        if not Path(cwd).exists():
            return None, f"path does not exist: {cwd}"
        root = git.repo_root(cwd)
    except (git.GitError, ValueError, OSError) as e:
        return None, f"cannot inspect repository: {e}"
    if root is None:
        return None, f"not a git repository (or bare): {cwd}"
    return root, None


def repo_state(root: Path) -> dict:
    """Branch/head/detached/unborn/shallow/operation as plain data, never raising."""
    st = {"branch": None, "head": None, "detached": False, "unborn": False, "shallow": False, "operation": None}
    try:
        st["head"] = git.head_sha(root)
        st["branch"] = git.current_branch(root)
        st["unborn"] = st["head"] is None
        st["detached"] = st["head"] is not None and st["branch"] is None
        st["shallow"] = git.is_shallow(root)
        st["operation"] = git.repo_operation(root)
    except git.GitError:
        pass
    return st


def state_warnings(st: dict) -> list:
    w = []
    if st["unborn"]:
        w.append("repository has no commits yet (unborn branch); history-based evidence is unavailable")
    if st["detached"]:
        w.append("HEAD is detached")
    if st["shallow"]:
        w.append("shallow clone: history is truncated, so historical evidence may be incomplete")
    return w


def read_text(path: Path, max_bytes: int = MAX_FILE_BYTES) -> Optional[str]:
    """Read a text file if it is small, regular and not binary; else None."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


class Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return max(0.0, self.end - time.monotonic())

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.end


def top_dir(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(root)"


def status_letter(xy: str) -> str:
    if xy == "??":
        return "?"
    if xy in ("DD", "AU", "UD", "UA", "DU", "AA", "UU"):
        return "U"
    for ch in ("R", "D", "A", "C"):
        if ch in xy:
            return ch if ch != "C" else "A"
    return "M"
