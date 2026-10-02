"""``warp.py guard check "<command>"`` -> JSON verdict (debug / test aid; never runs the command)."""
from __future__ import annotations

import argparse
from typing import Optional

from gitwarp.core import git
from gitwarp.core.config import Config, load_config
from gitwarp.core.output import emit, fail

from .classifier import classify_command


def main(argv=None) -> int:
    argv = list(argv or [])
    if argv and argv[0] == "guard":
        argv = argv[1:]
    p = argparse.ArgumentParser(prog="warp.py guard", description="Classify a shell command with the Git Warp guard.")
    sub = p.add_subparsers(dest="action")
    c = sub.add_parser("check", help="classify a command string")
    c.add_argument("command", help="the command string to classify (it is NOT executed)")
    c.add_argument("--repo", default=None, help="repository for config / current branch (default: cwd)")
    c.add_argument("--branch", default=None, help="assume this current branch instead of looking it up")
    c.add_argument("--mode", choices=("standard", "strict"), default=None, help="override safety_mode")
    c.add_argument("--protected", default=None, help="comma separated protected branch patterns")
    try:
        ns = p.parse_args(argv)
    except SystemExit:
        return fail("usage: warp.py guard check \"<command>\" [--repo P] [--branch B] [--mode strict] [--protected a,b]")
    if ns.action != "check":
        return fail("usage: warp.py guard check \"<command>\"")
    cfg: Config = load_config(git.repo_root(ns.repo))
    if ns.mode:
        cfg.safety_mode = ns.mode
    if ns.protected is not None:
        cfg.protected_branches = [x.strip() for x in ns.protected.split(",") if x.strip()]
    branch: Optional[str] = ns.branch
    if branch is None:
        try:
            branch = git.current_branch(ns.repo)
        except Exception:
            branch = None
    v = classify_command(ns.command, cfg, branch)
    emit({**v.to_dict(), "branch": branch, "safety_mode": cfg.safety_mode})
    return 0
