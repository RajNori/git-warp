import json

from tests.hook_helpers import run_hook


def post(cwd, tool="Bash", tin=None, **kw):
    ev = {"hook_event_name": "PostToolUse", "session_id": "s9", "cwd": str(cwd), "tool_name": tool, "tool_input": tin or {"command": "ls"},
          "tool_response": {"stdout": "SHOULDNOTBESTORED"}, **kw}
    return run_hook("post_tool", ev, cwd)


def records(repo):
    p = repo.path / ".git" / "git-warp" / "flight-recorder.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_records_bash_and_edit(repo):
    code, out, err, elapsed = post(repo.path, tin={"command": "git log --oneline && export TOKEN=abcdef123456789xyz"})
    assert code == 0 and json.loads(out) == {} and err == ""
    post(repo.path, "Edit", {"file_path": str(repo.path / "f0.txt"), "old_string": "a", "new_string": "b"})
    recs = records(repo)
    assert [r["tool"]["category"] for r in recs] == ["git", "edit"]
    raw = (repo.path / ".git" / "git-warp" / "flight-recorder.jsonl").read_text()
    assert "abcdef123456789xyz" not in raw and "SHOULDNOTBESTORED" not in raw
    assert recs[1]["files"] == ["f0.txt"] and recs[0]["hook_event"] == "PostToolUse"
    assert elapsed < 3  # generous bound; typical is ~100ms


def test_ignores_other_tools(repo):
    code, out, _, _ = post(repo.path, "Read", {"file_path": "x"})
    assert code == 0 and json.loads(out) == {} and records(repo) == []


def test_clean_tree_and_no_claude_dir(repo):
    post(repo.path)
    post(repo.path, "Write", {"file_path": "new.py", "content": "print(1)"})
    assert repo.git("status", "--porcelain") == ""
    assert not (repo.path / ".claude").exists()


def test_non_git_dir(tmp_path):
    code, out, err, _ = post(tmp_path)
    assert code == 0 and json.loads(out) == {} and not list(tmp_path.iterdir())


def test_malformed(repo):
    for raw in ("", "{oops", "[1]", '{"tool_name": 5}'):
        code, out, err, _ = run_hook("post_tool", None, repo.path, raw=raw)
        assert code == 0 and json.loads(out) == {}
    assert records(repo) == []


def test_worktree(repo, tmp_path):
    wt = tmp_path / "wt2"
    repo.git("worktree", "add", "-q", "-b", "wt2b", str(wt))
    post(wt, "Edit", {"file_path": str(wt / "f0.txt")})
    recs = records(repo)
    assert recs and recs[0]["branch"] == "wt2b" and recs[0]["files"] == ["f0.txt"]
    assert not (wt / ".git" / "git-warp").exists()


def test_recorder_disabled(repo):
    repo.write(".claude/git-warp.local.md", "---\nrecorder_enabled: false\n---\n")
    post(repo.path)
    assert records(repo) == []
