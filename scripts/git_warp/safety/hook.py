"""Claude Code PreToolUse adapter for the pure Guardian classifier."""

from __future__ import annotations

import json
import sys
from typing import Any

from .classifier import Classification, Decision, classify_command


MAX_EVENT_BYTES = 1_048_576


def _ask(code: str, message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"Git Warp ({code}): {message}",
        }
    }


def output_for_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return _ask("invalid_event", "the hook event is not a JSON object; review the command manually.")
    tool_name = event.get("tool_name")
    if tool_name != "Bash":
        return {}
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return _ask("invalid_event", "Bash tool input is malformed; review the command manually.")
    command = tool_input.get("command")
    if not isinstance(command, str):
        return _ask("invalid_command", "the Bash command is missing or malformed; review it manually.")
    result: Classification | None = classify_command(command)
    if result is None:
        # Preserve Claude's normal permission flow; this hook never auto-allows.
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": result.decision.value,
            "permissionDecisionReason": f"Git Warp ({result.reason_code}): {result.message}",
        }
    }


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    if len(raw) > MAX_EVENT_BYTES:
        result = _ask("event_too_large", "the event exceeded Git Warp's input limit; review it manually.")
    else:
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            result = _ask("invalid_event", "the hook event could not be decoded; review the command manually.")
        else:
            try:
                result = output_for_event(event)
            except Exception:
                # Fail closed for an unexpected classifier fault without leaking
                # command text or environment data to the transcript.
                result = _ask("internal_error", "classification failed; review the command manually.")
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
