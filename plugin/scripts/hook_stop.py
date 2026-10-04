#!/usr/bin/env python3
"""Claude Code Stop hook entry point (logic lives in gitwarp.hooks.stop)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gitwarp.hooks.stop import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
