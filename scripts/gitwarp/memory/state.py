"""Small non-sensitive ``state.json`` kept next to the DB (under the common git dir only)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

STATE_FILE = "state.json"
STATE_SCHEMA = 1


def read(state_dir) -> dict:
    try:
        data = json.loads((Path(state_dir) / STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update(state_dir, **kv) -> bool:
    """Merge ``kv`` into state.json atomically (best-effort; never raises)."""
    d = Path(state_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
        data = read(d)
        data.update(kv)
        data["schema"] = STATE_SCHEMA
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=str(d))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, sort_keys=True)
            os.replace(tmp, d / STATE_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except (OSError, ValueError, TypeError):
        return False
