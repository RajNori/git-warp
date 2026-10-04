"""Memory subsystem on top of core/storage: quarantine retention, forget, privacy migration, no-follow reads."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys

import pytest

from gitwarp.memory import index, schema
from tests.hook_helpers import SCRIPTS
from tests.fake_secrets import GITHUB, HF, OPENAI

WARP = SCRIPTS / "warp.py"


def warp(repo_path, *args):
    p = subprocess.run([sys.executable, str(WARP), *args, "--repo", str(repo_path)], capture_output=True, text=True, timeout=60, cwd=str(repo_path))
    return p.returncode, p.stdout, p.stderr


def sd(r):
    return r.path / ".git" / "git-warp"


def test_repeated_corruption_rotates_quarantine_instead_of_deleting(make_repo):
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    for i in range(3):
        (sd(r) / "warp.db").write_bytes(f"garbage-{i}".encode() * 40)
        index.ensure_indexed(r.path, rebuild=True)
    kept = {p.name: p.read_bytes() for p in sd(r).iterdir() if ".corrupt" in p.name}
    assert set(kept) == {"warp.db.corrupt", "warp.db.corrupt.1", "warp.db.corrupt.2"}
    assert [kept[n][:9] for n in ("warp.db.corrupt", "warp.db.corrupt.1", "warp.db.corrupt.2")] == [b"garbage-2", b"garbage-1", b"garbage-0"]
    assert {stat.S_IMODE(p.stat().st_mode) for p in sd(r).iterdir()} == {0o600}


def test_corrupt_db_that_is_a_symlink_is_not_quarantined_or_followed(make_repo, tmp_path):
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL")
    (sd(r) / "warp.db").unlink()
    os.symlink(victim, sd(r) / "warp.db")
    res = index.ensure_indexed(r.path)
    assert res["persisted"] is False and res["warnings"] and res["total_commits"] == 2
    assert victim.read_text() == "ORIGINAL" and os.path.islink(sd(r) / "warp.db")


def test_read_only_paths_do_not_follow_symlinks(make_repo, tmp_path):
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    other = tmp_path / "other.db"
    (sd(r) / "warp.db").replace(other)
    os.symlink(other, sd(r) / "warp.db")
    assert index.list_sessions(r.path) == []
    st = index.index_status(r.path)
    assert st["exists"] is False and "refused" in st["error"]


def test_database_and_sidecars_are_private_after_indexing(make_repo):
    r = make_repo().seed(3)
    index.ensure_indexed(r.path)
    index.record_session(r.path, "s1", "main", "abc")
    assert {stat.S_IMODE(p.stat().st_mode) for p in sd(r).iterdir()} == {0o600}


def test_v1_database_is_scrubbed_on_upgrade(make_repo):
    """A warp.db written by v1 (raw commit text) is emptied and vacuumed so old secrets do not survive."""
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    p = sd(r) / "warp.db"
    con = sqlite3.connect(p)
    con.execute(f"UPDATE commits SET subject = 'leak {GITHUB}'")
    con.commit()
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    assert GITHUB[:8].encode() in p.read_bytes()
    res = index.ensure_indexed(r.path)
    assert res["total_commits"] == 2 and res["mode"] == "rebuild"
    assert GITHUB[:8].encode() not in p.read_bytes()
    con = sqlite3.connect(p)
    assert con.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
    con.close()


def test_commit_text_authors_paths_and_branch_are_redacted_in_db(make_repo):
    r = make_repo().seed(1)
    r.branch(f"rel/{GITHUB}", checkout=True)
    r.commit(f"feat: key {OPENAI}", {f"HF_TOKEN={HF}.txt": "x\n"})
    index.ensure_indexed(r.path)
    index.record_session(r.path, "s", f"rel/{GITHUB}", "abc")
    con = sqlite3.connect(sd(r) / "warp.db")
    blob = "".join(" ".join(map(str, row)) for t in ("commits", "files", "refs", "sessions", "events", "commit_files")
                   for row in con.execute(f"select * from {t}"))
    con.close()
    assert OPENAI[:12] not in blob and HF[:7] not in blob and GITHUB[:8] not in blob


def test_forget_requires_yes_and_deletes_only_regular_files(make_repo, tmp_path):
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL")
    os.symlink(victim, sd(r) / "warp.db.corrupt")
    os.mkfifo(sd(r) / "flight-recorder.jsonl.1")
    (sd(r) / "state.json").write_text("{}")
    rc, out, _ = warp(r.path, "memory", "forget")
    assert rc == 2 and (sd(r) / "warp.db").exists()
    rc, out, _ = warp(r.path, "memory", "forget", "--yes")
    data = json.loads(out)
    assert rc == 0
    assert not (sd(r) / "warp.db").exists()
    assert victim.read_text() == "ORIGINAL" and os.path.islink(sd(r) / "warp.db.corrupt")
    assert stat.S_ISFIFO(os.lstat(sd(r) / "flight-recorder.jsonl.1").st_mode)
    assert (sd(r) / "state.json").exists()
    assert any("could not delete" in w for w in data["warnings"])


def test_memory_cli_reports_structured_warning_for_unsafe_state_dir(make_repo, tmp_path):
    r = make_repo().seed(2)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, sd(r))
    rc, out, err = warp(r.path, "memory", "index")
    assert rc == 0 and "Traceback" not in err
    data = json.loads(out)
    assert data["index"]["persisted"] is False and data["warnings"]
    assert list(outside.iterdir()) == []
