"""Crash and concurrency safety of the storage layer (bounded: a few seconds)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

from gitwarp.core import storage
from gitwarp.memory import state
from tests.conftest import SCRIPTS


@pytest.fixture
def sd(tmp_path):
    return storage.state_dir(tmp_path, create=True)


def test_interrupted_state_write_keeps_old_content_and_leaves_no_temp(sd, monkeypatch):
    state.update(sd, keep="old")
    before = (sd / "state.json").read_bytes()

    def boom(*a, **k):
        raise KeyboardInterrupt("killed between temp write and replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        state.update(sd, keep="new")
    monkeypatch.undo()
    assert (sd / "state.json").read_bytes() == before
    assert json.loads(before)["keep"] == "old"
    assert [p.name for p in sd.iterdir() if p.name.endswith(".tmp")] == []


def test_interrupted_write_midway_never_truncates_target(sd, monkeypatch):
    storage.write_atomic(sd, "state.json", b'{"ok": 1}')
    real = os.write
    calls = []

    def flaky(fd, data):
        calls.append(1)
        if len(calls) == 1:
            real(fd, bytes(data)[:3])           # partial write, then the process "dies"
            raise OSError("disk died")
        return real(fd, data)

    monkeypatch.setattr(os, "write", flaky)
    with pytest.raises(OSError):
        storage.write_atomic(sd, "state.json", b'{"ok": 2, "pad": "' + b"x" * 1000 + b'"}')
    monkeypatch.undo()
    assert storage.read_bytes(sd, "state.json") == b'{"ok": 1}'
    assert [p.name for p in sd.iterdir() if p.name.endswith(".tmp")] == []


def test_leftover_temp_from_a_killed_writer_does_not_break_later_writes(sd):
    (sd / ".state.json.999.deadbeef.tmp").write_text("partial")
    assert state.update(sd, a=1) is True and state.read(sd)["a"] == 1


def test_interrupted_recorder_append_leaves_only_whole_lines(sd, monkeypatch):
    storage.append_line(sd, "log", b'{"a":1}\n', 10_000)
    real = os.write

    def die(fd, data):
        raise OSError("killed")

    monkeypatch.setattr(os, "write", die)
    with pytest.raises(OSError):
        storage.append_line(sd, "log", b'{"a":2}\n', 10_000)
    monkeypatch.undo()
    storage.append_line(sd, "log", b'{"a":3}\n', 10_000)
    assert [json.loads(x)["a"] for x in (sd / "log").read_bytes().splitlines()] == [1, 3]
    assert real is os.write


def test_concurrent_threads_append_whole_lines_without_loss(sd):
    n_threads, per = 8, 60

    errors = []

    def work(t):
        try:
            for i in range(per):
                storage.append_line(sd, "log", (json.dumps({"t": t, "i": i, "pad": "p" * 200}) + "\n").encode(), 10 ** 9)
        except BaseException as e:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(repr(e))

    ts = [threading.Thread(target=work, args=(t,)) for t in range(n_threads)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert errors == []
    rows = [json.loads(x) for x in (sd / "log").read_bytes().splitlines()]
    assert len(rows) == n_threads * per
    assert {(r["t"], r["i"]) for r in rows} == {(t, i) for t in range(n_threads) for i in range(per)}


_APPENDER = """
import sys, json
sys.path.insert(0, {scripts!r})
from gitwarp.core import storage
from pathlib import Path
sd = Path(sys.argv[1]); w = int(sys.argv[2])
for i in range(40):
    storage.append_line(sd, "log", (json.dumps({{"w": w, "i": i}}) + "\\n").encode(), int(sys.argv[3]))
"""


def _spawn(sd, code, *args):
    return subprocess.Popen([sys.executable, "-c", code.format(scripts=str(SCRIPTS)), str(sd), *map(str, args)])


def test_concurrent_processes_append_valid_lines_no_loss(sd):
    ps = [_spawn(sd, _APPENDER, w, 10 ** 9) for w in range(8)]
    assert all(p.wait(60) == 0 for p in ps)
    rows = [json.loads(x) for x in (sd / "log").read_bytes().splitlines()]
    assert len(rows) == 8 * 40


def test_concurrent_appends_with_rotation_stay_valid(sd):
    """Rotation keeps one generation: older lines may be dropped (documented) but every surviving line is valid JSON."""
    ps = [_spawn(sd, _APPENDER, w, 2000) for w in range(6)]
    assert all(p.wait(60) == 0 for p in ps)
    for name in ("log", "log.1"):
        for ln in (sd / name).read_bytes().splitlines():
            json.loads(ln)
    assert not (sd / "log.2").exists()
    assert {oct(os.stat(sd / n).st_mode & 0o777) for n in ("log", "log.1")} == {"0o600"}


_UPDATER = """
import sys
sys.path.insert(0, {scripts!r})
from pathlib import Path
from gitwarp.memory import state
sd = Path(sys.argv[1]); w = int(sys.argv[2])
for i in range(25):
    assert state.update(sd, **{{f"k{{w}}": i}})
"""


def test_simultaneous_state_updates_do_not_lose_keys(sd):
    """SessionStart and Stop (and others) updating different keys of state.json at once: every key survives."""
    ps = [_spawn(sd, _UPDATER, w) for w in range(8)]
    assert all(p.wait(60) == 0 for p in ps)
    data = state.read(sd)
    assert {f"k{w}" for w in range(8)} <= set(data) and all(data[f"k{w}"] == 24 for w in range(8))
    json.loads((sd / "state.json").read_text())


def test_parallel_post_tool_hooks_record_every_event(repo):
    ev = {"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(repo.path), "tool_name": "Bash",
          "tool_input": {"command": "ls"}, "tool_response": {}}
    ps = [subprocess.Popen([sys.executable, str(SCRIPTS / "hook_post_tool.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, cwd=str(repo.path), text=True) for _ in range(8)]
    for p in ps:
        p.stdin.write(json.dumps(ev))
        p.stdin.close()
    for p in ps:
        assert p.wait(60) == 0
        assert p.stdout.read() in ("", "{}") and p.stderr.read() == ""
    sd = repo.path / ".git" / "git-warp"
    lines = (sd / "flight-recorder.jsonl").read_bytes().splitlines()
    assert len(lines) == 8 and all(json.loads(x)["tool"]["name"] == "Bash" for x in lines)
    assert oct(os.stat(sd / "flight-recorder.jsonl").st_mode & 0o777) == "0o600"


def test_session_start_and_stop_concurrently_keep_state_valid(repo):
    repo.write("dirty.txt", "d\n")
    ev = {"cwd": str(repo.path), "session_id": "s"}
    procs = []
    for _ in range(3):
        for name, extra in (("session_start", {"hook_event_name": "SessionStart", "source": "startup"}),
                            ("stop", {"hook_event_name": "Stop", "stop_hook_active": False})):
            p = subprocess.Popen([sys.executable, str(SCRIPTS / f"hook_{name}.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, cwd=str(repo.path), text=True)
            p.stdin.write(json.dumps({**ev, **extra}))
            p.stdin.close()
            procs.append(p)
    for p in procs:
        assert p.wait(120) == 0
        p.stdout.read(), p.stderr.read()
    sd = repo.path / ".git" / "git-warp"
    st = json.loads((sd / "state.json").read_text())
    assert st["schema"] == 1
    assert [p.name for p in sd.iterdir() if p.name.endswith(".tmp")] == []
