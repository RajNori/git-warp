"""Small, bounded protocol helpers for Claude command hooks."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

MAX_EVENT_BYTES = 1024 * 1024


def read_event(stream: TextIO | None = None) -> dict[str, Any] | None:
    source = stream or sys.stdin
    raw = source.read(MAX_EVENT_BYTES + 1)
    if len(raw.encode("utf-8", errors="replace")) > MAX_EVENT_BYTES:
        return None
    if not raw.strip():
        return {}
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return None
    return event if isinstance(event, dict) else None


def write_json(value: dict[str, Any], stream: TextIO | None = None) -> None:
    (stream or sys.stdout).write(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")


def event_cwd(event: dict[str, Any]) -> str | None:
    value = event.get("cwd")
    return value if isinstance(value, str) and value else None
