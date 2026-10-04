"""Small non-sensitive ``state.json`` kept next to the DB (under the common git dir only), via core/storage."""
from __future__ import annotations

import json
from pathlib import Path

from ..core import storage
from ..core.redact import redact

STATE_FILE = "state.json"
STATE_SCHEMA = 1


def _parse(raw) -> dict:
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read(state_dir) -> dict:
    try:
        return _parse(storage.read_bytes(Path(state_dir), STATE_FILE, max_bytes=1 << 20))
    except OSError:
        return {}


def update(state_dir, **kv) -> bool:
    """Merge ``kv`` into state.json atomically and serialized across processes (best-effort; never raises)."""
    clean = {k: (redact(v) if isinstance(v, str) else v) for k, v in kv.items()}

    def merge(raw):
        data = _parse(raw)
        data.update(clean)
        data["schema"] = STATE_SCHEMA
        return json.dumps(data, sort_keys=True).encode("utf-8")

    try:
        storage.update_locked(Path(state_dir), STATE_FILE, merge)
        return True
    except (OSError, ValueError, TypeError):
        return False
