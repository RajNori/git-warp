"""``warp.py memory <sub>`` — JSON-only access to repository memory."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..core import git
from ..core.config import load_config
from ..core.output import emit
from . import index, recorder, state

SUBS = ("index", "status", "cochange", "hotspots", "churn", "introduced", "reverts", "authors", "sessions", "forget")


class _CliError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # structured instead of printing usage to stderr + exit
        raise _CliError(message)


def _parser() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument("--repo", default=None, help="repository path (default: current directory)")
    p = _Parser(prog="warp.py memory", parents=[common])
    sub = p.add_subparsers(dest="sub")
    s = sub.add_parser("index", parents=[common]); s.add_argument("--max-commits", type=int, default=None); s.add_argument("--rebuild", action="store_true")
    sub.add_parser("status", parents=[common])
    s = sub.add_parser("cochange", parents=[common]); s.add_argument("path"); s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("hotspots", parents=[common]); s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("churn", parents=[common]); s.add_argument("prefix", nargs="?", default=None); s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("introduced", parents=[common]); s.add_argument("path")
    s = sub.add_parser("reverts", parents=[common]); s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("authors", parents=[common]); s.add_argument("path")
    s = sub.add_parser("sessions", parents=[common]); s.add_argument("--limit", type=int, default=10)
    s = sub.add_parser("forget", parents=[common]); s.add_argument("--yes", action="store_true")
    return p


def _norm(path: str, cwd: str, root: Path) -> str:
    """Accept repo-relative paths, cwd-relative paths, or absolute paths; return repo-relative POSIX."""
    p = Path(path)
    try:
        rroot = Path(os.path.realpath(root))
        if p.is_absolute():
            return Path(os.path.realpath(p)).relative_to(rroot).as_posix()
        cand = Path(cwd) / p
        if not (rroot / p).exists() and cand.exists():
            return Path(os.path.realpath(cand)).relative_to(rroot).as_posix()
    except (ValueError, OSError):
        pass
    s = p.as_posix()
    return s[2:] if s.startswith("./") else s


def _fresh(res: dict) -> dict:
    keys = ("mode", "indexed", "total_commits", "complete", "head", "shallow", "persisted", "elapsed_ms", "reason")
    return {k: res[k] for k in keys if k in res}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "memory":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
        if not args.sub:
            emit({"error": "usage: warp.py memory <sub> [args]", "subcommands": list(SUBS)})
            return 2
        cwd = os.path.abspath(args.repo) if args.repo else os.getcwd()
        if not os.path.isdir(cwd):
            emit({"error": f"not a directory: {cwd}"})
            return 2
        root = git.repo_root(cwd)
        if root is None:
            emit({"error": "not a git repository", "repo": cwd})
            return 2
        cfg = load_config(root)
        out = {"command": f"memory {args.sub}", "repo": str(root)}
        warnings: list = list(cfg.warnings)
        sub = args.sub

        if sub == "status":
            out["enabled"] = {"memory": cfg.memory_enabled, "recorder": cfg.recorder_enabled, "retention_days": cfg.recorder_retention_days}
            out["state_dir"] = str(git.state_dir(cwd))
            out["index"] = index.index_status(cwd)
            out["recorder"] = recorder.stats(cwd)
            out["sessions_recorded"] = len(recorder.summarize_sessions(cwd, limit=10_000))
        elif sub == "sessions":
            out["sessions"] = recorder.summarize_sessions(cwd, limit=args.limit)
            out["known_sessions"] = index.list_sessions(cwd, limit=args.limit)
            out["note"] = "from the local flight recorder (paths and redacted commands only; no file contents or prompts)"
            if not cfg.recorder_enabled:
                warnings.append("flight recorder is disabled (recorder_enabled: false)")
        elif sub == "forget":
            sd = git.state_dir(cwd)
            targets = [sd / n for n in (index.DB_NAME, index.DB_NAME + "-wal", index.DB_NAME + "-shm", index.DB_NAME + "-journal",
                                        index.DB_NAME + ".corrupt", recorder.RECORDER_FILE, recorder.RECORDER_FILE + ".1")]
            existing = [t for t in targets if t.exists()]
            if not args.yes:
                emit({"error": "refusing to delete without --yes", "would_delete": [str(t) for t in existing], "state_dir": str(sd)})
                return 2
            deleted = []
            for t in existing:
                try:
                    t.unlink()
                    deleted.append(str(t))
                except OSError as e:
                    warnings.append(f"could not delete {t.name}: {e}")
            index._MEM.clear()
            out["deleted"] = deleted
            out["kept"] = "state.json and everything else outside warp.db / flight-recorder.jsonl"
        else:
            if not cfg.memory_enabled:
                emit({**out, "error": "repository memory is disabled (memory_enabled: false in .claude/git-warp.local.md)"})
                return 2
            if sub == "index":
                res = index.ensure_indexed(cwd, max_commits=args.max_commits, rebuild=args.rebuild)
                out["index"] = res
                if res.get("error"):
                    emit({**out, "error": res["error"]})
                    return 2
            else:
                res = index.ensure_indexed(cwd, max_commits=index.AUTO_CAP, time_budget=60.0)
                out["index"] = _fresh(res)
                warnings.extend(res.get("warnings", []))
                if res.get("error"):
                    emit({**out, "error": res["error"]})
                    return 2
                if sub == "cochange":
                    path = _norm(args.path, cwd, root)
                    out.update(path=path, cochange=index.cochange(cwd, path, args.limit),
                               note="count = commits in which both files changed (commits touching >%d files and merges excluded); ratio = count / commits touching the path. Correlation, not causation." % index.COCHANGE_MAX_FILES)
                elif sub == "hotspots":
                    out.update(hotspots=index.hotspots(cwd, args.limit), formula=index.HOTSPOT_FORMULA,
                               note="generated/vendored/lock files and deleted files are excluded; ranking is relative evidence, not a defect prediction")
                elif sub == "churn":
                    prefix = _norm(args.prefix, cwd, root) if args.prefix else None
                    out.update(prefix=prefix, churn=index.churn(cwd, prefix, args.limit))
                elif sub == "introduced":
                    path = _norm(args.path, cwd, root)
                    out["introduced"] = index.introduced(cwd, path)
                    out["history"] = index.file_commits(cwd, path, 10)
                elif sub == "reverts":
                    out.update(index.reverts(cwd, args.limit))
                elif sub == "authors":
                    out.update(index.authors(cwd, _norm(args.path, cwd, root)))
            warnings.extend(res.get("warnings", [])) if sub == "index" else None
        seen = set()
        out["warnings"] = [w for w in warnings if not (w in seen or seen.add(w))]
        if sub == "index":
            out["index"].pop("warnings", None)
        emit(out)
        return 0
    except _CliError as e:
        emit({"error": str(e), "subcommands": list(SUBS)})
        return 2
    except SystemExit:
        raise
    except git.GitError as e:
        emit({"error": f"git error: {e}"})
        return 2
    except Exception as e:  # structured, never a traceback
        emit({"error": f"{type(e).__name__}: {e}", "hint": "if the index looks corrupt run `memory index --rebuild`"})
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
