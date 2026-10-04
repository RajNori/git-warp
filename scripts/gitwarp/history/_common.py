"""Shared helpers for the history/forensics CLI (rescue, archaeology, bisect)."""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Optional

from ..core import git, revisions
from ..core.output import emit
from ..core.redact import redact

_HEX = re.compile(r"^[0-9a-fA-F]{4,64}$")


class UsageError(Exception):
    """Bad command line; reported as structured JSON, exit 2."""


class HelpRequested(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


class Parser(argparse.ArgumentParser):
    """argparse that raises instead of printing to stderr / calling sys.exit."""

    def error(self, message):
        raise UsageError(message)

    def exit(self, status=0, message=None):
        if status == 0:
            raise HelpRequested(self.format_help())
        raise UsageError(message or "invalid arguments")


def add_repo_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=None, help="repository path (default: current directory)")


def resolve_repo(path: Optional[str]) -> tuple:
    """Return ``(cwd, error_message)``. ``cwd`` is a directory inside a work tree."""
    cwd = Path(path).expanduser() if path else Path(os.getcwd())
    if not cwd.exists() or not cwd.is_dir():
        return None, f"path does not exist or is not a directory: {cwd}"
    if not git.is_git_repository(cwd):
        return None, f"not a git repository (or no work tree): {cwd}"
    return cwd, None


def clip(text: str, n: int = 300) -> str:
    text = redact((text or "")[: max(1000, n * 4)])
    return text if len(text) <= n else text[: n - 1] + "…"


def brief(c: "git.Commit") -> dict:
    return {"sha": c.sha, "short": c.short, "date": c.commit_date, "author": c.author_name,
            "subject": clip(c.subject, 200), "parents": [p[:8] for p in c.parents]}


def is_hexish(s: str) -> bool:
    return bool(_HEX.match(s or ""))


def commit_files(sha: str, cwd, limit: int = 500) -> tuple:
    """Files changed by a commit (merge-like/stash commits are diffed against parent 1).

    Returns ``(files, total)`` with ``files`` capped at ``limit``.
    """
    sha = revisions.sha_of(sha, cwd)
    r = git.run(["rev-list", "--parents", "-n1", "--end-of-options", sha], cwd=cwd)
    parts = r.text.split() if r.ok else []
    if len(parts) >= 3:
        d = git.run(["diff", "--name-only", "-z", parts[1], sha, "--"], cwd=cwd, timeout=30)
    else:
        d = git.run(["diff-tree", "-r", "--root", "--no-commit-id", "--name-only", "-z", sha, "--"], cwd=cwd, timeout=30)
    names = [p for p in d.stdout.split("\x00") if p] if d.ok else []
    return names[:limit], len(names)


def refs_containing(sha: str, cwd, max_names: int = 6) -> list:
    """Branches/tags/remotes/stash that contain ``sha`` (empty = unreachable from refs)."""
    sha = revisions.sha_of(sha, cwd)
    r = git.run(["for-each-ref", "--contains", sha, "--format=%(refname:short)",
                 "refs/heads", "refs/tags", "refs/remotes", "refs/stash"], cwd=cwd, timeout=30)
    names = r.lines if r.ok else []
    return names[:max_names] if max_names else names


def is_ancestor(a: str, b: str, cwd) -> Optional[bool]:
    return git.is_ancestor(a, b, cwd)


def repo_state(cwd) -> dict:
    """Common state block: branch, head, detached, unborn, shallow, operation."""
    head = git.head_sha(cwd)
    branch = git.current_branch(cwd)
    return {
        "branch": branch,
        "head": head,
        "detached": branch is None and head is not None,
        "unborn": head is None,
        "shallow": git.is_shallow(cwd),
        "operation": git.repo_operation(cwd),
    }


def finish(payload: dict, code: int = 0) -> int:
    emit(payload)
    return code


def error(msg: str, code: int = 2, **extra) -> int:
    emit({"error": msg, **extra})
    return code
