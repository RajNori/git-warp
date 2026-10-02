"""Thin Claude Code hook entry point for the Git Warp safety service."""

from git_warp.safety.hook import main


if __name__ == "__main__":
    raise SystemExit(main())
