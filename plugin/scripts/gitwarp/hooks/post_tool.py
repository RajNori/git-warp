"""PostToolUse hook: append a redacted flight-recorder entry. Must be fast, silent and fail open.

Always exits 0 and prints exactly ``{}``.  Redaction happens inside ``recorder.record`` before anything is written.
"""
from __future__ import annotations

import os
import sys

from ..memory import recorder
from ._runtime import HookDeadline, cwd_of, deadline, emit, read_event

BUDGET_S = 6.0      # hooks.json allows 10 s


def main() -> int:
    try:
        with deadline(BUDGET_S):
            event = read_event()
            tool = event.get("tool_name")
            if isinstance(tool, str) and (tool in recorder.EDIT_TOOLS or tool in recorder.SHELL_TOOLS):
                cwd = cwd_of(event) or os.getcwd()
                recorder.record(cwd, {**event, "hook_event_name": "PostToolUse"})
    except (Exception, HookDeadline):
        pass
    try:
        emit({})
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
