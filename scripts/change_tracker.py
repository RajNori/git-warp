#!/usr/bin/env python3
"""Claude Code PostToolUse entry point; records metadata only."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from git_warp.hooks.common import read_event, write_json  # noqa: E402
from git_warp.hooks.post_tool import handle  # noqa: E402


def main() -> int:
    event = read_event()
    if event is not None:
        handle(event)
    write_json({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
