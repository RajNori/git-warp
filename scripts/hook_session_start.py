#!/usr/bin/env python3
"""Claude Code SessionStart hook entry point (logic lives in gitwarp.hooks.session_start)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitwarp.hooks.session_start import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
