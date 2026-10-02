import json

import pytest

from tests.hook_helpers import run_hook


def stop(cwd, **kw):
    return run_hook("stop", {"hook_event_name": "Stop", "session_id": "s", "cwd": str(cwd), "stop_hook_active": False, **kw}, cwd)


def msg(out):
    data = json.loads(out)
    assert set(data) == {"systemMessage"}  # never decision: block
    return data["systemMessage"]


def test_clean_tree_is_silent(repo):
    code, out, err, _ = stop(repo.path)
    assert code == 0 and out == "" and err == ""


def test_stop_hook_active_is_noop(repo):
    repo.write("f0.txt", "changed\n")
    code, out, _, _ = stop(repo.path, stop_hook_active=True)
    assert code == 0 and out == ""
    assert not (repo.path / ".git" / "git-warp" / "state.json").exists()


def test_report_then_dedupe_then_change(repo):
    repo.write("f0.txt", "changed\nlines\n")
    repo.write("new.txt", "n\n")
    repo.git("add", "f0.txt")
    code, out, _, _ = stop(repo.path)
    text = msg(out)
    assert "branch main" in text and "2 path(s)" in text and "1 staged" in text and "1 untracked" in text
    assert "Risk: LOW" in text and "f0.txt" in text and "Next:" in text
    # identical state → quiet
    assert stop(repo.path)[1] == ""
    # something changed → new report
    repo.write("f1.txt", "again\n")
    assert "Changed: 3 path(s)" in msg(stop(repo.path)[1])
    # clean again, then dirty with the same content → reported again
    repo.git("reset", "-q", "--hard")
    repo.git("clean", "-fdq")
    assert stop(repo.path)[1] == ""
    repo.write("f0.txt", "changed\nlines\n")
    assert "1 path(s)" in msg(stop(repo.path)[1])


def test_high_risk_secret_and_conflict_reasons(repo):
    repo.write(".env", "TOKEN=1\n")
    text = msg(stop(repo.path)[1])
    assert "Risk: HIGH" in text and "secret-like file" in text and ".env" in text


def test_medium_lockfile_migration_and_sensitive(repo):
    repo.write("package-lock.json", "{}\n")
    repo.write("db/migrations/001_init.sql", "create table t(id int);\n")
    repo.write("src/auth/login.py", "x=1\n")
    text = msg(stop(repo.path)[1])
    assert "Risk: MEDIUM" in text
    assert "lockfile changed" in text and "migration" in text and "sensitive path" in text


def test_conflict_is_high(make_repo):
    r = make_repo().seed(1)
    r.merge_conflict()
    text = msg(stop(r.path)[1])
    assert "Risk: HIGH" in text and "conflict" in text and "1 conflicted" in text and "merge is in progress" in text


def test_diffstat_truncated(repo):
    for i in range(20):
        repo.write(f"f{i % 3}.txt" if i < 3 else f"extra{i}.txt", f"{i}\n" * (i + 1))
    text = msg(stop(repo.path)[1])
    assert text.count("\n  ") <= 9 and "… and" in text


def test_unborn_repo_with_files(empty_repo):
    empty_repo.write("a.txt", "x\n")
    text = msg(stop(empty_repo.path)[1])
    assert "no commits" in text and "1 untracked" in text


def test_non_git_dir(tmp_path):
    code, out, _, _ = stop(tmp_path)
    assert code == 0 and out == ""


@pytest.mark.parametrize("raw", ["", "{bad", "[]", "null"])
def test_malformed(repo, raw):
    repo.write("f0.txt", "changed\n")
    code, out, err, _ = run_hook("stop", None, repo.path, raw=raw)
    assert code == 0 and out == ""


def test_hooks_leave_worktree_clean(repo):
    from tests.hook_helpers import run_hook as rh
    base = {"session_id": "s", "cwd": str(repo.path)}
    rh("session_start", {**base, "hook_event_name": "SessionStart", "source": "startup"}, repo.path)
    rh("post_tool", {**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}, repo.path)
    rh("stop", {**base, "hook_event_name": "Stop"}, repo.path)
    assert repo.git("status", "--porcelain") == ""
    assert not (repo.path / ".claude").exists()
    assert (repo.path / ".git" / "git-warp").is_dir()


def test_worktree_report_and_state(repo, tmp_path):
    wt = tmp_path / "wt3"
    repo.git("worktree", "add", "-q", "-b", "wt3b", str(wt))
    (wt / "f0.txt").write_text("changed in wt\n")
    text = msg(stop(wt)[1])
    assert "branch wt3b" in text
    assert (repo.path / ".git" / "git-warp" / "state.json").exists()
    assert not (wt / ".git" / "git-warp").exists()
