#!/usr/bin/env python3
"""Claude Code SessionStart entry point."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from git_warp.hooks.common import event_cwd, read_event  # noqa: E402
from git_warp.hooks.session_start import repository_context  # noqa: E402
from git_warp.models import GitError  # noqa: E402


def main() -> int:
    event = read_event()
    if event is None:
        return 0
    cwd = event_cwd(event) or str(Path.cwd())
    try:
        print(repository_context(cwd))
    except (GitError, OSError, RuntimeError, ValueError):
        # Context enrichment is optional; malformed/non-repository state must
        # not prevent Claude Code from starting.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
