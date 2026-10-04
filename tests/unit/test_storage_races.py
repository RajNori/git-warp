"""Torn recorder lines, first-creation index races and repeated corruption (storage-owned regression tests)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

from gitwarp.core import storage
from gitwarp.memory import index
from tests.conftest import SCRIPTS


def test_append_after_torn_line_isolates_the_fragment(tmp_path):
    sd = storage.state_dir(tmp_path, create=True)
    storage.append_line(sd, "log", b'{"a":1}\n', 10_000)
    with open(sd / "log", "ab") as fh:
        fh.write(b'{"ts":"2030","tool":"Ba')
    storage.append_line(sd, "log", b'{"a":2}\n', 10_000)
    storage.append_line(sd, "log", b'{"a":3}\n', 10_000)
    good = []
    for ln in (sd / "log").read_bytes().splitlines():
        try:
            good.append(json.loads(ln)["a"])
        except (ValueError, KeyError):
            pass
    assert good == [1, 2, 3]
    assert oct(os.stat(sd / "log").st_mode & 0o777) == "0o600"


_INDEXER = """
import sys, json, time
sys.path.insert(0, {scripts!r})
from gitwarp.memory import index
go = float(sys.argv[2])
while time.time() < go:
    time.sleep(0.001)
res = index.ensure_indexed(sys.argv[1])
print(json.dumps({{"mode": res.get("mode"), "persisted": res.get("persisted"), "warnings": res.get("warnings"), "total": res.get("total_commits")}}))
"""


def test_simultaneous_first_creation_is_never_mistaken_for_corruption(make_repo):
    import time
    for _ in range(4):
        r = make_repo().seed(6)
        sd = r.path / ".git" / "git-warp"
        go = time.time() + 1.0
        ps = [subprocess.Popen([sys.executable, "-c", _INDEXER.format(scripts=str(SCRIPTS)), str(r.path), str(go)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
        outs = [(p.communicate(timeout=120), p.returncode) for p in ps]
        assert all(rc == 0 for _, rc in outs), outs
        assert [p.name for p in sd.iterdir() if ".corrupt" in p.name] == []
        con = sqlite3.connect(f"file:{sd / 'warp.db'}?mode=ro", uri=True)
        assert con.execute("select count(*) from commits").fetchone()[0] == 6
        con.close()


def test_busy_or_uninitialised_errors_are_not_corruption():
    for m in ("database is locked", "table commits already exists", "unable to open database file", "database schema is locked"):
        assert not index._is_corruption(sqlite3.OperationalError(m))
    for m in ("database disk image is malformed", "file is not a database"):
        assert index._is_corruption(sqlite3.DatabaseError(m))


def test_second_corruption_keeps_the_first_quarantined_copy(make_repo):
    r = make_repo().seed(2)
    sd = r.path / ".git" / "git-warp"
    index.ensure_indexed(r.path)
    (sd / "warp.db").write_bytes(b"first-corruption" * 50)
    index.ensure_indexed(r.path)
    (sd / "warp.db").write_bytes(b"second-corruption" * 50)
    index.ensure_indexed(r.path)
    kept = sorted(p.read_bytes()[:10] for p in sd.iterdir() if ".corrupt" in p.name)
    assert kept == [b"first-corr", b"second-cor"]
