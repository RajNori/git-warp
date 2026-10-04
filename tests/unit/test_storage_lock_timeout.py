"""A lock that cannot be taken refuses the operation (explicit drop) instead of proceeding unlocked."""
from __future__ import annotations

import subprocess
import sys

import pytest

from gitwarp.core import storage
from gitwarp.memory import state
from tests.conftest import SCRIPTS

_HOLDER = """
import sys, fcntl, os
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
sys.stdin.readline()
"""


@pytest.fixture
def held(tmp_path, monkeypatch):
    """Factory: hold <name>.lock from a second process until released."""
    monkeypatch.setattr(storage, "LOCK_WAIT", 0.3)
    sd = storage.state_dir(tmp_path, create=True)
    procs = []

    def hold(name):
        p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(sd / (name + ".lock"))], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        assert p.stdout.readline().strip() == "held"
        procs.append(p)
        return p

    def release(p):
        p.stdin.write("\n")
        p.stdin.close()
        p.wait(10)

    yield sd, hold, release
    for p in procs:
        if p.poll() is None:
            p.kill()


def test_append_is_refused_not_written_unlocked_and_nothing_is_lost(held):
    sd, hold, release = held
    storage.append_line(sd, "log", b'{"id":1}\n', 10 ** 9)
    before = (sd / "log").read_bytes()
    p = hold("log")
    with pytest.raises(storage.LockTimeout):
        storage.append_line(sd, "log", b'{"id":2}\n', 10 ** 9)
    with pytest.raises(storage.LockTimeout):          # rotation path is refused too
        storage.append_line(sd, "log", b'{"id":3}\n', 5)
    with pytest.raises(storage.LockTimeout):
        storage.rewrite_locked(sd, "log", lambda b: b"")
    assert (sd / "log").read_bytes() == before and not (sd / "log.1").exists()
    release(p)
    storage.append_line(sd, "log", b'{"id":4}\n', 10 ** 9)           # normal append works after release
    assert (sd / "log").read_bytes() == before + b'{"id":4}\n'


def test_update_locked_is_refused_and_state_is_intact(held):
    sd, hold, release = held
    assert state.update(sd, keep="old") is True
    before = (sd / "state.json").read_bytes()
    p = hold("state.json")
    assert state.update(sd, keep="new") is False                     # state.update swallows the refusal
    with pytest.raises(storage.LockTimeout):
        storage.update_locked(sd, "state.json", lambda cur: b"{}")
    assert (sd / "state.json").read_bytes() == before
    release(p)
    assert state.update(sd, keep="new") is True and state.read(sd)["keep"] == "new"


def test_recorder_drops_the_record_when_locked_and_never_raises(repo, monkeypatch):
    from gitwarp.memory import recorder
    monkeypatch.setattr(storage, "LOCK_WAIT", 0.2)
    sd = repo.path / ".git" / "git-warp"
    ev = {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert recorder.record(repo.path, ev) is True
    before = (sd / "flight-recorder.jsonl").read_bytes()
    p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(sd / "flight-recorder.jsonl.lock")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        assert recorder.record(repo.path, ev) is False
        assert (sd / "flight-recorder.jsonl").read_bytes() == before
    finally:
        p.stdin.write("\n")
        p.stdin.close()
        p.wait(10)
    assert recorder.record(repo.path, ev) is True


def test_file_lock_refuses_on_timeout(held):
    sd, hold, release = held
    p = hold("warp.db")
    with pytest.raises(storage.LockTimeout):
        with storage.file_lock(sd, "warp.db", timeout=0.2):
            pytest.fail("entered the critical section without the lock")
    release(p)
    with storage.file_lock(sd, "warp.db", timeout=1):
        pass
