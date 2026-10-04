"""core/storage: the single secure-storage abstraction (modes, refusals, atomic/append writes, quarantine)."""
from __future__ import annotations

import os
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from gitwarp.core import storage
from gitwarp.memory import state


@pytest.fixture(autouse=True)
def _umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


@pytest.fixture
def sd(tmp_path):
    return storage.state_dir(tmp_path, create=True)


def _fifo(p):
    os.mkfifo(p)


def _sock(p):
    short = Path(tempfile.mkdtemp(prefix="gw", dir="/tmp"))
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(short / "s"))
    os.rename(short / "s", p)
    s.close()
    short.rmdir()


def _dir(p):
    os.mkdir(p)
    (Path(p) / "keep").write_text("k")


UNSAFE = {"fifo": _fifo, "socket": _sock, "dir": _dir}


def test_state_dir_is_private_and_lives_under_the_given_common_dir(tmp_path):
    d = storage.state_dir(tmp_path, create=True)
    assert d == tmp_path / "git-warp" and mode(d) == 0o700


def test_state_dir_without_create_touches_nothing(tmp_path):
    assert not storage.state_dir(tmp_path).exists()


def test_loose_directory_and_files_are_tightened_on_use(tmp_path):
    d = tmp_path / "git-warp"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    for n in ("warp.db", "flight-recorder.jsonl", "state.json", "warp.db-wal", "stray.tmp"):
        (d / n).write_text("x")
        os.chmod(d / n, 0o644)
    storage._SWEPT.clear()
    storage.ensure_dir(d)
    assert mode(d) == 0o700
    assert {mode(p) for p in d.iterdir()} == {0o600}


def test_stricter_modes_are_never_widened(tmp_path):
    d = tmp_path / "git-warp"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        storage.ensure_dir(d)
        assert mode(d) == 0o500
    finally:
        os.chmod(d, 0o700)


def test_umask_cannot_loosen_new_objects(tmp_path):
    os.umask(0)
    d = storage.state_dir(tmp_path, create=True)
    storage.write_atomic(d, "state.json", b"{}")
    storage.append_line(d, "log", b"x\n", 100)
    assert mode(d) == 0o700 and mode(d / "state.json") == 0o600 and mode(d / "log") == 0o600
    assert not [p for p in d.iterdir() if p.name.endswith(".tmp")]


def test_symlinked_directory_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, tmp_path / "git-warp")
    with pytest.raises(storage.UnsafeStorage):
        storage.state_dir(tmp_path, create=True)
    with pytest.raises(storage.UnsafeStorage):
        storage.write_atomic(tmp_path / "git-warp", "state.json", b"{}")
    with pytest.raises(storage.UnsafeStorage):
        storage.read_bytes(tmp_path / "git-warp", "state.json")
    assert list(outside.iterdir()) == []


def test_file_in_place_of_directory_is_refused(tmp_path):
    (tmp_path / "git-warp").write_text("not a dir")
    with pytest.raises(storage.UnsafeStorage):
        storage.state_dir(tmp_path, create=True)
    assert (tmp_path / "git-warp").read_text() == "not a dir"


def test_unsafe_storage_is_an_oserror():
    assert issubclass(storage.UnsafeStorage, OSError)


def test_symlinked_file_is_never_followed(sd, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL")
    for name in ("state.json", "flight-recorder.jsonl", "warp.db"):
        os.symlink(victim, sd / name)
    with pytest.raises(storage.UnsafeStorage):
        storage.write_atomic(sd, "state.json", b"{}")
    with pytest.raises(storage.UnsafeStorage):
        storage.append_line(sd, "flight-recorder.jsonl", b"x\n", 100)
    with pytest.raises(storage.UnsafeStorage):
        storage.read_bytes(sd, "state.json")
    with pytest.raises(storage.UnsafeStorage):
        storage.prepare_file(sd, "warp.db")
    with pytest.raises(storage.UnsafeStorage):
        storage.quarantine(sd, "warp.db")
    assert victim.read_text() == "ORIGINAL"
    assert all(os.path.islink(sd / n) for n in ("state.json", "flight-recorder.jsonl", "warp.db"))


@pytest.mark.parametrize("kind", sorted(UNSAFE))
def test_unexpected_object_is_refused_not_replaced_not_blocked(sd, kind):
    p = sd / "state.json"
    UNSAFE[kind](p)
    before = os.lstat(p).st_mode
    for op in (lambda: storage.write_atomic(sd, "state.json", b"{}"),
               lambda: storage.append_line(sd, "state.json", b"x\n", 100),
               lambda: storage.read_bytes(sd, "state.json"),
               lambda: storage.prepare_file(sd, "state.json"),
               lambda: storage.quarantine(sd, "state.json"),
               lambda: storage.unlink_regular(sd, "state.json"),
               lambda: storage.rewrite_locked(sd, "state.json", lambda b: b"")):
        with pytest.raises(storage.UnsafeStorage):
            op()
    assert os.lstat(p).st_mode == before
    if kind == "dir":
        assert (p / "keep").read_text() == "k"
    assert state.update(sd, a=1) is False and state.read(sd) == {}


def test_rotation_refuses_an_unexpected_rotated_target(sd):
    storage.append_line(sd, "log", b"a" * 50 + b"\n", 60)
    _fifo(sd / "log.1")
    with pytest.raises(storage.UnsafeStorage):
        storage.append_line(sd, "log", b"b" * 50 + b"\n", 60)
    assert stat.S_ISFIFO(os.lstat(sd / "log.1").st_mode)
    assert (sd / "log").read_bytes() == b"a" * 50 + b"\n"


def test_replace_between_check_and_use_is_refused(sd, monkeypatch):
    """A target swapped for a FIFO after the first check is caught by the re-check before os.replace."""
    storage.write_atomic(sd, "state.json", b'{"a":1}')
    real = os.fsync
    swapped = []

    def swap_then_fsync(fd):
        if not swapped:
            swapped.append(1)
            os.unlink(sd / "state.json")
            os.mkfifo(sd / "state.json")
        return real(fd)

    monkeypatch.setattr(os, "fsync", swap_then_fsync)
    with pytest.raises(storage.UnsafeStorage):
        storage.write_atomic(sd, "state.json", b'{"a":2}')
    assert stat.S_ISFIFO(os.lstat(sd / "state.json").st_mode)
    assert not [p for p in sd.iterdir() if p.name.endswith(".tmp")]


def test_atomic_write_roundtrip_mode_and_no_temp_left(sd):
    storage.write_atomic(sd, "state.json", b"one")
    storage.write_atomic(sd, "state.json", b"two")
    assert storage.read_bytes(sd, "state.json") == b"two"
    assert mode(sd / "state.json") == 0o600
    assert [p.name for p in sd.iterdir()] == ["state.json"]


def test_atomic_write_tightens_a_loose_existing_file(sd):
    (sd / "state.json").write_text("{}")
    os.chmod(sd / "state.json", 0o644)
    storage.write_atomic(sd, "state.json", b"{}")
    assert mode(sd / "state.json") == 0o600


def test_read_missing_is_none_and_size_is_bounded(sd):
    assert storage.read_bytes(sd, "nope") is None
    storage.write_atomic(sd, "big", b"x" * 100)
    with pytest.raises(storage.UnsafeStorage):
        storage.read_bytes(sd, "big", max_bytes=10)


def test_append_preserves_lines_and_rotates_to_one_generation(sd):
    for i in range(10):
        storage.append_line(sd, "log", (f"{i}" * 20 + "\n").encode(), 100)
    assert mode(sd / "log") == 0o600 and mode(sd / "log.1") == 0o600
    data = (sd / "log.1").read_bytes() + (sd / "log").read_bytes()
    assert all(len(ln) == 20 for ln in data.splitlines())
    assert not (sd / "log.2").exists()
    assert (sd / "log").stat().st_size <= 100


def test_oversized_line_is_rejected(sd):
    with pytest.raises(ValueError):
        storage.append_line(sd, "log", b"x" * (storage.MAX_LINE_BYTES + 1), 10 ** 9)


def test_prepare_file_creates_private_and_tightens_sidecars(sd):
    (sd / "warp.db-wal").write_text("w")
    os.chmod(sd / "warp.db-wal", 0o644)
    p = storage.prepare_file(sd, "warp.db")
    assert p == sd / "warp.db" and mode(p) == 0o600 and mode(sd / "warp.db-wal") == 0o600
    os.chmod(p, 0o666)
    storage.prepare_file(sd, "warp.db")
    assert mode(p) == 0o600


def test_prepare_file_refuses_unsafe_sidecar(sd, tmp_path):
    os.symlink(tmp_path / "x", sd / "warp.db-journal")
    with pytest.raises(storage.UnsafeStorage):
        storage.prepare_file(sd, "warp.db")


def test_sqlite_sidecars_are_private(sd):
    import sqlite3
    storage.prepare_file(sd, "warp.db")
    con = sqlite3.connect(str(sd / "warp.db"))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("create table t(x)")
    con.execute("insert into t values (1)")
    con.commit()
    storage.tighten_sidecars(sd, "warp.db")
    names = {p.name for p in sd.iterdir()}
    assert {"warp.db", "warp.db-wal", "warp.db-shm"} <= names
    assert {mode(p) for p in sd.iterdir()} == {0o600}
    con.close()


def test_quarantine_moves_private_and_rotates_bounded(sd):
    for i in range(5):
        (sd / "warp.db").write_bytes(f"gen{i}".encode())
        (sd / "warp.db-wal").write_bytes(f"wal{i}".encode())
        os.chmod(sd / "warp.db", 0o644)
        assert storage.quarantine(sd, "warp.db") == "warp.db.corrupt"
        assert not (sd / "warp.db").exists() and not (sd / "warp.db-wal").exists()
    kept = sorted(p.name for p in sd.iterdir() if p.name.startswith("warp.db.corrupt"))
    assert kept == ["warp.db.corrupt", "warp.db.corrupt.1", "warp.db.corrupt.2"]
    assert (sd / "warp.db.corrupt").read_bytes() == b"gen4"          # newest first
    assert (sd / "warp.db.corrupt.1").read_bytes() == b"gen3"
    assert (sd / "warp.db.corrupt.2").read_bytes() == b"gen2"
    assert {mode(p) for p in sd.iterdir()} == {0o600}


def test_quarantine_of_missing_is_noop(sd):
    assert storage.quarantine(sd, "warp.db") is None


def test_quarantine_never_unlinks_an_unexpected_previous_generation(sd):
    (sd / "warp.db").write_bytes(b"bad")
    _fifo(sd / "warp.db.corrupt")
    with pytest.raises(storage.UnsafeStorage):
        storage.quarantine(sd, "warp.db")
    assert (sd / "warp.db").read_bytes() == b"bad"
    assert stat.S_ISFIFO(os.lstat(sd / "warp.db.corrupt").st_mode)


def test_unlink_regular_only_removes_regular_files(sd, tmp_path):
    (sd / "a").write_text("a")
    assert storage.unlink_regular(sd, "a") is True
    assert storage.unlink_regular(sd, "a") is False
    victim = tmp_path / "victim"
    victim.write_text("v")
    os.symlink(victim, sd / "l")
    with pytest.raises(storage.UnsafeStorage):
        storage.unlink_regular(sd, "l")
    assert victim.exists() and os.path.islink(sd / "l")


def test_names_with_separators_are_rejected(sd):
    for bad in ("../x", "a/b", "", ".."):
        with pytest.raises(storage.UnsafeStorage):
            storage.write_atomic(sd, bad, b"x")


def test_state_json_roundtrip_and_redaction(sd):
    assert state.update(sd, last_session="abc", n=3) is True
    assert state.update(sd, note="token ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8") is True
    data = state.read(sd)
    assert data["last_session"] == "abc" and data["n"] == 3 and data["schema"] == 1
    assert "ghp_A1b2" not in (sd / "state.json").read_text()
    assert mode(sd / "state.json") == 0o600


def test_corrupt_state_json_is_replaced_not_crashed(sd):
    (sd / "state.json").write_text("{not json")
    assert state.read(sd) == {}
    assert state.update(sd, a=1) is True and state.read(sd)["a"] == 1
