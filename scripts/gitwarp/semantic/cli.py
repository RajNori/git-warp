"""Semantic CLI: ``warp.py commits|blast|conflict|temporal``.  JSON out, never a traceback."""
from __future__ import annotations

import sys

from ..core import git
from ..core.output import emit
from .common import ArgError, make_parser, resolve_repo

USAGE = {
    "commits": "commits [--staged] [--repo PATH]",
    "blast": "blast [paths...] [--base REF] [--depth N] [--max-nodes N] [--timeout S] [--repo PATH]",
    "conflict": "conflict [--repo PATH]",
    "temporal": "temporal [--base REF] [--limit N] [--budget N] [--timeout S] [--repo PATH]",
}


def _parse(cmd: str, argv: list):
    p = make_parser(cmd)
    if cmd == "commits":
        p.add_argument("--staged", action="store_true")
    elif cmd == "blast":
        p.add_argument("paths", nargs="*")
        p.add_argument("--base", default=None)
        p.add_argument("--depth", type=int, default=3)
        p.add_argument("--max-nodes", type=int, default=200)
        p.add_argument("--timeout", type=float, default=15.0)
    elif cmd == "temporal":
        p.add_argument("--base", default=None)
        p.add_argument("--limit", type=int, default=20)
        p.add_argument("--budget", type=int, default=25)
        p.add_argument("--timeout", type=float, default=30.0)
    return p.parse_args(argv)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd not in USAGE:
        emit({"error": f"unknown semantic command: {cmd!r}", "usage": sorted(USAGE.values())})
        return 2
    try:
        args = _parse(cmd, argv[1:])
    except ArgError as e:
        emit({"error": f"invalid arguments: {e}", "usage": "warp.py " + USAGE[cmd]})
        return 2
    root, err = resolve_repo(args.repo)
    if root is None:
        emit({"error": err, "warnings": [err]})
        return 2
    try:
        if cmd == "commits":
            from . import commits
            res = commits.analyze(root, staged_only=args.staged)
        elif cmd == "blast":
            from . import blast
            res = blast.analyze(root, args.paths or None, args.base, args.depth, args.max_nodes, args.timeout)
        elif cmd == "conflict":
            from . import conflict
            res = conflict.analyze(root)
        else:
            from . import temporal
            res = temporal.analyze(root, args.base, args.limit, args.budget, args.timeout)
    except git.GitError as e:
        emit({"error": f"git failed: {e}", "warnings": [str(e)]})
        return 2
    except Exception as e:  # noqa: BLE001 - the CLI contract is JSON, never a traceback
        emit({"error": f"internal error in '{cmd}': {type(e).__name__}: {e}", "warnings": ["please report this; analysis is incomplete"]})
        return 2
    emit(res)
    return 2 if "error" in res else 0
