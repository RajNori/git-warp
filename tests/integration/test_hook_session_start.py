import json
import sqlite3

import pytest

from tests.hook_helpers import run_hook

EV = {"hook_event_name": "SessionStart", "session_id": "sess-1", "source": "startup", "transcript_path": "/x/t.jsonl"}


def start(cwd, **kw):
    return run_hook("session_start", {**EV, "cwd": str(cwd), **kw}, cwd)


def ctx(out):
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    return data["hookSpecificOutput"]["additionalContext"]


def test_context_content(repo):
    code, out, err, _ = start(repo.path)
    assert code == 0 and err == ""
    text = ctx(out)
    assert "branch main" in text and "working tree: clean" in text and "stashes: 0" in text
    assert "recent commits" in text and "commit 2" in text and "guard is active" in text and "no upstream" in text
    assert len(text) < 1800


def test_dirty_stash_and_operation(make_repo):
    r = make_repo().seed(1)
    r.merge_conflict()
    code, out, _, _ = start(r.path)
    text = ctx(out)
    assert "IN PROGRESS: merge" in text and "1 conflicted" in text


def test_detached_and_untracked(repo):
    repo.git("checkout", "-q", "--detach", "HEAD~1")
    repo.write("new.txt")
    text = ctx(start(repo.path)[1])
    assert "DETACHED HEAD" in text and "1 untracked" in text


def test_unborn_repo(empty_repo):
    code, out, _, _ = start(empty_repo.path)
    assert code == 0 and "no commits yet" in ctx(out)
    assert empty_repo.git("status", "--porcelain") == ""


def test_commit_subject_is_sanitized(make_repo):
    r = make_repo()
    r.commit("evil\x1b[31m subject token=supersecretvalue12345 " + "x" * 300)
    text = ctx(start(r.path)[1])
    assert "supersecretvalue12345" not in text and "\x1b" not in text


def test_indexes_and_records_session(repo):
    start(repo.path)
    db = sqlite3.connect(repo.path / ".git" / "git-warp" / "warp.db")
    assert db.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 3
    assert db.execute("SELECT session_id, branch FROM sessions").fetchone() == ("sess-1", "main")
    # second start is incremental/no-op and does not duplicate the session
    start(repo.path)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_memory_disabled_no_db(repo):
    repo.write(".claude/git-warp.local.md", "---\nmemory_enabled: false\n---\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "-m", "cfg")
    code, out, _, _ = start(repo.path)
    assert code == 0 and ctx(out)
    assert not (repo.path / ".git" / "git-warp" / "warp.db").exists()


def test_non_git_dir_silent(tmp_path):
    code, out, err, _ = start(tmp_path)
    assert code == 0 and out == ""


@pytest.mark.parametrize("raw", ["", "   ", "{not json", "[]", "null", "42"])
def test_malformed_stdin(repo, raw):
    code, out, err, _ = run_hook("session_start", None, repo.path, raw=raw)
    assert code == 0 and out == "" and not (repo.path / ".git" / "git-warp").exists()


def test_working_tree_untouched(repo):
    start(repo.path)
    assert repo.git("status", "--porcelain") == ""
    assert not (repo.path / ".claude").exists()


def test_worktree_state_in_common_dir(repo, tmp_path):
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    code, out, _, _ = start(wt)
    assert code == 0 and "worktrees: 2" in ctx(out) and "branch wtb" in ctx(out)
    assert (repo.path / ".git" / "git-warp" / "warp.db").exists()
    assert not (wt / ".claude").exists()
    import subprocess
    assert subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True).stdout == ""
