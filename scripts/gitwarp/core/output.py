"""Shared I/O helpers: CLI JSON emission and Claude hook protocol output."""
from __future__ import annotations

import json
import sys
from typing import Optional


def emit(obj, stream=None) -> None:
    """Print ``obj`` as stable, indented JSON (what skills read)."""
    (stream or sys.stdout).write(json.dumps(obj, indent=2, sort_keys=False, default=str, ensure_ascii=False) + "\n")


def fail(message: str, code: int = 2, **extra) -> int:
    """Print a structured error to stdout (JSON) and return ``code`` — never silently swallow."""
    emit({"error": message, **extra})
    return code


def read_hook_event(stream=None) -> dict:
    """Parse hook stdin. Malformed/empty input → ``{}`` (callers must then fail open without side effects)."""
    try:
        raw = (stream or sys.stdin).read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def pretool_decision(decision: str, reason: str, context: Optional[str] = None) -> dict:
    """PreToolUse response. ``decision`` is allow | deny | ask."""
    assert decision in ("allow", "deny", "ask")
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}
    if context:
        out["hookSpecificOutput"]["additionalContext"] = context
    return out


def additional_context(event_name: str, text: str) -> dict:
    """Context injection for SessionStart / UserPromptSubmit / PostToolUse."""
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}


def write_hook(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
