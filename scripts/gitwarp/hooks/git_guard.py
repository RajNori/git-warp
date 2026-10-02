"""PreToolUse guard for Bash: classify the command, deny/ask/allow.  Fail-safe (ADR-6), never raises, exit 0."""
from __future__ import annotations

import os
import signal
import sys
import time

from gitwarp.core import git
from gitwarp.core.config import Config, load_config
from gitwarp.core.output import pretool_decision, read_hook_event, write_hook
from gitwarp.safety.classifier import MAX_COMMAND_CHARS, classify_command

GUARD_ERROR_REASON = "Git Warp guard error — verify manually"
GUARD_TIMEOUT_REASON = "Git Warp guard timed out — verify manually"
GIT_LOOKUP_TIMEOUT_S = 2.0   # per git lookup (repo root, branch); on timeout fall back to defaults and still classify
GUARD_DEADLINE_S = 6.0       # total budget; the host kills the hook at 10 s and then runs the command unguarded


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


class _GuardTimeout(BaseException):
    """Internal wall-clock deadline hit (BaseException so no inner ``except Exception`` can swallow it)."""


def _on_alarm(signum, frame):  # pragma: no cover - exercised through the deadline tests
    raise _GuardTimeout()


def _arm(seconds: float) -> bool:
    """Arm a SIGALRM wall-clock timer where available (POSIX main thread)."""
    try:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return True
    except (AttributeError, ValueError, OSError):
        return False


def _disarm() -> None:
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except (AttributeError, ValueError, OSError):
        pass


def _lookups(event: dict, command: str, check):
    """Config + current branch.  Any git failure/timeout falls back to defaults (classification still runs)."""
    cfg, branch = Config(), None
    if "git" in command.lower() and len(command) <= MAX_COMMAND_CHARS:
        cwd = event.get("cwd")
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else (os.getcwd() if cwd is None else None)
        if cwd is not None:
            try:
                root = git.repo_root(cwd, timeout=GIT_LOOKUP_TIMEOUT_S)
                cfg = load_config(root)
            except Exception:  # noqa: BLE001 - GitTimeout/GitError/anything: default config
                cfg = Config()
            check()
            try:
                branch = git.current_branch(cwd, timeout=GIT_LOOKUP_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                branch = None
            check()
    return cfg, branch


def main() -> int:
    command = None
    armed = _arm(GUARD_DEADLINE_S)
    t0 = time.monotonic()

    def check() -> None:        # monotonic fallback between phases when no signal timer is available
        if time.monotonic() - t0 > GUARD_DEADLINE_S:
            raise _GuardTimeout()

    try:
        event = read_hook_event()
        command = _extract_command(event)
        if not command or not command.strip():
            _disarm()
            _emit_allow()
            return 0
        cfg, branch = _lookups(event, command, check)
        v = classify_command(command, cfg, branch)
        check()
        _disarm()
        if v.decision in ("deny", "ask"):
            write_hook(pretool_decision(v.decision, _message(v)))
        else:
            _emit_allow()
        return 0
    except BaseException as e:  # noqa: BLE001 - fail safe, never crash Claude
        _disarm()
        try:
            if isinstance(e, _GuardTimeout):
                write_hook(pretool_decision("ask", GUARD_TIMEOUT_REASON))
            elif isinstance(command, str) and "git" in command.lower():
                write_hook(pretool_decision("ask", GUARD_ERROR_REASON))
            else:
                _emit_allow()
        except Exception:
            pass
        return 0
    finally:
        if armed:
            _disarm()
