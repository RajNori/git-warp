import json
import os
import sqlite3
import subprocess
import time

import pytest

from gitwarp.core import git
from gitwarp.memory import cli, index

BASE = {"GIT_AUTHOR_NAME": "Test Author", "GIT_AUTHOR_EMAIL": "author@example.com",
        "GIT_COMMITTER_NAME": "Test Author", "GIT_COMMITTER_EMAIL": "author@example.com"}


def commit_at(repo, ts, message, files=None, author=None, delete=()):
    """Commit with an explicit timestamp (and optional author email)."""
    for rel, content in (files or {}).items():
        repo.write(rel, content)
    for rel in delete:
        (repo.path / rel).unlink()
    env = dict(os.environ, **BASE, GIT_AUTHOR_DATE=f"{ts} +0000", GIT_COMMITTER_DATE=f"{ts} +0000")
    if author:
        env["GIT_AUTHOR_EMAIL"] = author
        env["GIT_AUTHOR_NAME"] = author.split("@")[0]
    subprocess.run(["git", "add", "-A"], cwd=repo.path, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message], cwd=repo.path, env=env, check=True, capture_output=True)
    return repo.sha()


def total(repo):
    return int(repo.git("rev-list", "--count", "HEAD"))


def db(repo):
    return sqlite3.connect(repo.path / ".git" / "git-warp" / "warp.db")


def rich_repo(make_repo):
    r = make_repo()
    r.commit("c1", {"src/a.py": "a\nb\nc\n", "src/b.py": "b\n", "docs/read me.md": "doc\n", "é/ünï ç.txt": "u\n"})
    r.commit("c2", {"src/a.py": "a\nb\nc\nd\n", "src/b.py": "b2\n"})
    r.git("mv", "src/a.py", "src/a2.py")
    (r.path / "blob.bin").write_bytes(bytes(range(256)) * 4)
    r.commit("c3 rename + binary")
    r.git("checkout", "-q", "-b", "feature")
    r.commit("feat", {"feat.py": "f\n"})
    r.git("checkout", "-q", "main")
    r.commit("main side", {"m.py": "m\n"})
    r.git("merge", "--no-ff", "-q", "-m", "merge feature", "feature")
    return r


def test_index_rich_history(make_repo):
    r = rich_repo(make_repo)
    res = index.ensure_indexed(r.path)
    assert res["mode"] == "rebuild" and res["complete"] is True and not res.get("error")
    assert res["indexed"] == total(r) == res["total_commits"]
    conn = db(r)
    paths = {p for (p,) in conn.execute("SELECT path FROM files")}
    assert {"é/ünï ç.txt", "docs/read me.md", "src/a2.py", "blob.bin"} <= paths
    assert conn.execute("SELECT added, deleted FROM commit_files cf JOIN files f ON f.id=cf.file_id WHERE f.path='blob.bin'").fetchone() == (0, 0)
    merge_sha = r.sha()
    assert conn.execute("SELECT is_merge FROM commits WHERE sha=?", (merge_sha,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM commit_files WHERE sha=?", (merge_sha,)).fetchone()[0] == 0
    # rename recorded with old path
    row = conn.execute("SELECT status, old_path FROM commit_files cf JOIN files f ON f.id=cf.file_id WHERE f.path='src/a2.py'").fetchone()
    assert row == ("R", "src/a.py")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == index.schema.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0] >= 2
    assert conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0] == 1


def test_introduced_follows_rename_and_history(make_repo):
    r = rich_repo(make_repo)
    first = r.git("rev-list", "--max-parents=0", "HEAD")
    index.ensure_indexed(r.path)
    intro = index.introduced(r.path, "src/a2.py")
    assert intro["found"] and intro["sha"] == first and "src/a.py" in intro["renames_followed"]
    assert index.introduced(r.path, "nope.py")["found"] is False
    unicode_intro = index.introduced(r.path, "é/ünï ç.txt")
    assert unicode_intro["sha"] == first
    commits = index.file_commits(r.path, "src/a2.py")
    assert [c["subject"] for c in commits] == ["c3 rename + binary", "c2", "c1"]


def test_incremental_and_noop(make_repo):
    r = make_repo().seed(3)
    assert index.ensure_indexed(r.path)["indexed"] == 3
    again = index.ensure_indexed(r.path)
    assert again["indexed"] == 0 and again["mode"] == "noop"
    r.commit("new1", {"n1": "1\n"})
    r.commit("new2", {"n2": "1\n"})
    res = index.ensure_indexed(r.path)
    assert res["mode"] == "incremental" and res["indexed"] == 2 and res["total_commits"] == 5


def test_rewritten_history_amend(make_repo):
    r = make_repo().seed(3)
    index.ensure_indexed(r.path)
    old_head = r.sha()
    r.git("commit", "-q", "--amend", "-m", "amended", "--allow-empty")
    r.write("extra.txt", "e\n")
    r.git("add", "-A")
    r.git("commit", "-q", "--amend", "--no-edit")
    res = index.ensure_indexed(r.path)
    assert res["mode"] == "rebuild" and res["total_commits"] == total(r) == 3
    conn = db(r)
    assert conn.execute("SELECT COUNT(*) FROM commits WHERE sha=?", (old_head,)).fetchone()[0] == 0
    assert conn.execute("SELECT subject FROM commits WHERE sha=?", (r.sha(),)).fetchone()[0] == "amended"
    # co-change data from the discarded commit must be gone
    assert conn.execute("SELECT COUNT(*) FROM files WHERE path='extra.txt'").fetchone()[0] == 1


def test_reset_back_then_forward(make_repo):
    r = make_repo().seed(4)
    index.ensure_indexed(r.path)
    r.git("reset", "-q", "--hard", "HEAD~2")
    res = index.ensure_indexed(r.path)
    assert res["mode"] == "rebuild" and res["total_commits"] == 2
    r.commit("diverge", {"d.txt": "d\n"})
    res = index.ensure_indexed(r.path)
    assert res["total_commits"] == 3 and res["mode"] == "incremental"


def test_max_commits_partial_then_extend(make_repo):
    r = make_repo().seed(6)
    res = index.ensure_indexed(r.path, max_commits=2)
    assert res["complete"] is False and res["total_commits"] == 2 and any("partial" in w for w in res["warnings"])
    r.commit("more", {"z": "z\n"})
    res = index.ensure_indexed(r.path, max_commits=2)
    assert res["complete"] is False and res["total_commits"] == 3 and res["mode"] == "incremental"
    full = index.ensure_indexed(r.path)
    assert full["mode"] == "rebuild" and full["complete"] is True and full["total_commits"] == 7
    assert index.introduced(r.path, "f0.txt")["found"]


def test_corrupt_db_recovers(make_repo):
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    p = r.path / ".git" / "git-warp" / "warp.db"
    p.write_bytes(b"this is definitely not sqlite" * 50)
    res = index.ensure_indexed(r.path)
    assert res["total_commits"] == 2 and res["persisted"] is True
    assert any("rebuilding" in w or "unreadable" in w for w in res["warnings"])
    assert (r.path / ".git" / "git-warp" / "warp.db.corrupt").exists()
    assert index.hotspots(r.path)  # usable afterwards


def test_newer_schema_is_quarantined(make_repo):
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    c = db(r)
    c.execute(f"PRAGMA user_version = {index.schema.SCHEMA_VERSION + 5}")
    c.commit()
    c.close()
    res = index.ensure_indexed(r.path)
    assert res["total_commits"] == 2 and any("newer" in w for w in res["warnings"])


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_readonly_state_dir_degrades(make_repo):
    r = make_repo().seed(3)
    sd = r.path / ".git" / "git-warp"
    sd.mkdir()
    sd.chmod(0o500)
    try:
        res = index.ensure_indexed(r.path)
        assert res["persisted"] is False and res["total_commits"] == 3
        assert res["warnings"]
        assert index.hotspots(r.path)  # served from the in-memory fallback
    finally:
        sd.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_readonly_with_existing_db(make_repo):
    r = make_repo().seed(2)
    index.ensure_indexed(r.path)
    r.commit("late", {"late.txt": "1\n"})
    sd = r.path / ".git" / "git-warp"
    (sd / "warp.db").chmod(0o400)
    sd.chmod(0o500)
    try:
        res = index.ensure_indexed(r.path)
        assert res["total_commits"] == 3 and res["persisted"] is False
    finally:
        sd.chmod(0o700)
        (sd / "warp.db").chmod(0o600)


def test_unborn(empty_repo):
    res = index.ensure_indexed(empty_repo.path)
    assert res["unborn"] is True and res["indexed"] == 0 and not res.get("error")
    assert not (empty_repo.path / ".git" / "git-warp" / "warp.db").exists()


def test_not_a_repo(tmp_path):
    res = index.ensure_indexed(tmp_path)
    assert "error" in res


def test_shallow_clone_warns(make_repo, tmp_path):
    src = make_repo("src").seed(5)
    dst = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "2", f"file://{src.path}", str(dst)], check=True, capture_output=True)
    res = index.ensure_indexed(dst)
    assert res["shallow"] is True and any("shallow" in w for w in res["warnings"])
    assert res["total_commits"] == 2


def test_memory_disabled_config(make_repo):
    r = make_repo().seed(2)
    r.write(".claude/git-warp.local.md", "---\nmemory_enabled: false\n---\n")
    res = index.ensure_indexed(r.path)
    assert res["enabled"] is False and res["indexed"] == 0
    assert not (r.path / ".git" / "git-warp" / "warp.db").exists()


def test_cochange_counts_and_big_commit_excluded(make_repo):
    r = make_repo()
    t = 1_700_000_000
    for i in range(3):
        commit_at(r, t + i, f"xy{i}", {"x.py": f"{i}\n", "y.py": f"{i}\n"})
    commit_at(r, t + 10, "x with z", {"x.py": "z\n", "z.py": "z\n"})
    commit_at(r, t + 11, "solo", {"solo.py": "s\n"})
    big = {f"bulk/f{i}.txt": "b\n" for i in range(45)}
    big["x.py"] = "bulk\n"
    commit_at(r, t + 12, "bulk", big)
    index.ensure_indexed(r.path)
    cc = {c["path"]: c for c in index.cochange(r.path, "x.py")}
    assert cc["y.py"]["count"] == 3 and cc["z.py"]["count"] == 1
    assert not any(p.startswith("bulk/") for p in cc)
    assert cc["y.py"]["commits_touching_path"] == 5 and cc["y.py"]["ratio"] == 0.6
    assert index.cochange(r.path, "solo.py") == []
    assert index.cochange(r.path, "missing.py") == []
    # symmetric
    assert {c["path"]: c["count"] for c in index.cochange(r.path, "y.py")}["x.py"] == 3


def test_cochange_ignores_generated(make_repo):
    r = make_repo()
    commit_at(r, 1_700_000_000, "c", {"src/a.py": "1\n", "node_modules/x/i.js": "1\n", "dist/out.js": "1\n"})
    index.ensure_indexed(r.path)
    assert index.cochange(r.path, "src/a.py") == []
    r.write(".claude/git-warp.local.md", "---\nignored_paths: [\"gen/*\"]\n---\n")
    commit_at(r, 1_700_000_100, "c2", {"src/b.py": "1\n", "gen/g.py": "1\n", "src/c.py": "1\n"})
    index.ensure_indexed(r.path)
    got = {c["path"] for c in index.cochange(r.path, "src/b.py")}
    assert "src/c.py" in got and "gen/g.py" not in got


def test_hotspots_recency_churn_and_exclusions(make_repo):
    r = make_repo()
    day = 86400
    now = 1_700_000_000 + 400 * day
    # old, heavily-edited file vs recent, moderately edited file
    for i in range(6):
        commit_at(r, now - 380 * day + i * day, f"old{i}", {"old.py": "o\n" * (i + 1)})
    for i in range(3):
        commit_at(r, now - 5 * day + i, f"new{i}", {"new.py": "n\n" * (i + 1)})
    commit_at(r, now - 3 * day, "lock", {"package-lock.json": "{}\n", "node_modules/a/b.js": "1\n"})
    commit_at(r, now - 2 * day, "gone", {"gone.py": "g\n"})
    commit_at(r, now - 1 * day, "rm", delete=["gone.py"])
    index.ensure_indexed(r.path)
    hs = index.hotspots(r.path, now=now)
    order = [h["path"] for h in hs]
    assert order[0] == "new.py" and "old.py" in order
    assert "package-lock.json" not in order and "gone.py" not in order and not any("node_modules" in p for p in order)
    new = hs[0]
    assert new["commits"] == 3 and new["recent_commits_90d"] == 3
    assert hs[order.index("old.py")]["recent_commits_90d"] == 0
    # churn ranks by raw lines, so the old file wins there
    ch = index.churn(r.path)
    assert ch[0]["path"] == "old.py"
    assert [c["path"] for c in index.churn(r.path, "node_modules")] == []


def test_churn_prefix(make_repo):
    r = make_repo()
    commit_at(r, 1_700_000_000, "a", {"api/x.py": "1\n2\n", "api/sub/y.py": "1\n", "web/z.py": "1\n"})
    index.ensure_indexed(r.path)
    assert {c["path"] for c in index.churn(r.path, "api")} == {"api/x.py", "api/sub/y.py"}
    assert {c["path"] for c in index.churn(r.path, "apix")} == set()


def test_reverts_detected(make_repo):
    r = make_repo().seed(1)
    bad = r.commit("risky change", {"core/engine.py": "v1\n"})
    r.git("revert", "--no-edit", bad)
    r.commit("again", {"core/engine.py": "v2\n"})
    r.git("revert", "--no-edit", "HEAD")
    index.ensure_indexed(r.path)
    rv = index.reverts(r.path)
    assert rv["revert_commits_total"] == 2 and rv["reverts"][-1]["reverts"] == bad
    assert rv["reverts"][-1]["reverted_subject"] == "risky change"
    assert rv["repeatedly_reverted_files"][0] == {"path": "core/engine.py", "revert_commits": 2, "last_revert": rv["repeatedly_reverted_files"][0]["last_revert"]}


def test_authors_note_and_counts(make_repo):
    r = make_repo()
    commit_at(r, 1_700_000_000, "1", {"lib/a.py": "1\n"}, author="alice@example.com")
    commit_at(r, 1_700_000_100, "2", {"lib/a.py": "2\n"}, author="alice@example.com")
    commit_at(r, 1_700_000_200, "3", {"lib/a.py": "3\n", "lib/b.py": "1\n"}, author="bob@example.com")
    index.ensure_indexed(r.path)
    a = index.authors(r.path, "lib/a.py")
    assert a["note"] == "historical contribution evidence, not current ownership"
    assert [(x["email"], x["commits"]) for x in a["authors"]] == [("alice@example.com", 2), ("bob@example.com", 1)]
    d = index.authors(r.path, "lib")
    assert d["scope"] == "directory" and d["total_commits"] == 3
    assert index.authors(r.path, "ghost")["found"] is False
    assert db(r).execute("SELECT commits FROM authors WHERE email='alice@example.com'").fetchone()[0] == 2


def test_session_rows(make_repo):
    r = make_repo().seed(1)
    assert index.record_session(r.path, "sess-1", "main", r.sha())
    assert index.record_session(r.path, "sess-1", "main", r.sha())
    assert [s["session_id"] for s in index.list_sessions(r.path)] == ["sess-1"]


# ------------------------------------------------------------------ CLI

def run_cli(capsys, repo, *argv):
    code = cli.main(["memory", *argv, "--repo", str(repo.path)])
    return code, json.loads(capsys.readouterr().out)


def test_cli_queries_auto_index_and_report_freshness(make_repo, capsys):
    r = rich_repo(make_repo)
    code, out = run_cli(capsys, r, "cochange", "src/b.py")
    assert code == 0 and out["index"]["mode"] == "rebuild" and out["index"]["complete"] is True
    assert any(c["path"] in ("src/a.py", "src/a2.py") for c in out["cochange"])
    code, out = run_cli(capsys, r, "hotspots", "--limit", "3")
    assert code == 0 and out["index"]["mode"] == "noop" and len(out["hotspots"]) <= 3 and "formula" in out
    code, out = run_cli(capsys, r, "authors", "src/b.py")
    assert out["note"].startswith("historical contribution evidence")
    code, out = run_cli(capsys, r, "introduced", "src/a2.py")
    assert out["introduced"]["found"]
    code, out = run_cli(capsys, r, "status")
    assert out["index"]["exists"] and out["index"]["up_to_date"] is True


def test_cli_cwd_relative_path(make_repo, capsys, monkeypatch):
    r = rich_repo(make_repo)
    monkeypatch.chdir(r.path / "src")
    code = cli.main(["memory", "introduced", "b.py"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["introduced"]["found"] and out["introduced"]["path"] == "src/b.py"


def test_cli_forget_requires_yes(make_repo, capsys):
    r = make_repo().seed(2)
    run_cli(capsys, r, "index")
    from gitwarp.memory import recorder
    recorder.record(r.path, {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s"})
    sd = r.path / ".git" / "git-warp"
    assert (sd / "warp.db").exists() and (sd / "flight-recorder.jsonl").exists()
    code, out = run_cli(capsys, r, "forget")
    assert code == 2 and "--yes" in out["error"] and (sd / "warp.db").exists()
    (sd / "other.txt").write_text("keep")
    code, out = run_cli(capsys, r, "forget", "--yes")
    assert code == 0
    assert not (sd / "warp.db").exists() and not (sd / "flight-recorder.jsonl").exists()
    assert (sd / "other.txt").exists() and (sd / "state.json").exists()


def test_cli_errors_are_structured(tmp_path, capsys, make_repo):
    code = cli.main(["memory", "status", "--repo", str(tmp_path)])
    assert code == 2 and "error" in json.loads(capsys.readouterr().out)
    code = cli.main(["memory", "bogus"])
    assert code == 2 and "error" in json.loads(capsys.readouterr().out)
    code = cli.main(["memory", "cochange"])
    assert code == 2 and "error" in json.loads(capsys.readouterr().out)
    r = make_repo().seed(1)
    r.write(".claude/git-warp.local.md", "---\nmemory_enabled: false\n---\n")
    code, out = run_cli(capsys, r, "hotspots")
    assert code == 2 and "disabled" in out["error"]


def test_cli_empty_repo(empty_repo, capsys):
    code, out = run_cli(capsys, empty_repo, "hotspots")
    assert code == 0 and out["hotspots"] == [] and any("no commits" in w for w in out["warnings"])
