"""Phase 11/13: guard and hook-payload stress, ported from Disposable (GW-PERF-001.., payload rows of GW-HOOK).

A guard that exceeds the host hook timeout is a FAIL-OPEN: the host then runs the command unguarded.  So the bound is
the ``hooks/hooks.json`` timeout, every destructive row must still be stopped (ask/deny) and never time out.
Commands are text only -- nothing is executed.
"""
from __future__ import annotations

import json
import shlex

import pytest

from tests.acceptance import helpers as h

DESTRUCTIVE = "git reset --hard"
LIMITS = h.hook_timeouts()


def _nest_sh(depth):
    cmd = DESTRUCTIVE
    for _ in range(depth):
        cmd = "sh -c " + shlex.quote(cmd)
    return cmd


GUARD_PERF = [
    ("GW-PERF-001", "100KB padding then destructive", "D", lambda: "echo " + "a" * 100_000 + "; " + DESTRUCTIVE),
    ("GW-PERF-002", "1MB padding then destructive", "D", lambda: "echo " + "a" * 1_000_000 + "; " + DESTRUCTIVE),
    ("GW-PERF-003", "50k repeated 'git ' words", "D", lambda: "git " * 50_000 + "reset --hard"),
    ("GW-PERF-004", "500-deep $( nesting", "D", lambda: "$(" * 500 + DESTRUCTIVE + ")" * 500),
    ("GW-PERF-005", "2000 chained env wrappers", "D", lambda: "env " * 2000 + DESTRUCTIVE),
    ("GW-PERF-006", "nested sh -c depth 7", "D", lambda: _nest_sh(7)),
    ("GW-PERF-007", "nested sh -c depth 8", "D", lambda: _nest_sh(8)),
    ("GW-PERF-008", "100k semicolons then destructive", "D", lambda: ";" * 100_000 + DESTRUCTIVE),
    ("GW-PERF-009", "5000 nested subshell parens", "D", lambda: "(" * 5000 + DESTRUCTIVE + ")" * 5000),
    ("GW-PERF-010", "regex-adversarial: git + 100k a", "N", lambda: "git " + "a" * 100_000 + " "),
    ("GW-PERF-011", "regex-adversarial: 50k ' -' options", "N", lambda: "git" + " -" * 50_000 + " push"),
    ("GW-PERF-012", "regex-adversarial: 100k plus signs", "N", lambda: "git push origin " + "+" * 100_000),
    ("GW-PERF-013", "200k unbalanced double quotes", "N", lambda: '"' * 200_000),
    ("GW-PERF-014", "300k unterminated single-quoted message", "N", lambda: "git commit -m '" + "a" * 300_000),
    ("GW-PERF-015", "20k backslash-escaped git", "D", lambda: "\\" * 20_000 + "git reset --hard"),
    ("GW-PERF-016", "10k heredoc lines then destructive", "D", lambda: "bash <<EOF\n" + "x\n" * 10_000 + DESTRUCTIVE + "\nEOF"),
]


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return h.build_state(tmp_path_factory.mktemp("guard-stress"), "clean").repo


@pytest.mark.parametrize("row", GUARD_PERF, ids=[f"{r[0]}-{r[1]}" for r in GUARD_PERF])
def test_guard_stress_row(repo, row):
    rid, label, code, make = row
    command = make()
    ev = h.hook_event("git_guard", repo, command=command)
    r = h.run_hook("git_guard", repo, event=ev, timeout=LIMITS["git_guard"] * 3)
    assert not r.timed_out, f"{rid}: guard hung ({label})"
    assert r.seconds < LIMITS["git_guard"], f"{rid}: {r.seconds:.1f}s >= hooks.json guard timeout {LIMITS['git_guard']}s: the host would run the command unguarded"
    assert r.code == 0 and not r.traceback, f"{rid}: exit {r.code}: {r.stderr[-200:]}"
    data = json.loads(r.stdout) if r.stdout.strip() else {}
    decision = (data.get("hookSpecificOutput") or {}).get("permissionDecision", "defer")
    assert decision in ("defer", "allow", "ask", "deny")
    if code == "D":
        assert decision in ("ask", "deny"), f"{rid}: destructive command ({label}) was not stopped"


def _padded(name, repo, n):
    ev = h.hook_event(name, repo)
    ev["padding"] = "A" * n
    return json.dumps(ev)


@pytest.mark.parametrize("name", list(h.HOOKS))
@pytest.mark.parametrize("size", [2_000_000, 12_000_000], ids=["2MB", "12MB"])
def test_oversized_payload_is_handled_within_budget(repo, name, size):
    """GW-HOOK oversized-2MB / oversized-12MB: bounded time, exit 0, no traceback, output is empty or one JSON object."""
    r = h.run_hook(name, repo, raw=_padded(name, repo, size), timeout=LIMITS[name] * 3)
    assert not r.timed_out
    assert r.seconds < LIMITS[name], f"{name}: {r.seconds:.1f}s >= hooks.json timeout {LIMITS[name]}s"
    assert r.code == 0 and not r.traceback, r.stderr[-200:]
    if r.stdout.strip().startswith("{"):
        assert isinstance(json.loads(r.stdout), dict)


@pytest.mark.parametrize("name", list(h.HOOKS))
def test_binary_garbage_payload_is_handled(repo, name):
    """GW-PERF payload row: 5MB of non-UTF-8 garbage on stdin."""
    raw = b"\xff\xfe{" + b"\x00garbage" * 700_000
    import subprocess
    import sys
    p = subprocess.run([sys.executable, str(h.SCRIPTS / f"hook_{name}.py")], input=raw, cwd=str(repo), env=h.clean_env(),
                       capture_output=True, timeout=LIMITS[name] * 3)
    assert p.returncode == 0
    assert b"Traceback" not in p.stderr
    if name == "git_guard":
        out = p.stdout.decode("utf-8", "replace")
        decision = (json.loads(out).get("hookSpecificOutput") or {}).get("permissionDecision", "defer") if out.strip() else "defer"
        assert decision in ("ask", "deny"), "an uninterpretable payload must not be silently allowed by the guard"
