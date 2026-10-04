"""Shared plumbing for the non-guard hooks (SessionStart, PostToolUse, Stop).

* ``read_event``: bounded stdin read.  A payload over ``MAX_PAYLOAD_BYTES`` is discarded (drained in small chunks
  with a time cap so the host never blocks on a full pipe) and treated as "no event".
* ``deadline``: wall-clock budget enforced with ``setitimer``; raises ``HookDeadline`` (a BaseException, so a broad
  ``except Exception`` inside the work cannot swallow it).  Hooks must finish well inside their hooks.json timeout.
"""
from __future__ import annotations

import json
import signal
import sys
import time
from contextlib import contextmanager

MAX_PAYLOAD_BYTES = 1024 * 1024        # real hook events are a few KiB; anything bigger is not worth parsing
_DRAIN_CAP_BYTES = 64 * 1024 * 1024
_DRAIN_CAP_S = 2.0


class HookDeadline(BaseException):
    """The hook's own time budget ran out."""


def read_event(stream=None, limit: int = MAX_PAYLOAD_BYTES) -> dict:
    """Parse hook stdin into a dict.  Malformed, empty, non-object or oversized input -> ``{}`` (fail open)."""
    try:
        stream = stream or getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read(limit + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        if len(raw) > limit:
            _drain(stream)
            return {}
        data = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
    except (ValueError, OSError, RecursionError, MemoryError):
        return {}
    return data if isinstance(data, dict) else {}


def _drain(stream) -> None:
    end, seen = time.monotonic() + _DRAIN_CAP_S, 0
    try:
        while seen < _DRAIN_CAP_BYTES and time.monotonic() < end:
            chunk = stream.read(65536)
            if not chunk:
                return
            seen += len(chunk)
    except (OSError, ValueError):
        pass


def cwd_of(event: dict):
    """The event's cwd when it is a non-empty string without NUL bytes, else None."""
    c = event.get("cwd")
    return c if isinstance(c, str) and c and "\0" not in c else None


@contextmanager
def deadline(seconds: float):
    """Raise ``HookDeadline`` in the main thread after ``seconds`` (no-op where setitimer is unavailable)."""
    def _fire(signum, frame):
        raise HookDeadline()
    armed = False
    try:
        signal.signal(signal.SIGALRM, _fire)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        armed = True
    except (AttributeError, ValueError, OSError):
        pass
    try:
        yield
    finally:
        if armed:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
            except (ValueError, OSError):
                pass


def emit(obj: dict) -> None:
    """Write one hook JSON object to stdout and flush (so it survives a later deadline)."""
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()
