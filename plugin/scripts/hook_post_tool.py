#!/usr/bin/env python3
"""Claude Code PostToolUse hook entry point (logic lives in gitwarp.hooks.post_tool)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitwarp.hooks.post_tool import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
