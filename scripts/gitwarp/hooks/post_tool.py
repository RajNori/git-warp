"""PostToolUse hook: append a redacted flight-recorder entry. Must be fast and silent."""
from __future__ import annotations

import os
import sys

from ..core.output import read_hook_event, write_hook
from ..memory import recorder


def main() -> int:
    try:
        event = read_hook_event()
        tool = event.get("tool_name")
        if tool in recorder.EDIT_TOOLS or tool in recorder.SHELL_TOOLS:
            cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and event.get("cwd") else os.getcwd()
            recorder.record(cwd, {**event, "hook_event_name": "PostToolUse"})
    except Exception:
        pass
    try:
        write_hook({})
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
