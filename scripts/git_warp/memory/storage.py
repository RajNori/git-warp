"""Securely locate Git Warp's private per-repository data directory."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ..git import git_common_dir


def git_warp_directory(cwd: str | Path, *, timeout: float = 5.0) -> Path:
    common = git_common_dir(cwd=cwd, timeout=timeout)
    target = common / "git-warp"
    try:
        info = target.lstat()
    except FileNotFoundError:
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = target.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(".git/git-warp exists but is not a real directory")
    resolved = target.resolve(strict=True)
    if resolved.parent != common:
        raise OSError(".git/git-warp resolves outside the Git common directory")
    try:
        os.chmod(resolved, 0o700)
    except OSError:
        # Windows/locked filesystems may not implement POSIX permission bits.
        pass
    return resolved


def require_regular_file(path: Path) -> None:
    """Reject symlink/device targets before opening local runtime data."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise OSError("Git Warp runtime file is not a regular file")
