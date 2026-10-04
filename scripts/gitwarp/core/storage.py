"""Secure private-state storage (the single abstraction for everything Git Warp writes under ``.git/git-warp``).

SEAM STUB: behaviour is unchanged from the pre-convergence code; the Storage owner replaces the body.
"""
from __future__ import annotations

from pathlib import Path

STATE_DIRNAME = "git-warp"


def state_dir(common_git_dir: Path, create: bool = False) -> Path:
    d = Path(common_git_dir) / STATE_DIRNAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d
