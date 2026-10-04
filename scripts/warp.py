#!/usr/bin/env python3
"""Git Warp CLI: deterministic data gathering for skills.  ``warp.py <command> [args]``.

Every command prints JSON to stdout (errors included) and never mutates the
working tree, index or refs unless the command is explicitly documented to.
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# command -> module exposing ``main(argv: list[str]) -> int`` (argv[0] is the command)
COMMANDS = {
    "xray": "gitwarp.analysis.cli",
    "pr": "gitwarp.analysis.cli",
    "rescue": "gitwarp.history.cli",
    "archaeology": "gitwarp.history.cli",
    "bisect": "gitwarp.history.cli",
    "commits": "gitwarp.semantic.cli",
    "blast": "gitwarp.semantic.cli",
    "conflict": "gitwarp.semantic.cli",
    "temporal": "gitwarp.semantic.cli",
    "memory": "gitwarp.memory.cli",
    "guard": "gitwarp.safety.cli",
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        from gitwarp.core.output import emit
        emit({"error": "usage: warp.py <command> [args]" if argv and argv[0] not in ("-h", "--help") and argv[0] not in COMMANDS else None,
              "usage": "warp.py <command> [args]", "commands": sorted(COMMANDS)})
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    try:
        mod = importlib.import_module(COMMANDS[argv[0]])
    except ImportError as e:
        from gitwarp.core.output import emit
        emit({"error": f"command '{argv[0]}' is unavailable: {e}"})
        return 2
    return int(mod.main(argv) or 0)


if __name__ == "__main__":
    sys.exit(main())
