"""End-to-end: run scripts/hook_git_guard.py as Claude Code would and assert JSON + no repo mutation."""
import json
import os
import subprocess
import sys

import pytest

from tests.conftest import ROOT, SCRIPTS

HOOK = SCRIPTS / "hook_git_guard.py"


def snapshot(repo):
    """State that a guard must never change."""
    st = repo.git("status", "--porcelain=v1", "-uall")
    return (repo.sha(), repo.git("for-each-ref"), st, repo.git("stash", "list"),
            sorted(p.name for p in repo.path.iterdir()), repo.git("reflog", "--all"))


def call(payload, cwd=None, raw=None):
    env = dict(os.environ)
    p = subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload), capture_output=True,
                       text=True, cwd=cwd, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stderr.strip() == ""
    return json.loads(p.stdout) if p.stdout.strip() else None


def event(command, cwd, **extra):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd), **extra}


def decision(out):
    hs = out.get("hookSpecificOutput") if out else None
    return (hs or {}).get("permissionDecision")


@pytest.fixture
def dirty_repo(repo):
    repo.write("f0.txt", "modified\n")
    repo.write("untracked.txt", "u\n")
    return repo


@pytest.mark.parametrize("command,expected", [
    ("git reset --hard", "deny"), ("git clean -fd", "deny"), ("git checkout .", "deny"), ("git restore .", "deny"),
    ("git push --force origin main", "deny"), ("git push -f", "deny"),  # repo is on main
    ("bash -c 'git reset --hard HEAD~1'", "deny"), ("(cd . && git clean -fdx)", "deny"), ("rm -rf .git", "deny"),
    ("git stash clear", "deny"), ("git reflog expire --expire=now --all", "deny"), ("git gc --prune=now", "deny"),
    ("git rebase main", "ask"), ("git commit --amend", "ask"), ("git branch -D foo", "ask"), ("git stash drop", "ask"),
    ("git push --force origin feature/x", "ask"), ("git tag -d v1", "ask"), ("git $X status", "ask"),
    ("git status", None), ("git log --oneline", None), ("git diff", None), ("git add -A", None), ("git commit -m 'reset --hard'", None),
    ("echo 'git reset --hard'", None), ("git checkout -b topic", None), ("git stash", None), ("ls", None),
    ("grep -r 'git clean -fd' .", None), ("git reset --soft HEAD~1", None), ("git clean -n", None),
])
def test_hook_decisions_and_repo_is_never_mutated(dirty_repo, command, expected):
    before = snapshot(dirty_repo)
    out = call(event(command, dirty_repo.path), cwd=dirty_repo.path)
    if expected is None:
        assert out == {}
    else:
        assert decision(out) == expected, out
        hs = out["hookSpecificOutput"]
        assert hs["hookEventName"] == "PreToolUse"
        assert hs["permissionDecisionReason"].startswith("Git Warp:")
        assert "Safer" in hs["permissionDecisionReason"]
        assert set(out) == {"hookSpecificOutput"}
    assert snapshot(dirty_repo) == before
    assert (dirty_repo.path / "untracked.txt").read_text() == "u\n"
    assert (dirty_repo.path / "f0.txt").read_text() == "modified\n"


def test_branch_is_resolved_from_event_cwd(repo):
    repo.branch("feature/x", checkout=True)
    assert decision(call(event("git push --force", repo.path))) == "ask"
    repo.checkout("main")
    assert decision(call(event("git push --force", repo.path))) == "deny"


def test_cwd_can_be_a_subdirectory(repo):
    repo.write("sub/dir/file.txt", "x")
    out = call(event("git push -f", repo.path / "sub" / "dir"))
    assert decision(out) == "deny"


def test_config_protected_branches_and_strict_mode_are_honoured(repo):
    repo.branch("trunk", checkout=True)
    repo.write(".claude/git-warp.local.md", "---\nprotected_branches: [trunk]\nsafety_mode: strict\n---\n")
    assert decision(call(event("git push -f", repo.path))) == "deny"
    assert decision(call(event("git rebase main", repo.path))) == "deny"
    assert decision(call(event("git stash drop", repo.path))) == "ask"
    repo.write(".claude/git-warp.local.md", "---\nprotected_branches: [trunk]\n---\n")
    assert decision(call(event("git rebase main", repo.path))) == "ask"
    repo.write(".claude/git-warp.local.md", "---\nprotected_branches: [other]\n---\n")
    repo.checkout("main")
    assert decision(call(event("git push -f", repo.path))) == "ask"


def test_invalid_config_falls_back_to_defaults(repo):
    repo.write(".claude/git-warp.local.md", "not frontmatter at all\n\x00\xff")
    assert decision(call(event("git push -f", repo.path))) == "deny"
    repo.write(".claude/git-warp.local.md", "---\nsafety_mode: bogus\nprotected_branches: 5\n---\n")
    assert decision(call(event("git push -f", repo.path))) == "deny"


def test_not_a_repo_still_classifies(tmp_path):
    assert decision(call(event("git reset --hard", tmp_path))) == "deny"
    assert decision(call(event("git push -f", tmp_path))) == "ask"
    assert call(event("git status", tmp_path)) == {}


def test_unborn_and_detached_repos(empty_repo, repo):
    assert decision(call(event("git clean -fd", empty_repo.path))) == "deny"
    repo.git("checkout", "-q", "--detach")
    assert decision(call(event("git push -f", repo.path))) == "ask"


@pytest.mark.parametrize("cwd", ["/definitely/not/a/dir", "", 5, None, ["x"]])
def test_bad_cwd_values_are_tolerated(cwd):
    ev = {"tool_name": "Bash", "tool_input": {"command": "git reset --hard"}, "cwd": cwd}
    assert decision(call(ev)) == "deny"


def test_missing_cwd_uses_process_cwd(repo):
    ev = {"tool_name": "Bash", "tool_input": {"command": "git push -f"}}
    assert decision(call(ev, cwd=repo.path)) == "deny"


@pytest.mark.parametrize("raw", ['{"tool_name": "Bash", "tool_input": {"command": ""}}', '{"tool_name": "Bash", "tool_input": {"command": "  "}}',
                                  '{"tool_input":{"command":"ls"}}', '{"tool_name": "Read", "tool_input": []}'])
def test_nothing_to_classify_is_defer(raw):
    """Payloads that provably carry no command are DEFER (`{}`)."""
    assert call(None, raw=raw) == {}


@pytest.mark.parametrize("raw", ["", "   ", "not json", "{", "[]", "null", "5", '"str"', "{}", '{"tool_name": "Bash"}',
                                  '{"tool_name": "Bash", "tool_input": null}', '{"tool_name": "Bash", "tool_input": {"command": 5}}',
                                  '{"tool_name": "Bash", "tool_input": []}', '{"tool_name": "Bash", "tool_input": {}}',
                                  '{"tool_input": {"command": "x"}, "tool_name": 7}', "\x00\x01", "[" * 100000,
                                  '{"tool_name": {"a": 1}, "tool_input": {"command": "ls"}}'])
def test_uninterpretable_payload_is_ask_never_silent(raw):
    """Changed from the old 'noop' expectation: a payload that could be a Bash command but cannot be read must ASK."""
    out = call(None, raw=raw)
    assert decision(out) == "ask", out
    hs = out["hookSpecificOutput"]
    assert hs["hookEventName"] == "PreToolUse" and hs["permissionDecisionReason"].startswith("Git Warp")
    assert set(out) == {"hookSpecificOutput"}


def test_oversized_payload_is_ask():
    from gitwarp.hooks import git_guard
    out = call(None, raw='{"tool_name":"Bash","tool_input":{"command":"echo ' + "a" * (git_guard.MAX_PAYLOAD_CHARS + 10) + '"}}')
    assert decision(out) == "ask" and "oversized" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_invalid_utf8_stdin_is_not_a_crash():
    p = subprocess.run([sys.executable, str(HOOK)], input=b'{"tool_name":"Bash","tool_input":{"command":"git reset --hard \\xff"}}',
                       capture_output=True, timeout=30)
    assert p.returncode == 0 and json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] in ("ask", "deny")


@pytest.mark.parametrize("tool", ["Write", "Edit", "Read", "Grep", "mcp__x__y", "bash", ""])
def test_non_bash_tools_are_ignored(tool, tmp_path):
    ev = {"tool_name": tool, "tool_input": {"command": "git reset --hard"}, "cwd": str(tmp_path)}
    assert call(ev) == {}


def test_missing_tool_name_with_command_is_handled(tmp_path):
    ev = {"tool_input": {"command": "git reset --hard"}, "cwd": str(tmp_path)}
    assert decision(call(ev)) == "deny"


def test_non_ascii_and_huge_payloads(tmp_path):
    assert decision(call(event("git commit -m 'é中\U0001f600' && git reset --hard", tmp_path))) == "deny"
    out = call(event("git status " + "a " * 30000, tmp_path))
    assert decision(out) == "ask"
    assert call(event("echo " + "a" * 200000, tmp_path)) == {}


def test_internal_error_asks_when_command_mentions_git(monkeypatch, capsys, tmp_path):
    from gitwarp.hooks import git_guard
    import io

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(git_guard, "classify_command", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event("git reset --hard", tmp_path))))
    assert git_guard.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert decision(out) == "ask" and "guard error" in out["hookSpecificOutput"]["permissionDecisionReason"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event("ls -la", tmp_path))))
    assert git_guard.main() == 0
    assert json.loads(capsys.readouterr().out) == {}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event("GIT_TRACE=1 ls", tmp_path))))
    assert git_guard.main() == 0
    assert decision(json.loads(capsys.readouterr().out)) == "ask"


def test_internal_error_in_git_lookup_degrades_not_crashes(monkeypatch, capsys, tmp_path):
    from gitwarp.hooks import git_guard
    import io

    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(git_guard.git, "repo_root", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event("git push -f", tmp_path))))
    assert git_guard.main() == 0
    assert decision(json.loads(capsys.readouterr().out)) == "ask"


def test_hooks_json_wires_the_guard_for_bash():
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    pre = cfg["hooks"]["PreToolUse"]
    assert any(e.get("matcher") == "Bash" and "hook_git_guard.py" in h["command"] for e in pre for h in e["hooks"])


def test_guard_cli_check(repo):
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "guard", "check", "git reset --hard", "--repo", str(repo.path)],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert data["decision"] == "deny" and data["rule"] == "reset-hard" and data["branch"] == "main"
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "guard", "check", "git push -f", "--branch", "feat", "--mode", "strict",
                        "--protected", "x,y"], capture_output=True, text=True, timeout=30, cwd=repo.path)
    data = json.loads(p.stdout)
    assert data["decision"] == "deny" or data["decision"] == "ask"
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "guard", "check", "git status", "--branch", "feat"],
                       capture_output=True, text=True, timeout=30, cwd=repo.path)
    assert json.loads(p.stdout)["decision"] == "defer"
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "guard"], capture_output=True, text=True, timeout=30)
    assert p.returncode != 0 and "error" in json.loads(p.stdout)
