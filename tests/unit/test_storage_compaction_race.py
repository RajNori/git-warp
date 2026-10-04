"""GW-STATE-01: no append may be lost to compaction / rotation (lock held through the atomic replace)."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

from gitwarp.core import storage
from tests.conftest import SCRIPTS


def rec(i, pad=0):
    return (json.dumps({"id": i, "pad": "p" * pad}) + "\n").encode()


def ids(sd):
    out = []
    for n in ("log.1", "log"):
        p = sd / n
        if p.exists():
            out += [json.loads(x)["id"] for x in p.read_bytes().splitlines() if x.strip()]
    return out


def test_append_during_compaction_is_blocked_then_lands_after_replace(tmp_path):
    """Deterministic: an append is started from inside the transform (i.e. after the snapshot, before the replace)."""
    sd = storage.state_dir(tmp_path, create=True)
    storage.append_line(sd, "log", rec(1), 10 ** 9)
    storage.append_line(sd, "log", rec(2), 10 ** 9)
    state = {}

    def transform(data):
        t = threading.Thread(target=lambda: storage.append_line(sd, "log", rec(99), 10 ** 9))
        t.start()
        t.join(0.5)
        state["blocked"] = t.is_alive()       # must still be waiting for the lock: it cannot slip in before the replace
        state["thread"] = t
        return b"".join(x + b"\n" for x in data.splitlines() if json.loads(x)["id"] != 1)   # drop record 1

    storage.rewrite_locked(sd, "log", transform)
    state["thread"].join(10)
    assert state["blocked"] is True
    assert ids(sd) == [2, 99]


def test_append_during_rotation_and_compaction_keeps_everything(tmp_path):
    sd = storage.state_dir(tmp_path, create=True)
    for i in range(5):
        storage.append_line(sd, "log", rec(i, 30), 10 ** 9)
    ts = []

    def transform(data):
        for i in range(100, 104):
            t = threading.Thread(target=storage.append_line, args=(sd, "log", rec(i, 30), 10 ** 9))
            t.start()
            ts.append(t)
        time.sleep(0.2)
        return data

    storage.rewrite_locked(sd, "log", transform)
    [t.join(10) for t in ts]
    assert sorted(ids(sd)) == [0, 1, 2, 3, 4, 100, 101, 102, 103]


_WORKER = """
import sys, json
sys.path.insert(0, {scripts!r})
from pathlib import Path
from gitwarp.core import storage
sd, w, n, mx = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
for i in range(n):
    storage.append_line(sd, "log", (json.dumps({{"id": w * 10000 + i}}) + "\\n").encode(), mx)
"""

_COMPACTOR = """
import sys, time
sys.path.insert(0, {scripts!r})
from pathlib import Path
from gitwarp.core import storage
sd = Path(sys.argv[1]); stop = time.time() + float(sys.argv[2])
while time.time() < stop:
    storage.rewrite_locked(sd, "log", lambda b: b + b"")     # identity rewrite: every record must survive
"""


def _spawn(code, *args):
    return subprocess.Popen([sys.executable, "-c", code.format(scripts=str(SCRIPTS)), *map(str, args)])


def test_multiprocess_appenders_with_repeated_compactions_lose_nothing(tmp_path):
    sd = storage.state_dir(tmp_path, create=True)
    workers = [_spawn(_WORKER, sd, w, 150, 10 ** 9) for w in range(8)]
    compactors = [_spawn(_COMPACTOR, sd, 4) for _ in range(2)]
    assert all(p.wait(120) == 0 for p in workers)
    assert all(p.wait(120) == 0 for p in compactors)
    got = ids(sd)
    assert sorted(got) == sorted(w * 10000 + i for w in range(8) for i in range(150))     # every record exactly once


def test_multiprocess_rotation_and_compaction_never_corrupt(tmp_path):
    """With rotation (documented: older generations may be dropped) every surviving line is whole and unique."""
    sd = storage.state_dir(tmp_path, create=True)
    workers = [_spawn(_WORKER, sd, w, 150, 6000) for w in range(6)]
    compactors = [_spawn(_COMPACTOR, sd, 3)]
    assert all(p.wait(120) == 0 for p in workers + compactors)
    got = ids(sd)
    assert len(got) == len(set(got)) and got
    assert not (sd / "log.2").exists()
