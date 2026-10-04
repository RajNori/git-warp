"""Repository-state matrix (Disposable GW-STATE-001..021): every hook and every ``warp.py`` command runs against every
one of the 21 repository states.  Contracts, per state:

1. no crash       -- no Python traceback, no timeout, sane exit code;
2. valid output   -- CLI stdout is a JSON object (errors included), hook stdout is empty / JSON / (SessionStart) text;
3. no mutation    -- HEAD, refs, index entries, tracked-file hashes, untracked set, stash, worktree list, config and the
                     git-dir file set are identical before and after EVERY operation (``.git/git-warp`` excluded).

All states are built and exercised once, in parallel, by a session fixture; each state then has three assertions.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.acceptance import helpers as h

HOOK_LIMITS = h.hook_timeouts()


def _operations(fx):
    t = fx.target
    ops = []
    for name in ("session_start", "git_guard", "stop"):
        ops.append((f"hook:{name}", "hook", name, lambda n=name: h.run_hook(n, t)))
    ops.append(("hook:post_tool(Bash)", "hook", "post_tool", lambda: h.run_hook("post_tool", t)))
    ops.append(("hook:post_tool(Write)", "hook", "post_tool", lambda: h.run_hook(
        "post_tool", t, event=h.hook_event("post_tool", t, tool="Write", file_path=str(t / "README.md")))))
    ops.append(("cli:" + h.GUARD_CLI[0], "cli", None, lambda: h.run_warp(t, *h.GUARD_CLI[1])))
    for name, args in h.warp_battery():
        ops.append(("cli:" + name, "cli", None, lambda a=args: h.run_warp(t, *a)))
    return ops


def _exercise(root, state):
    fx = h.build_state(root, state)
    out = {}
    for name, kind, hook, thunk in _operations(fx):
        before = h.snapshot(fx.target)
        r = thunk()
        after = h.snapshot(fx.target)
        out[name] = {"kind": kind, "hook": hook, "run": r, "mutated": h.diff(before, after)}
    return out


@pytest.fixture(scope="session")
def matrix(tmp_path_factory):
    base = tmp_path_factory.mktemp("state-matrix")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {s: ex.submit(_exercise, base / s, s) for s in h.STATES}
        return {s: f.result() for s, f in futs.items()}


def test_matrix_covers_21_states_and_every_command():
    assert len(h.STATES) == 21
    covered = {a[0] for _, a in h.warp_battery()} | {"guard"}
    assert covered >= {"xray", "pr", "blast", "commits", "conflict", "temporal", "rescue", "archaeology", "bisect", "memory", "guard"}


@pytest.mark.parametrize("state", h.STATES)
def test_no_crash_or_timeout(matrix, state):
    """GW-STATE: no traceback, no timeout; hooks exit 0 inside their hooks.json budget; CLI exits 0/1/2."""
    bad = []
    for name, o in matrix[state].items():
        r = o["run"]
        if r.timed_out:
            bad.append(f"{name}: TIMEOUT")
        elif r.traceback:
            bad.append(f"{name}: traceback: {r.stderr[-200:]!r}")
        elif o["kind"] == "hook" and r.code != 0:
            bad.append(f"{name}: hook exit {r.code}")
        elif o["kind"] == "cli" and r.code not in (0, 1, 2):
            bad.append(f"{name}: exit {r.code}")
        elif o["kind"] == "hook" and r.seconds > HOOK_LIMITS[o["hook"]]:
            bad.append(f"{name}: {r.seconds:.1f}s exceeds hooks.json timeout {HOOK_LIMITS[o['hook']]}s")
    assert bad == []


@pytest.mark.parametrize("state", h.STATES)
def test_output_is_valid(matrix, state):
    """CLI stdout is always one JSON object (errors included); hook stdout is empty or a JSON object."""
    bad = []
    for name, o in matrix[state].items():
        r = o["run"]
        text = r.stdout.strip()
        if o["kind"] == "cli":
            try:
                data = json.loads(text)
            except ValueError:
                bad.append(f"{name}: stdout is not JSON: {text[:120]!r}")
                continue
            if not isinstance(data, dict):
                bad.append(f"{name}: JSON is not an object")
        elif text:
            try:
                data = json.loads(text)
            except ValueError:
                if o["hook"] != "session_start":
                    bad.append(f"{name}: hook stdout is not JSON: {text[:120]!r}")
                continue
            if not isinstance(data, dict):
                bad.append(f"{name}: hook JSON is not an object")
    assert bad == []


@pytest.mark.parametrize("state", h.STATES)
def test_no_repository_mutation(matrix, state):
    """GW-STATE: no operation changes HEAD/refs/index/files/stash/worktrees/config (only .git/git-warp may change)."""
    mutated = {name: o["mutated"] for name, o in matrix[state].items() if o["mutated"]}
    assert mutated == {}


@pytest.mark.parametrize("state", [s for s in h.STATES if s not in ("unborn",)])
def test_core_analysis_actually_works_on_populated_states(matrix, state):
    """Guard against vacuous passes: on every repository that has history, xray/commits/temporal/blast return data
    (no ``error`` key), the SessionStart hook produces context, and memory index reports an index."""
    for name in ("cli:xray", "cli:commits", "cli:temporal", "cli:blast", "cli:memory-index"):
        data = json.loads(matrix[state][name]["run"].stdout)
        assert not data.get("error"), (state, name, data.get("error"))
    assert matrix[state]["hook:session_start"]["run"].stdout.strip(), "SessionStart produced no context"
