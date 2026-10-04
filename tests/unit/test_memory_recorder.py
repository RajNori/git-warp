import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

from gitwarp.memory import recorder, state

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", "plugin", "scripts")


def sd(repo):
    return repo.path / ".git" / "git-warp"


def lines(repo):
    p = sd(repo) / "flight-recorder.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def bash(cmd, **kw):
    return {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash", "tool_input": {"command": cmd}, **kw}


def test_bash_command_redacted_and_truncated(repo):
    secret = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    assert recorder.record(repo.path, bash(f"curl -H 'Authorization: Bearer abcdef1234567890SECRET' -u x:y https://h/ && export API_KEY=hunter2hunter2 && echo {secret}"))
    raw = (sd(repo) / "flight-recorder.jsonl").read_text()
    for needle in ("abcdef1234567890SECRET", "hunter2hunter2", secret):
        assert needle not in raw
    rec = lines(repo)[0]
    assert "[REDACTED]" in rec["command"] and rec["tool"] == {"name": "Bash", "category": "shell"}
    assert rec["branch"] == "main" and len(rec["head"]) == 12 and rec["session_id"] == "s1"
    recorder.record(repo.path, bash("echo " + "a" * 1000))
    assert len(lines(repo)[-1]["command"]) <= 301


def test_git_category(repo):
    recorder.record(repo.path, bash("cd x && git status"))
    recorder.record(repo.path, bash("ls -la"))
    recorder.record(repo.path, bash("echo digit"))
    cats = [r["tool"]["category"] for r in lines(repo)]
    assert cats == ["git", "shell", "shell"]


def test_edit_never_stores_contents(repo):
    ev = {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Edit",
          "tool_input": {"file_path": str(repo.path / "f0.txt"), "old_string": "TOPSECRETOLD", "new_string": "TOPSECRETNEW", "content": "TOPSECRETCONTENT"},
          "tool_response": {"filePath": "x", "output": "TOPSECRETRESPONSE"}, "prompt": "TOPSECRETPROMPT"}
    assert recorder.record(repo.path, ev)
    raw = (sd(repo) / "flight-recorder.jsonl").read_text()
    assert "TOPSECRET" not in raw
    rec = lines(repo)[0]
    assert rec["files"] == ["f0.txt"] and rec["tool"]["category"] == "edit"
    assert set(rec) <= {"ts", "session_id", "hook_event", "tool", "files", "command", "branch", "head", "dirty_count", "test_outcome", "source"}


def test_write_with_secret_looking_content_not_stored(repo):
    ev = {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Write", "tool_input": {"file_path": "new.py", "content": "API_KEY='ghp_" + "a" * 30 + "'"}}
    recorder.record(repo.path, ev)
    assert "ghp_" not in (sd(repo) / "flight-recorder.jsonl").read_text()


def test_outside_repo_and_relative_paths(repo, tmp_path):
    outside = tmp_path / "elsewhere" / "x.py"
    recorder.record(repo.path, {"tool_name": "Write", "tool_input": {"file_path": str(outside)}})
    recorder.record(repo.path, {"tool_name": "Write", "tool_input": {"file_path": "../escape.py"}})
    recorder.record(repo.path, {"tool_name": "Write", "tool_input": {"file_path": "sub/new.py"}})
    recorder.record(repo.path, {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(repo.path / "n.ipynb")}})
    files = [r["files"] for r in lines(repo)]
    assert files == [["<outside-repo>"], ["<outside-repo>"], ["sub/new.py"], ["n.ipynb"]]
    assert str(tmp_path) not in (sd(repo) / "flight-recorder.jsonl").read_text()


def test_test_outcome_only_when_explicit(repo):
    recorder.record(repo.path, bash("pytest -q"))
    recorder.record(repo.path, bash("pytest -q", test_outcome="failed"))
    recorder.record(repo.path, bash("pytest -q", test_outcome="rm -rf /"))
    out = lines(repo)
    assert "test_outcome" not in out[0] and out[1]["test_outcome"] == "failed" and "test_outcome" not in out[2]


def test_disabled_and_non_repo_and_garbage(repo, tmp_path):
    assert recorder.record(tmp_path, bash("ls")) is False
    assert recorder.record(repo.path, None) is False  # type: ignore[arg-type]
    assert recorder.record(repo.path, {"tool_name": "Bash", "tool_input": "notadict"}) is True
    repo.write(".claude/git-warp.local.md", "---\nrecorder_enabled: false\n---\n")
    before = len(lines(repo))
    assert recorder.record(repo.path, bash("ls")) is False
    assert len(lines(repo)) == before


def test_retention_compaction_daily(repo):
    recorder.record(repo.path, bash("first"))
    p = sd(repo) / "flight-recorder.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with open(p, "a") as fh:
        fh.write(json.dumps({"ts": old, "session_id": "old"}) + "\n" + json.dumps({"ts": recent, "session_id": "recent"}) + "\ngarbage line\n")
    recorder.record(repo.path, bash("second"))  # compacted <24h ago: nothing dropped
    raw = p.read_text().splitlines()
    assert any('"old"' in x for x in raw) and "garbage line" in raw
    st = state.read(sd(repo))
    state.update(sd(repo), last_compaction=st["last_compaction"] - 2 * 86400)
    recorder.record(repo.path, bash("third"))
    sessions = [json.loads(x).get("session_id") for x in p.read_text().splitlines()]
    assert "old" not in sessions and "recent" in sessions
    assert all(x.startswith("{") for x in p.read_text().splitlines())
    assert state.read(sd(repo))["last_compaction"] > st["last_compaction"]


def test_rotation_at_size_cap(repo, monkeypatch):
    monkeypatch.setattr(recorder, "MAX_BYTES", 2000)
    for i in range(40):
        recorder.record(repo.path, bash(f"echo {i} " + "x" * 80))
    p = sd(repo) / "flight-recorder.jsonl"
    assert p.stat().st_size <= 2000 + 400
    assert (sd(repo) / "flight-recorder.jsonl.1").exists()
    allrecs = recorder.tail(repo.path, 1000)
    assert 10 < len(allrecs) <= 40
    assert allrecs[-1]["command"].startswith("echo 39")


def test_concurrent_threads(repo):
    def work(t):
        for i in range(25):
            recorder.record(repo.path, bash(f"echo t{t} i{i}", session_id=f"t{t}"))
    ths = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    recs = lines(repo)
    assert len(recs) == 200
    assert {r["session_id"] for r in recs} == {f"t{t}" for t in range(8)}


def test_concurrent_processes(repo):
    code = (
        "import sys; sys.path.insert(0, %r); from gitwarp.memory import recorder\n"
        "n=sys.argv[1]\n"
        "for i in range(30): recorder.record(%r, {'tool_name':'Bash','session_id':'p'+n,'tool_input':{'command':'echo '+n+' '+str(i)}})\n"
    ) % (SCRIPTS, str(repo.path))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(n)]) for n in range(5)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    recs = lines(repo)
    assert len(recs) == 150


def test_tail_and_session_summary(repo):
    for i in range(3):
        recorder.record(repo.path, {"session_id": "A", "tool_name": "Edit", "tool_input": {"file_path": "f0.txt"}})
    recorder.record(repo.path, {"session_id": "A", "tool_name": "Edit", "tool_input": {"file_path": "f1.txt"}})
    recorder.record(repo.path, bash("git commit -m x", session_id="A"))
    recorder.record(repo.path, bash("make", session_id="B"))
    assert len(recorder.tail(repo.path, 2)) == 2 and recorder.tail(repo.path, 0) == []
    sums = recorder.summarize_sessions(repo.path)
    a = next(s for s in sums if s["session_id"] == "A")
    assert a["edits"] == 4 and a["git_commands"] == 1 and a["files_touched"] == 2 and a["top_files"][0] == {"path": "f0.txt", "edits": 3}
    assert recorder.summarize_sessions(repo.path, session_id="B")[0]["shell"] == 1
    assert recorder.stats(repo.path)["records"] == 6


def test_state_only_in_git_dir(repo):
    recorder.record(repo.path, bash("ls", session_id="x"))
    assert repo.git("status", "--porcelain") == ""
    assert not (repo.path / ".claude").exists()
    st = state.read(sd(repo))
    assert st["last_session"] == "x" and st["schema"] == 1
