"""Phase 12: crash safety and concurrency.  Bounded, local, no network; every subprocess has a hard timeout.

Contracts:
* an interrupted state write (crash between temp-file write and atomic replace) never leaves a torn ``state.json`` /
  recorder and never blocks the next run;
* an interrupted recorder append (torn last line) is tolerated by every reader and does not poison later records;
* a corrupt / truncated / wrong-schema ``warp.db`` is never deleted: the CLI recovers or reports cleanly (valid JSON,
  no traceback) and the original bytes remain on disk;
* concurrent hooks lose no recorder line beyond the documented single-generation rotation, and every line parses;
* concurrent SessionStart / Stop / PostToolUse never traceback and ``state.json`` is always valid JSON;
* concurrent ``memory index`` runs all succeed and leave a consistent, intact database.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.acceptance import helpers as h

REC = "flight-recorder.jsonl"
MAX_BYTES = 5 * 1024 * 1024        # recorder rotation threshold (documented in recorder.py: keeps ONE previous generation)


# --------------------------------------------------------------------------- fixtures / helpers

@pytest.fixture
def repo(tmp_path):
    fx = h.build_state(tmp_path / "fx", "clean")
    return fx.repo


@pytest.fixture
def big_repo(tmp_path):
    fx = h.new_fixture(tmp_path / "big", "big")
    fx.g("init", "-q", "-b", "main")
    h.fast_import_history(fx.repo, 400)
    return fx.repo


def sdir(repo: Path) -> Path:
    return repo / ".git" / "git-warp"


def post_tool(repo, i=0, env=None, timeout=30):
    ev = h.hook_event("post_tool", repo, command=f"echo qa-marker-{i}")
    ev["session_id"] = f"sess-{i % 5}"
    return h.run_hook("post_tool", repo, event=ev, env=env, timeout=timeout)


def exercise(repo):
    post_tool(repo)
    h.run_hook("session_start", repo)
    h.run_hook("stop", repo)
    h.run_warp(repo, "memory", "index")


def recorder_lines(repo: Path, include_rotated=True):
    """(parsed records, list of unparseable raw lines) from the recorder file(s)."""
    good, bad = [], []
    names = ([REC + ".1"] if include_rotated else []) + [REC]
    for n in names:
        p = sdir(repo) / n
        if not p.exists():
            continue
        for ln in p.read_bytes().split(b"\n"):
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                (good if isinstance(obj, dict) else bad).append(obj if isinstance(obj, dict) else ln)
            except ValueError:
                bad.append(ln)
    return good, bad


def assert_state_json_ok(repo):
    p = sdir(repo) / "state.json"
    if p.exists():
        assert isinstance(json.loads(p.read_text()), dict)


def tree_bytes(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in d.rglob("*") if p.is_file()} if d.exists() else {}


CRASHER = textwrap.dedent("""
    import os, runpy, sys
    targets = set(sys.argv[2].split(","))
    real = os.replace
    def boom(src, dst, *a, **k):
        if os.path.basename(str(dst)) in targets:
            os._exit(137)          # simulated power loss: temp file written, replace never happens
        return real(src, dst, *a, **k)
    os.replace = boom
    sys.argv = [sys.argv[1]]
    runpy.run_path(sys.argv[0], run_name="__main__")
""")


def crashing_hook(repo, name, targets, event=None, timeout=30):
    payload = json.dumps(event or h.hook_event(name, repo))
    p = subprocess.run([sys.executable, "-c", CRASHER, str(h.SCRIPTS / f"hook_{name}.py"), ",".join(targets)], cwd=str(repo),
                       env=h.clean_env(), input=payload, capture_output=True, text=True, timeout=timeout)
    return p


# --------------------------------------------------------------------------- 1. interrupted state writes

@pytest.mark.parametrize("hook", ["session_start", "stop", "post_tool"])
def test_crash_before_state_replace_leaves_valid_state_and_recovers(repo, hook):
    """Crash between the temp write and os.replace of state.json: no torn file; the next run is normal."""
    exercise(repo)                                    # state.json exists with real content
    path = sdir(repo) / "state.json"
    old = path.read_text() if path.exists() else None
    ev = h.hook_event(hook, repo, command="echo crash") if hook == "post_tool" else None
    if hook == "post_tool":
        ev["session_id"] = "brand-new-session"        # forces a state.json update (last_session changes)
    crashing_hook(repo, hook, ["state.json"], event=ev)
    if path.exists():
        json.loads(path.read_text())                  # valid JSON (old or new), never torn
        if old is not None and hook == "post_tool":
            assert path.read_text() == old, "interrupted replace must leave the previous complete file"
    exercise(repo)                                    # recovery
    assert_state_json_ok(repo)
    r = h.run_warp(repo, "memory", "status")
    assert r.code == 0 and not r.traceback
    json.loads(r.stdout)


def test_crash_with_no_prior_state_file(repo):
    crashing_hook(repo, "session_start", ["state.json"])
    assert not (sdir(repo) / "state.json").exists() or json.loads((sdir(repo) / "state.json").read_text()) is not None
    r = h.run_hook("session_start", repo)
    assert r.code == 0 and not r.traceback
    assert_state_json_ok(repo)


def test_crash_during_recorder_rotation_loses_nothing(repo):
    """Crash exactly at the rotate step (replace of the recorder): the live recorder is intact and later rotates."""
    post_tool(repo, 0)
    rec = sdir(repo) / REC
    filler = (json.dumps({"ts": "2999-01-01T00:00:00.000Z", "tool": "Bash", "category": "shell", "command": "x" * 200}) + "\n").encode()
    n = (MAX_BYTES - rec.stat().st_size) // len(filler) + 2
    with open(rec, "ab") as fh:
        fh.write(filler * n)                          # now at the rotation threshold
    before_lines = len(recorder_lines(repo)[0])
    p = crashing_hook(repo, "post_tool", [REC], event=h.hook_event("post_tool", repo, command="echo during-crash"))
    good, bad = recorder_lines(repo)
    assert bad == [] and len(good) >= before_lines, "crash at rotation must not lose or tear recorder lines"
    assert not (sdir(repo) / (REC + ".1")).exists() or (sdir(repo) / (REC + ".1")).stat().st_size > 0
    post_tool(repo, 1)                                # recovery: rotation now succeeds
    good2, bad2 = recorder_lines(repo)
    assert bad2 == [] and len(good2) >= before_lines
    assert any("qa-marker-1" in json.dumps(r) for r in good2)


def test_crash_during_compaction_loses_nothing(repo):
    """Compaction rewrites via temp + replace; a crash there keeps the original recorder."""
    post_tool(repo, 0)
    rec = sdir(repo) / REC
    old = (json.dumps({"ts": "2001-01-01T00:00:00.000Z", "tool": "Bash", "category": "shell", "command": "ancient"}) + "\n").encode()
    rec.write_bytes(old * 3 + rec.read_bytes())
    st = sdir(repo) / "state.json"
    data = json.loads(st.read_text()) if st.exists() else {}
    data["last_compaction"] = 0                       # due now
    st.write_text(json.dumps(data))
    before = rec.read_bytes()
    crashing_hook(repo, "post_tool", [REC], event=h.hook_event("post_tool", repo, command="echo c"))
    after = rec.read_bytes()
    assert after.startswith(before) or after == before, "interrupted compaction must not truncate or tear the recorder"
    assert recorder_lines(repo)[1] == []


@pytest.mark.parametrize("seed", range(3))
def test_sigkill_storms_never_corrupt_state(repo, seed):
    """Kill hook processes with SIGKILL at random moments, repeatedly; afterwards every artifact is intact/readable."""
    rnd = random.Random(seed)
    exercise(repo)
    procs = []
    try:
        for i in range(24):
            name = ("post_tool", "session_start", "stop")[i % 3]
            payload = json.dumps(h.hook_event(name, repo, command=f"echo kill-{i}"))
            p = subprocess.Popen([sys.executable, str(h.SCRIPTS / f"hook_{name}.py")], cwd=str(repo), env=h.clean_env(),
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            p.stdin.write(payload.encode())
            p.stdin.close()
            procs.append(p)
            time.sleep(rnd.uniform(0, 0.06))
            if rnd.random() < 0.7:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):   # already exited (macOS reports EPERM for a zombie group)
                    pass
    finally:
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                p.kill()
    assert_state_json_ok(repo)
    good, bad = recorder_lines(repo)
    assert len(bad) <= 1, f"at most one torn (final) line is acceptable after kills, got {len(bad)}"
    # full recovery
    exercise(repo)
    r = h.run_warp(repo, "memory", "status")
    assert r.code == 0 and not r.traceback
    json.loads(r.stdout)
    assert (sdir(repo) / "warp.db").exists() or True
    con = sqlite3.connect(f"file:{sdir(repo) / 'warp.db'}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


# --------------------------------------------------------------------------- 2. interrupted recorder writes

@pytest.mark.parametrize("torn", [b'{"ts":"2030-01-01T00:00:00.000Z","tool":"Ba', b'{"ts":', b"{", b'{"a":1}\n{"b":', b"\xff\xfe\x00garbage"])
def test_partial_last_line_is_tolerated_by_readers(repo, torn):
    """A torn final line must not break any reader (memory CLI, SessionStart/Stop hooks)."""
    exercise(repo)
    rec = sdir(repo) / REC
    with open(rec, "ab") as fh:
        fh.write(torn)                                # no trailing newline: the process died mid-append
    for args in (("memory", "sessions"), ("memory", "status"), ("memory", "index")):
        r = h.run_warp(repo, *args)
        assert r.code == 0 and not r.traceback, (args, r.stderr[-300:])
        json.loads(r.stdout)
    for name in ("session_start", "stop"):
        r = h.run_hook(name, repo)
        assert r.code == 0 and not r.traceback


def test_record_written_after_a_torn_line_is_not_lost(repo):
    """Data-loss contract: the first record appended after a crash-torn line must still be a parseable record
    (a missing newline from the torn write must not glue it onto the garbage)."""
    exercise(repo)
    rec = sdir(repo) / REC
    with open(rec, "ab") as fh:
        fh.write(b'{"ts":"2030-01-01T00:00:00.000Z","tool":"Ba')
    post_tool(repo, 777)
    good, _bad = recorder_lines(repo)
    assert any("qa-marker-777" in json.dumps(r) for r in good), "post-crash record was swallowed by the torn line"


# --------------------------------------------------------------------------- 3. SQLite corruption

def _make_db(repo):
    r = h.run_warp(repo, "memory", "index")
    assert r.code == 0, r.stderr
    return sdir(repo) / "warp.db"


def _corrupt_garbage(db):
    db.write_bytes(b"this is definitely not sqlite " * 400)


def _corrupt_truncated(db):
    raw = db.read_bytes()
    db.write_bytes(raw[: max(100, len(raw) // 3)])


def _corrupt_zero(db):
    db.write_bytes(b"")


def _corrupt_header_only(db):
    db.write_bytes(db.read_bytes()[:100])


def _corrupt_future_schema(db):
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA user_version = 99")
    con.execute("CREATE TABLE IF NOT EXISTS from_the_future(x)")
    con.commit()
    con.close()


def _corrupt_foreign_db(db):
    db.unlink()
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, secret TEXT)")
    con.execute("INSERT INTO users(secret) VALUES ('precious user data')")
    con.commit()
    con.close()


def _corrupt_page_flip(db):
    raw = bytearray(db.read_bytes())
    for i in range(4096, min(len(raw), 4096 * 4), 7):
        raw[i] ^= 0xFF
    db.write_bytes(bytes(raw))


CORRUPTIONS = {"garbage": _corrupt_garbage, "truncated": _corrupt_truncated, "zero-length": _corrupt_zero,
               "header-only": _corrupt_header_only, "future-schema": _corrupt_future_schema,
               "foreign-db": _corrupt_foreign_db, "flipped-pages": _corrupt_page_flip}


@pytest.mark.parametrize("kind", CORRUPTIONS)
def test_corrupt_database_never_deleted_and_cli_recovers(big_repo, kind):
    repo = big_repo
    db = _make_db(repo)
    CORRUPTIONS[kind](db)
    original = db.read_bytes()
    r = h.run_warp(repo, "memory", "index")
    assert not r.traceback and not r.timed_out, r.stderr[-300:]
    data = json.loads(r.stdout)                       # a clean report or a clean recovery -- always valid JSON
    assert r.code in (0, 2)
    if original:                                      # zero-length carries no data to preserve
        survivors = [p for p in sdir(repo).iterdir() if p.is_file() and p.read_bytes() == original]
        assert survivors, f"{kind}: the original database bytes are gone (deleted or overwritten)"
    # every other command that touches the db also behaves
    for args in (("memory", "status"), ("memory", "hotspots"), ("memory", "churn"), ("memory", "index"), ("memory", "reverts")):
        rr = h.run_warp(repo, *args)
        assert not rr.traceback and not rr.timed_out
        json.loads(rr.stdout)
    final = h.run_warp(repo, "memory", "status")
    assert not json.loads(final.stdout).get("error"), "database did not recover to a usable state"
    assert isinstance(data, dict)


@pytest.mark.parametrize("kind", ["garbage", "truncated", "future-schema"])
def test_hooks_survive_corrupt_database(repo, kind):
    exercise(repo)
    CORRUPTIONS[kind](sdir(repo) / "warp.db")
    for name in ("session_start", "stop"):
        r = h.run_hook(name, repo)
        assert r.code == 0 and not r.traceback, r.stderr[-300:]
    assert post_tool(repo).code == 0
    assert_state_json_ok(repo)


def test_sidecar_garbage_does_not_destroy_database(repo):
    db = _make_db(repo)
    (sdir(repo) / "warp.db-wal").write_bytes(b"garbage wal" * 200)
    (sdir(repo) / "warp.db-journal").write_bytes(b"garbage journal" * 200)
    r = h.run_warp(repo, "memory", "status")
    assert not r.traceback
    json.loads(r.stdout)
    assert db.exists() or any(p.name.startswith("warp.db") for p in sdir(repo).iterdir())


def test_repeated_corruption_does_not_destroy_the_earlier_quarantined_copy(repo):
    """Two independent corruptions in a row: the first quarantined file must not be overwritten/unlinked by the second."""
    db = _make_db(repo)
    first = b"FIRST corruption payload " * 300
    db.write_bytes(first)
    h.run_warp(repo, "memory", "index")
    second = b"SECOND corruption payload " * 300
    db.write_bytes(second)
    h.run_warp(repo, "memory", "index")
    blobs = [p.read_bytes() for p in sdir(repo).iterdir() if p.is_file()]
    assert first in blobs and second in blobs, "an earlier corrupt database was deleted by a later recovery"


def test_unwritable_state_dir_degrades_without_traceback(repo):
    exercise(repo)
    sd = sdir(repo)
    os.chmod(sd, 0o500)
    try:
        for args in (("memory", "index"), ("memory", "status")):
            r = h.run_warp(repo, *args)
            assert not r.traceback and not r.timed_out
            json.loads(r.stdout)
        for name in ("session_start", "stop"):
            r = h.run_hook(name, repo)
            assert r.code == 0 and not r.traceback
        assert post_tool(repo).code == 0
    finally:
        os.chmod(sd, 0o700)


# --------------------------------------------------------------------------- 4. concurrent hook writes

@pytest.mark.parametrize("workers", [8, 16])
def test_concurrent_post_tool_hooks_lose_no_lines(repo, workers):
    """N parallel PostToolUse processes (x3 rounds): every JSONL line parses, none lost (no rotation at this size)."""
    post_tool(repo, 0)
    start = len(recorder_lines(repo)[0])
    total = workers * 3
    with ThreadPoolExecutor(max_workers=workers) as ex:
        runs = list(ex.map(lambda i: post_tool(repo, 1000 + i), range(total)))
    assert all(r.code == 0 and not r.traceback and not r.timed_out for r in runs)
    assert all(r.seconds < h.hook_timeouts()["post_tool"] for r in runs)
    good, bad = recorder_lines(repo)
    assert bad == [], f"{len(bad)} torn/unparseable recorder lines"
    blob = [json.dumps(r) for r in good]
    missing = [i for i in range(total) if not any(f"qa-marker-{1000 + i}" in b for b in blob)]
    assert missing == [], f"lost recorder lines for workers {missing}"
    assert len(good) == start + total
    assert_state_json_ok(repo)


def test_concurrent_hooks_across_rotation_lose_nothing(repo):
    """Documented rotation keeps exactly one previous generation: with the file at the threshold, 16 concurrent
    writers rotate it once and every record (old + new) is still present and parseable across both generations."""
    post_tool(repo, 0)
    rec = sdir(repo) / REC
    filler = (json.dumps({"ts": "2999-01-01T00:00:00.000Z", "tool": "Bash", "category": "shell", "command": "x" * 200}) + "\n").encode()
    n = (MAX_BYTES - rec.stat().st_size - 300) // len(filler)
    with open(rec, "ab") as fh:
        fh.write(filler * n)
    before = len(recorder_lines(repo)[0])
    with ThreadPoolExecutor(max_workers=16) as ex:
        runs = list(ex.map(lambda i: post_tool(repo, 2000 + i), range(16)))
    assert all(r.code == 0 and not r.traceback for r in runs)
    good, bad = recorder_lines(repo)
    assert bad == []
    assert len(good) == before + 16, f"expected {before + 16} records across generations, found {len(good)}"
    assert (sdir(repo) / (REC + ".1")).exists(), "rotation did not happen -- the test did not exercise it"


# --------------------------------------------------------------------------- 5. parallel PostToolUse + SessionStart/Stop

def test_parallel_post_tool_session_start_and_stop(repo):
    """Mixed simultaneous hooks, 4 rounds: no traceback, exit 0, within budgets; a reader hammering state.json never
    observes a torn file (atomic replace)."""
    exercise(repo)
    limits = h.hook_timeouts()
    stop_reading = threading.Event()
    torn = []

    def reader():
        p = sdir(repo) / "state.json"
        while not stop_reading.is_set():
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if raw:
                try:
                    json.loads(raw)
                except ValueError:
                    torn.append(raw[:80])
            else:
                torn.append(b"<empty>")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for rnd in range(4):
            jobs = ([("post_tool", i) for i in range(8)] + [("session_start", i) for i in range(4)] + [("stop", i) for i in range(4)])
            random.Random(rnd).shuffle(jobs)

            def go(job):
                name, i = job
                ev = h.hook_event(name, repo, command=f"echo par-{rnd}-{i}")
                ev["session_id"] = f"par-{rnd}-{i}"
                return name, h.run_hook(name, repo, event=ev)

            with ThreadPoolExecutor(max_workers=16) as ex:
                results = list(ex.map(go, jobs))
            for name, r in results:
                assert r.code == 0 and not r.traceback and not r.timed_out, (name, r.stderr[-300:])
                assert r.seconds < limits[name], f"{name} took {r.seconds:.1f}s (> hooks.json {limits[name]}s) under contention"
    finally:
        stop_reading.set()
        t.join(timeout=10)
    assert torn == [], f"state.json was observed torn/empty: {torn[:3]}"
    assert_state_json_ok(repo)
    good, bad = recorder_lines(repo)
    assert bad == []


# --------------------------------------------------------------------------- 6. concurrent memory index

@pytest.mark.parametrize("mix", ["plain", "with-rebuild"])
def test_concurrent_memory_index_runs(big_repo, mix):
    """8 simultaneous indexers started together on a FRESH state dir, 5 rounds (the first-creation race is the
    dangerous moment).  All must succeed; the database must be intact and complete; concurrency must never be
    mistaken for corruption (no quarantine)."""
    import shutil
    repo = big_repo
    n = 8
    for rnd in range(5):
        shutil.rmtree(sdir(repo), ignore_errors=True)
        argsets = [("memory", "index")] * n
        if mix == "with-rebuild":
            argsets = [("memory", "index", "--rebuild") if i % 3 == 0 else ("memory", "index") for i in range(n)]
        barrier = threading.Barrier(n)

        def go(a):
            barrier.wait(timeout=30)
            return h.run_warp(repo, *a, timeout=120)

        with ThreadPoolExecutor(max_workers=n) as ex:
            runs = list(ex.map(go, argsets))
        for r in runs:
            assert not r.timed_out and not r.traceback, r.stderr[-300:]
            assert r.code in (0, 2)
            assert isinstance(json.loads(r.stdout), dict)
        assert any(r.code == 0 for r in runs)
        quarantined = [p.name for p in sdir(repo).iterdir() if p.name.endswith(".corrupt")]
        assert quarantined == [], f"round {rnd}: concurrency was mistaken for corruption: {quarantined}"
        st = json.loads(h.run_warp(repo, "memory", "status").stdout)
        assert not st.get("error")
        con = sqlite3.connect(f"file:{sdir(repo) / 'warp.db'}?mode=ro", uri=True)
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert con.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 400
        finally:
            con.close()
    out = h.run_warp(repo, "memory", "hotspots")
    assert not out.traceback and not json.loads(out.stdout).get("error")


def test_concurrent_index_while_hooks_write(big_repo):
    repo = big_repo
    exercise(repo)

    def job(i):
        if i % 2:
            return h.run_warp(repo, "memory", "index", timeout=120)
        return post_tool(repo, 3000 + i)

    with ThreadPoolExecutor(max_workers=12) as ex:
        runs = list(ex.map(job, range(24)))
    assert all(not r.timed_out and not r.traceback for r in runs)
    assert recorder_lines(repo)[1] == []
    assert_state_json_ok(repo)
