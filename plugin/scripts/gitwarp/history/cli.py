"""CLI for ``warp.py rescue | archaeology | bisect``.  JSON on stdout, never a traceback."""
from __future__ import annotations

from typing import Optional

from ..core import git
from ..core.output import emit
from . import archaeology, bisect, rescue
from ._common import HelpRequested, Parser, UsageError, add_repo_arg, resolve_repo


def _rescue(argv: list) -> int:
    sub = "scan"
    rest = argv
    if argv and argv[0] in ("scan", "inspect", "preserve"):
        sub, rest = argv[0], argv[1:]
    p = Parser(prog=f"warp.py rescue {sub}", add_help=True)
    add_repo_arg(p)
    if sub == "scan":
        p.add_argument("--since", default=None, help='only candidates seen/created since e.g. "2 hours ago"')
        p.add_argument("--grep", default=None, help="only candidates whose message contains TEXT")
        p.add_argument("--path", default=None, help="only candidates touching PATH")
        p.add_argument("--limit", type=int, default=25)
        p.add_argument("--no-fsck", action="store_true", help="skip the (slow) dangling-object scan")
        p.add_argument("--fsck-timeout", type=float, default=45.0)
        p.add_argument("--include-reachable", action="store_true", help="also list reflog commits that are still reachable from a branch")
    elif sub == "inspect":
        p.add_argument("sha")
    else:
        p.add_argument("sha")
        p.add_argument("--name", default=None)
        p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(rest)
    cwd, err = resolve_repo(a.repo)
    if err:
        emit({"error": err})
        return 2
    if sub == "scan":
        emit(rescue.scan(cwd, since=a.since, grep=a.grep, path=a.path, limit=max(1, a.limit), run_fsck=not a.no_fsck,
                         fsck_timeout=max(1.0, a.fsck_timeout), include_reachable=a.include_reachable))
        return 0
    if sub == "inspect":
        out = rescue.inspect(cwd, a.sha)
        emit(out)
        return 2 if "error" in out else 0
    out, code = rescue.preserve(cwd, a.sha, a.name, a.dry_run)
    emit(out)
    return code


def _archaeology(argv: list) -> int:
    p = Parser(prog="warp.py archaeology")
    add_repo_arg(p)
    p.add_argument("target", nargs="?", default=None, help="file or directory path (history is searched even if it was deleted)")
    p.add_argument("--symbol", default=None, help="git log -S: commits that changed the number of occurrences of this string")
    p.add_argument("--regex", default=None, help="git log -G: commits whose diff matches this regex")
    p.add_argument("--question", default=None, help="free-text question; keywords are matched against commit messages")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--since", default=None)
    a = p.parse_args(argv)
    cwd, err = resolve_repo(a.repo)
    if err:
        emit({"error": err})
        return 2
    out, code = archaeology.run(cwd, a.target, a.symbol, a.regex, a.question, max(1, a.limit), a.since)
    emit(out)
    return code


def _bisect(argv: list) -> int:
    sub = argv[0] if argv and argv[0] in ("plan", "status") else None
    if sub is None:
        raise UsageError("usage: bisect plan --good REF --bad REF [--test CMD] | bisect status")
    p = Parser(prog=f"warp.py bisect {sub}")
    add_repo_arg(p)
    if sub == "plan":
        p.add_argument("--good", action="append", default=[], help="known-good ref (repeatable)")
        p.add_argument("--bad", default=None, help="known-bad ref (default HEAD)")
        p.add_argument("--test", default=None, help="test command; validated syntactically and echoed, never executed")
    a = p.parse_args(argv[1:])
    cwd, err = resolve_repo(a.repo)
    if err:
        emit({"error": err})
        return 2
    out, code = bisect.plan(cwd, a.good, a.bad, a.test) if sub == "plan" else bisect.status(cwd)
    emit(out)
    return code


_DISPATCH = {"rescue": _rescue, "archaeology": _archaeology, "bisect": _bisect}


def main(argv: Optional[list] = None) -> int:
    argv = list(argv or [])
    if not argv or argv[0] not in _DISPATCH:
        emit({"error": "usage: warp.py rescue|archaeology|bisect ...", "commands": sorted(_DISPATCH)})
        return 2
    try:
        return _DISPATCH[argv[0]](argv[1:])
    except HelpRequested as h:
        emit({"usage": h.text})
        return 0
    except UsageError as e:
        emit({"error": f"usage error: {e}"})
        return 2
    except git.GitNotFound:
        emit({"error": "git executable not found on PATH"})
        return 2
    except git.GitTimeout as e:
        emit({"error": f"git timed out: {e}", "partial": True})
        return 2
    except git.GitError as e:
        emit({"error": f"git error: {str(e)[:300]}"})
        return 2
    except Exception as e:  # last resort: structured error, never a traceback
        emit({"error": f"internal error: {type(e).__name__}: {str(e)[:300]}"})
        return 1
