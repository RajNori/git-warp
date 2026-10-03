#!/usr/bin/env python3
"""Claude Code Stop entry point; emits a bounded status summary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from git_warp.hooks.common import event_cwd, read_event, write_json  # noqa: E402
from git_warp.hooks.stop import end_report  # noqa: E402
from git_warp.models import GitError  # noqa: E402


def main() -> int:
    event = read_event()
    if event is None:
        return 0
    cwd = event_cwd(event) or str(Path.cwd())
    try:
        report = end_report(cwd)
    except (GitError, OSError, RuntimeError, ValueError):
        return 0
    # systemMessage is visible to the user without feeding back into Claude's
    # context or causing a Stop-hook continuation loop.
    write_json({"systemMessage": report} if report else {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
