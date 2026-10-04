#!/usr/bin/env python3
"""Claude Code PreToolUse hook entry point (logic lives in gitwarp.hooks.git_guard)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitwarp.hooks.git_guard import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
