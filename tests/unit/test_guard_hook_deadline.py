"""M3 / H1: the guard hook must never outlive the host's 10 s timeout: short git lookups, internal wall-clock deadline."""
import io
import json
import sys
import time

import pytest

from gitwarp.core import git
from gitwarp.hooks import git_guard
from gitwarp.safety.classifier import Verdict


def run_main(monkeypatch, capsys, command, cwd):
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    code = git_guard.main()
    out = capsys.readouterr().out
    assert code == 0
    return json.loads(out)


def decision(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


def test_budgets_fit_inside_the_host_timeout():
    assert git_guard.GIT_LOOKUP_TIMEOUT_S <= 2.0
    assert 2 * git_guard.GIT_LOOKUP_TIMEOUT_S < git_guard.GUARD_DEADLINE_S < 10.0


def test_git_lookups_use_short_timeouts(monkeypatch, capsys, tmp_path):
    seen = []

    def fake_run(args, cwd=None, timeout=git.DEFAULT_TIMEOUT, **kw):
        seen.append(timeout)
        raise git.GitTimeout("slow")

    monkeypatch.setattr(git, "run", fake_run)
    out = run_main(monkeypatch, capsys, "git reset --hard", tmp_path)
    assert decision(out) == "deny"                       # still classified
    assert len(seen) == 2 and all(t <= 2.0 for t in seen), seen


def test_git_timeout_falls_back_to_default_config_and_unknown_branch(monkeypatch, capsys, tmp_path):
    def fake_run(*a, **k):
        raise git.GitTimeout("slow")

    monkeypatch.setattr(git, "run", fake_run)
    assert decision(run_main(monkeypatch, capsys, "git clean -fd", tmp_path)) == "deny"
    # branch unknown -> a force push without a target is conservatively asked about
    assert decision(run_main(monkeypatch, capsys, "git push --force", tmp_path)) in ("ask", "deny")
    assert run_main(monkeypatch, capsys, "git status", tmp_path) == {}


def test_git_error_in_lookup_still_classifies(monkeypatch, capsys, tmp_path):
    def fake_run(*a, **k):
        raise git.GitError("boom")

    monkeypatch.setattr(git, "run", fake_run)
    assert decision(run_main(monkeypatch, capsys, "git reset --hard", tmp_path)) == "deny"


def test_wall_clock_deadline_emits_ask_instead_of_hanging(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(git_guard, "GUARD_DEADLINE_S", 0.3)

    def slow_classify(*a, **k):
        for _ in range(200):
            time.sleep(0.05)

    monkeypatch.setattr(git_guard, "classify_command", slow_classify)
    t0 = time.monotonic()
    out = run_main(monkeypatch, capsys, "git status", tmp_path)
    assert time.monotonic() - t0 < 3.0
    assert decision(out) == "ask"
    assert "guard timed out" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_deadline_covers_slow_git_lookups(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(git_guard, "GUARD_DEADLINE_S", 0.3)

    def slow_run(*a, **k):
        time.sleep(5)

    monkeypatch.setattr(git, "run", slow_run)
    t0 = time.monotonic()
    out = run_main(monkeypatch, capsys, "git status", tmp_path)
    assert time.monotonic() - t0 < 3.0
    assert "guard timed out" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_monotonic_deadline_works_without_signal_timers(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(git_guard, "_arm", lambda seconds: False)
    monkeypatch.setattr(git_guard, "GUARD_DEADLINE_S", 0.2)

    def slow_classify(*a, **k):
        time.sleep(0.5)
        return Verdict("defer")

    monkeypatch.setattr(git_guard, "classify_command", slow_classify)
    out = run_main(monkeypatch, capsys, "git status", tmp_path)
    assert decision(out) == "ask" and "guard timed out" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_timer_is_disarmed_after_normal_run(monkeypatch, capsys, tmp_path):
    import signal
    assert run_main(monkeypatch, capsys, "ls", tmp_path) == {}
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
    assert decision(run_main(monkeypatch, capsys, "git reset --hard", tmp_path)) == "deny"
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


@pytest.mark.parametrize("pad", ["a." * 9000, "a-" * 9000])
def test_padded_command_through_hook_entry_point_is_fast_and_denied(monkeypatch, capsys, tmp_path, pad):
    t0 = time.monotonic()
    out = run_main(monkeypatch, capsys, "git reset --hard " + pad, tmp_path)
    assert time.monotonic() - t0 < 4.0
    assert decision(out) == "deny"
