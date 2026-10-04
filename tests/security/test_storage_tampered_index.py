"""A tampered warp.db (index_state.head) must never put option-shaped or malformed values on a Git command line."""
from __future__ import annotations

import os
import shutil
import sqlite3
import stat

import pytest

from gitwarp.memory import index

TAMPERED = ["--output=/tmp/gw-pwned", "-o/tmp/gw-pwned", "--exec-path=/tmp", "-n1", "abc123", "g" * 40, "a" * 39, "a" * 41,
            "a" * 40 + "\x00--output=x", "HEAD", "main..HEAD", "A" * 40, "", " " + "a" * 40, "a" * 40 + "\n"]


@pytest.fixture
def argv_log(tmp_path, monkeypatch):
    real = shutil.which("git")
    log = tmp_path / "argv.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    w = bindir / "git"
    w.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec "{real}" "$@"\n')
    w.chmod(w.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return log


@pytest.mark.parametrize("bad", TAMPERED)
def test_tampered_head_never_reaches_git_and_index_recovers(make_repo, argv_log, tmp_path, bad):
    r = make_repo().seed(4)
    index.ensure_indexed(r.path)
    db = r.path / ".git" / "git-warp" / "warp.db"
    con = sqlite3.connect(db)
    con.execute("UPDATE index_state SET value = ? WHERE key = 'head'", (bad,))
    con.commit()
    con.close()
    argv_log.write_text("")
    res = index.ensure_indexed(r.path, max_commits=2)
    lines = argv_log.read_text().splitlines()
    assert not any(bad and bad.strip("\n ") and bad.strip("\n ") in ln for ln in lines if bad not in ("", "HEAD")), lines
    for ln in lines:
        assert "gw-pwned" not in ln and "--exec-path" not in ln
        assert "\x00" not in ln
    assert not (tmp_path / "gw-pwned").exists() and not os.path.exists("/tmp/gw-pwned")
    assert res.get("mode") == "rebuild" and res["total_commits"] == 2 and not res.get("error")
    assert index.hotspots(r.path)
    con = sqlite3.connect(db)
    head = con.execute("SELECT value FROM index_state WHERE key = 'head'").fetchone()[0]
    con.close()
    assert head == r.sha()


def test_tampered_max_commits_does_not_crash(make_repo):
    r = make_repo().seed(3)
    index.ensure_indexed(r.path, max_commits=2)
    con = sqlite3.connect(r.path / ".git" / "git-warp" / "warp.db")
    con.execute("UPDATE index_state SET value = '²' WHERE key = 'max_commits'")
    con.commit()
    con.close()
    r.commit("more", {"z": "z\n"})
    res = index.ensure_indexed(r.path, max_commits=2)
    assert not res.get("error") and res["total_commits"] >= 2
