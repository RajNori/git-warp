"""PreToolUse guard for Bash: classify the command with ``classify_command`` -> DENY / ASK / DEFER.

* DENY / ASK are printed as a PreToolUse ``permissionDecision``.
* DEFER (no Guardian objection) prints ``{}``: ordinary Claude Code permissions decide.  It is not an approval.
* Fail-safe: a payload the guard cannot interpret and that could be a Bash command (empty / malformed / non-object
  JSON, missing or non-object ``tool_input``, non-string ``command``, odd ``tool_name`` carrying a command, oversized
  payload) is ASK, never a silent pass.  Events for other tools are ``{}``.  Internal errors and the wall-clock
  deadline are ASK for anything that mentions git or cannot be classified.  Never raises, always exits 0.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time

from gitwarp.core import git
from gitwarp.core.config import load_config
from gitwarp.core.output import pretool_decision, write_hook
from gitwarp.safety.classifier import MAX_COMMAND_CHARS, classify_command

GUARD_ERROR_REASON = "Git Warp guard error — verify manually"
GUARD_TIMEOUT_REASON = "Git Warp guard timed out — verify manually"
GUARD_UNREADABLE_REASON = "Git Warp could not interpret this tool call ({why}) — verify manually"
MAX_PAYLOAD_CHARS = 2_000_000   # larger hook payloads are not parsed; they are ASK
GIT_LOOKUP_TIMEOUT_S = 2.0   # per git lookup (repo root, branch); on timeout fall back to defaults and still classify
GUARD_DEADLINE_S = 6.0       # total budget; the host kills the hook at 10 s and then runs the command unguarded


def _emit_defer() -> None:
    """DEFER: Guardian has no objection; ordinary Claude permissions decide.  Not an approval."""
    sys.stdout.write("{}")


def _message(v) -> str:
    text = f"Git Warp: {v.reason}"
    if v.safer:
        text += " Safer options: " + " | ".join(v.safer[:4])
    if v.decision == "deny":
        text += " If this is really intended, ask the user to run it themselves."
    return text


class _Unreadable(Exception):
    """The payload cannot be interpreted; carries a short human reason."""


def _read_payload():
    """Parse hook stdin.  Raises _Unreadable for empty / oversized / malformed / non-object input."""
    try:
        reconfigure = getattr(sys.stdin, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
        raw = sys.stdin.read(MAX_PAYLOAD_CHARS + 1)
    except (OSError, ValueError):
        raise _Unreadable("unreadable input")
    if len(raw) > MAX_PAYLOAD_CHARS:
        raise _Unreadable("oversized payload")
    if not raw.strip():
        raise _Unreadable("empty input")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise _Unreadable("malformed JSON")
    if not isinstance(data, dict):
        raise _Unreadable("payload is not a JSON object")
    return data


def _extract_command(event: dict):
    """Return ``(command, force_ask_reason)``.  ``command`` None => nothing to classify (``{}`` unless a reason is set)."""
    tool = event.get("tool_name")
    ti = event.get("tool_input")
    if isinstance(tool, str) and tool != "Bash":
        return None, None                                   # another tool: not Guardian's business
    if not isinstance(ti, dict):
        if tool is None and ti is None:
            raise _Unreadable("no tool_name or tool_input")
        raise _Unreadable("tool_input is not an object")
    if "command" not in ti:
        if tool == "Bash":
            raise _Unreadable("Bash call without a command")
        return None, None
    cmd = ti.get("command")
    if not isinstance(cmd, str):
        raise _Unreadable("command is not a string")
    if tool is not None and tool != "Bash":                 # list / number / object tool_name that carries a command
        return cmd, "unexpected tool_name"
    return cmd, None


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
    cfg, branch = load_config(None), None      # built-in floor + user policy even outside a repository
    if "git" in command.lower() and len(command) <= MAX_COMMAND_CHARS:
        cwd = event.get("cwd")
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else (os.getcwd() if cwd is None else None)
        if cwd is not None:
            try:
                root = git.repo_root(cwd, timeout=GIT_LOOKUP_TIMEOUT_S)
                cfg = load_config(root)
            except Exception:  # noqa: BLE001 - GitTimeout/GitError/anything: floor + user policy only
                cfg = load_config(None)
            check()
            try:
                branch = git.current_branch(cwd, timeout=GIT_LOOKUP_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                branch = None
            check()
    return cfg, branch


def main() -> int:
    command = None
    parsed = False
    armed = _arm(GUARD_DEADLINE_S)
    t0 = time.monotonic()

    def check() -> None:        # monotonic fallback between phases when no signal timer is available
        if time.monotonic() - t0 > GUARD_DEADLINE_S:
            raise _GuardTimeout()

    try:
        try:
            event = _read_payload()
            parsed = True
            command, force_ask = _extract_command(event)
        except _Unreadable as e:
            _disarm()
            write_hook(pretool_decision("ask", GUARD_UNREADABLE_REASON.format(why=e)))
            return 0
        if command is None or not command.strip():
            _disarm()
            _emit_defer()
            return 0
        cfg, branch = _lookups(event, command, check)
        v = classify_command(command, cfg, branch)
        check()
        _disarm()
        if v.decision in ("deny", "ask"):
            write_hook(pretool_decision(v.decision, _message(v)))
        elif force_ask:
            write_hook(pretool_decision("ask", GUARD_UNREADABLE_REASON.format(why=force_ask)))
        else:
            _emit_defer()
        return 0
    except BaseException as e:  # noqa: BLE001 - fail safe, never crash Claude
        _disarm()
        try:
            if isinstance(e, _GuardTimeout):
                write_hook(pretool_decision("ask", GUARD_TIMEOUT_REASON))
            elif not parsed or (isinstance(command, str) and "git" in command.lower()):
                write_hook(pretool_decision("ask", GUARD_ERROR_REASON))
            else:
                _emit_defer()
        except Exception:
            pass
        return 0
    finally:
        if armed:
            _disarm()
