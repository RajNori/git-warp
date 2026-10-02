#!/usr/bin/env python3
"""Query or incrementally update Git Warp's local repository memory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from git_warp.memory import RepositoryIndex  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("index", "hotspots", "cochanges", "history"))
    parser.add_argument("path", nargs="?", help="repository-relative path for cochanges/history")
    parser.add_argument("--cwd", default=".", help="repository directory (default: current directory)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        index = RepositoryIndex(args.cwd)
        result: object
        if args.action == "index":
            result = index.index_history(batch_size=args.batch_size)
        else:
            index.index_history(batch_size=args.batch_size)
            if args.action == "hotspots":
                result = index.hotspots(limit=args.limit)
            elif args.action == "cochanges":
                if not args.path:
                    parser.error("cochanges requires a repository-relative path")
                result = index.cochanges(args.path, limit=args.limit)
            else:
                if not args.path:
                    parser.error("history requires a repository-relative path")
                result = index.commit_history(args.path, limit=args.limit)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Git Warp memory unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
