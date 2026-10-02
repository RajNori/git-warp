"""PreToolUse guard for Bash: classify the command, deny/ask/allow.  Fail-safe (ADR-6), never raises, exit 0."""
from __future__ import annotations

import os
import sys

from gitwarp.core import git
from gitwarp.core.config import Config, load_config
from gitwarp.core.output import pretool_decision, read_hook_event, write_hook
from gitwarp.safety.classifier import MAX_COMMAND_CHARS, classify_command

GUARD_ERROR_REASON = "Git Warp guard error — verify manually"


def _emit_allow() -> None:
    sys.stdout.write("{}")


def _message(v) -> str:
    text = f"Git Warp: {v.reason}"
    if v.safer:
        text += " Safer options: " + " | ".join(v.safer[:4])
    if v.decision == "deny":
        text += " If this is really intended, ask the user to run it themselves."
    return text


def _extract_command(event: dict):
    tool = event.get("tool_name")
    ti = event.get("tool_input")
    if tool not in (None, "Bash") or not isinstance(ti, dict):
        return None
    cmd = ti.get("command")
    return cmd if isinstance(cmd, str) else None


def main() -> int:
    command = None
    try:
        event = read_hook_event()
        command = _extract_command(event)
        if not command or not command.strip():
            _emit_allow()
            return 0
        cfg, branch = Config(), None
        low = command.lower()
        if "git" in low and len(command) <= MAX_COMMAND_CHARS:
            cwd = event.get("cwd")
            cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else (os.getcwd() if cwd is None else None)
            if cwd is not None:
                try:
                    cfg = load_config(git.repo_root(cwd))
                    branch = git.current_branch(cwd)
                except Exception:
                    cfg, branch = Config(), None
        v = classify_command(command, cfg, branch)
        if v.decision in ("deny", "ask"):
            write_hook(pretool_decision(v.decision, _message(v)))
        else:
            _emit_allow()
        return 0
    except BaseException:  # noqa: BLE001 - fail safe, never crash Claude
        try:
            if isinstance(command, str) and "git" in command.lower():
                write_hook(pretool_decision("ask", GUARD_ERROR_REASON))
            else:
                _emit_allow()
        except Exception:
            pass
        return 0
