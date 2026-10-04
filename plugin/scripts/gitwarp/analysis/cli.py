"""CLI for ``warp.py xray`` and ``warp.py pr``.  Always prints JSON; never a traceback."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gitwarp.core import git
from gitwarp.core.output import emit


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D401
        raise _ArgError(message)

    def exit(self, status=0, message=None):
        raise _ArgError(message or "invalid arguments")


def _parser(cmd: str) -> _Parser:
    p = _Parser(prog=f"warp.py {cmd}", add_help=False)
    p.add_argument("--repo", default=None)
    if cmd == "xray":
        p.add_argument("--untracked-all", action="store_true")
    else:
        p.add_argument("base", nargs="?", default=None)
        p.add_argument("--base", dest="base_opt", default=None)
    return p


def _resolve_root(repo_arg):
    path = Path(repo_arg).expanduser() if repo_arg else Path.cwd()
    if not path.is_dir():
        return None, f"path is not a directory: {path}"
    try:
        root = git.repo_root(path)
    except git.GitNotFound:
        return None, "git executable not found"
    if root is None:
        return None, f"not a git repository: {path}"
    return root, None


def main(argv) -> int:
    argv = list(argv)
    cmd = argv[0] if argv else ""
    if cmd not in ("xray", "pr"):
        emit({"error": f"unknown analysis command: {cmd!r}", "commands": ["xray", "pr"]})
        return 2
    try:
        ns = _parser(cmd).parse_args(argv[1:])
    except _ArgError as e:
        emit({"error": f"invalid arguments: {e}", "usage": "xray [--untracked-all] [--repo PATH]" if cmd == "xray" else "pr [BASE | --base REF] [--repo PATH]"})
        return 2
    root, err = _resolve_root(ns.repo)
    if err:
        emit({"error": err, "hint": "run inside a Git repository or pass --repo PATH"})
        return 2
    try:
        if cmd == "xray":
            from .xray import run_xray
            result = run_xray(root, untracked_all=ns.untracked_all)
        else:
            from .pr import run_pr
            if ns.base and ns.base_opt and ns.base != ns.base_opt:
                emit({"error": "conflicting base: positional BASE and --base differ"})
                return 2
            result = run_pr(root, ns.base_opt or ns.base)
    except git.GitNotFound:
        emit({"error": "git executable not found"})
        return 2
    except git.GitError as e:
        emit({"error": f"git failed: {str(e)[:300]}"})
        return 2
    except Exception as e:  # noqa: BLE001 - structured error instead of a traceback
        emit({"error": f"internal error: {type(e).__name__}: {str(e)[:300]}"})
        return 1
    emit(result)
    return 2 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
