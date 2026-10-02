"""Metadata-only PostToolUse recorder adapter."""

from __future__ import annotations

from typing import Any

from ..memory.recorder import record_event


def handle(event: dict[str, Any]) -> dict[str, Any]:
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        record_event(cwd, event)
    return {}
