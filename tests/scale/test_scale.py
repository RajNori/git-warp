"""Phase 13: bounded scale test, above the Disposable harness (~1500 commits / ~5300 files).

One synthetic repository is built with ``git fast-import``:

* 6000 linear commits touching 400 distinct tracked files across the history;
* a working tree with ~8000 untracked files (80 directories x 100) and all 400 tracked files modified.

Every hook and every ``warp.py`` command is then run, sequentially (so timings are not distorted by contention), under
a supervisor that measures wall time and peak RSS.  Bounds: hooks use their ``hooks/hooks.json`` timeout; CLI commands
use ``CLI_BOUND_S``.  Contracts asserted: no timeout, no traceback, valid output, bounded output size, bounded memory,
no repository mutation, and an HONEST degradation mode (a command that does not cover the whole repository must say
so with a ``partial`` / ``truncated`` / ``complete: false`` style flag or a warning, rather than silently returning a
subset).

A human-readable table of the measurements is printed (``pytest -s``) and written to ``scale-results.md`` under the
pytest tmp dir; ``planning/SCALE_RESULTS.md`` records one real run.  Nothing here claims the tool works on every
monorepo -- only what was measured on this synthetic repository.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.acceptance import helpers as h

COMMITS = 6000
TRACKED_DISTINCT = 400            # 320 src modules + 80 docs
UNTRACKED_DIRS, UNTRACKED_PER_DIR = 80, 100
CLI_BOUND_S = 120                 # warp.py has no hooks.json timeout; interactive-use ceiling
HOOK_OUT_MAX = 64 * 1024          # hook stdout is injected into the model context: must stay small
CLI_OUT_MAX = 2 * 1024 * 1024
RSS_MAX_MB = 1024
FLAG_KEYS = ("partial", "truncated", "complete", "degraded", "capped", "limit_reached", "timed_out", "skipped")


# --------------------------------------------------------------------------- the repository

@pytest.fixture(scope="module")
def scale_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("scale")
    fx = h.new_fixture(root / "fx", "scale")
    fx.g("init", "-q", "-b", "main")
    t0 = time.monotonic()
    h.fast_import_history(fx.repo, COMMITS, files_per_commit=2, distinct_files=320)
    build_s = time.monotonic() - t0
    tracked = [p for p in h.git_out(fx.repo, "ls-files").splitlines()]
    assert len(tracked) == TRACKED_DISTINCT, len(tracked)
    assert int(h.git_out(fx.repo, "rev-list", "--count", "HEAD")) == COMMITS
    for rel in tracked:                                   # 400 modified tracked files
        (fx.repo / rel).write_text("modified in the working tree\n" * 3)
    for d in range(UNTRACKED_DIRS):                       # ~8000 untracked files
        dd = fx.repo / "scratch" / f"d{d:03d}"
        dd.mkdir(parents=True)
        for f in range(UNTRACKED_PER_DIR):
            (dd / f"f{f:03d}.txt").write_text(f"{d}:{f}\n")
    st = h.git(fx.repo, "status", "--porcelain").stdout   # not git_out: stripping would eat the first line's leading space
    n_mod = sum(1 for ln in st.splitlines() if ln.startswith(" M"))
    n_unt = sum(1 for ln in st.splitlines() if ln.startswith("??"))
    # untracked dirs are collapsed by plain status; count real files
    n_unt_files = int(h.git_out(fx.repo, "ls-files", "--others", "--exclude-standard").count("\n") + 1)
    assert n_mod == TRACKED_DISTINCT and n_unt_files == UNTRACKED_DIRS * UNTRACKED_PER_DIR
    fx.meta.update(build_seconds=build_s, tracked=len(tracked), modified=n_mod, untracked=n_unt_files)
    return fx


# --------------------------------------------------------------------------- measurement

def _flags(obj, depth=0):
    """Degradation indicators anywhere in the first 3 levels of the JSON output."""
    found = {}
    if isinstance(obj, dict) and depth < 3:
        for k, v in obj.items():
            if k in FLAG_KEYS and isinstance(v, (bool, str, int)):
                found[k] = v
            elif k == "mode" and isinstance(v, str):
                found["mode"] = v
            elif isinstance(v, (dict, list)):
                for kk, vv in _flags(v, depth + 1).items():
                    found.setdefault(f"{k}.{kk}", vv)
    elif isinstance(obj, list) and depth < 3:
        for it in obj[:3]:
            for kk, vv in _flags(it, depth + 1).items():
                found.setdefault(kk, vv)
    return found


def _ops(repo):
    ops = []
    limits = h.hook_timeouts()
    for name in ("session_start", "git_guard", "post_tool", "stop"):
        key = {"git_guard": "git_guard"}.get(name, name)
        ops.append((f"hook:{name}", "hook", limits[key], [h.sys.executable, str(h.SCRIPTS / f"hook_{name}.py")], json.dumps(h.hook_event(name, repo))))
    ops.append(("hook:post_tool(Write)", "hook", limits["post_tool"], [h.sys.executable, str(h.SCRIPTS / "hook_post_tool.py")],
                json.dumps(h.hook_event("post_tool", repo, tool="Write", file_path=str(repo / "src" / "mod1.py")))))
    ops.append(("cli:" + h.GUARD_CLI[0], "cli", CLI_BOUND_S, [h.sys.executable, str(h.WARP), *h.GUARD_CLI[1], "--repo", str(repo)], None))
    sample = "src/mod7.py"
    for name, args in h.warp_battery():
        a = ["archaeology", sample] if name == "archaeology" else \
            ["memory", "cochange", sample] if name == "memory-cochange" else \
            ["memory", "introduced", sample] if name == "memory-introduced" else \
            ["memory", "authors", sample] if name == "memory-authors" else \
            ["rescue", "inspect", "HEAD"] if name == "rescue-inspect" else \
            ["bisect", "plan", "--good=HEAD~4000", "--bad=HEAD"] if name == "bisect-plan" else \
            ["pr", "--base=HEAD~500"] if name == "pr" else args
        ops.append(("cli:" + name, "cli", CLI_BOUND_S, [h.sys.executable, str(h.WARP), *a, "--repo", str(repo)], None))
    return ops


@pytest.fixture(scope="module")
def measurements(scale_repo, tmp_path_factory):
    repo = scale_repo.repo
    work = tmp_path_factory.mktemp("scale-measure")
    before = h.snapshot(repo)
    rows = []
    for name, kind, bound, argv, payload in _ops(repo):
        r = h.run_measured(argv, cwd=repo, input=payload, timeout=bound, workdir=work)
        flags, out_bytes = {}, len(r.stdout.encode())
        parsed = None
        try:
            parsed = json.loads(r.stdout) if r.stdout.strip() else None
            flags = _flags(parsed) if parsed is not None else {}
        except ValueError:
            pass
        warns = len(parsed.get("warnings", [])) if isinstance(parsed, dict) and isinstance(parsed.get("warnings"), list) else 0
        rows.append({"name": name, "kind": kind, "bound": bound, "run": r, "seconds": r.seconds,
                     "rss_mb": (r.max_rss_kb or 0) / 1024, "out_bytes": out_bytes, "flags": flags, "warnings": warns,
                     "parsed": parsed})
    after = h.snapshot(repo)
    table = _render(scale_repo, rows)
    (work / "scale-results.md").write_text(table)
    print("\n" + table)
    return {"rows": {r["name"]: r for r in rows}, "mutated": h.diff(before, after), "table": table}


def _render(fx, rows) -> str:
    m = fx.meta
    out = [f"Scale run: {COMMITS} commits, {m['tracked']} tracked files touched across history, "
           f"{m['modified']} modified + {m['untracked']} untracked files in the working tree "
           f"(repository built in {m['build_seconds']:.1f}s via git fast-import).", "",
           "| operation | bound s | time s | peak RSS MB | stdout bytes | exit | degradation signals |",
           "|---|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        sig = ", ".join(f"{k}={v}" for k, v in sorted(r["flags"].items())) or "-"
        if r["warnings"]:
            sig += f" (warnings: {r['warnings']})"
        out.append(f"| {r['name']} | {r['bound']} | {r['seconds']:.2f} | {r['rss_mb']:.0f} | {r['out_bytes']} | "
                   f"{r['run'].code if not r['run'].timed_out else 'TIMEOUT'} | {sig} |")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- assertions

def _names():
    # static list so the parametrization does not need the (expensive) fixture at collection time
    names = ["hook:session_start", "hook:git_guard", "hook:post_tool", "hook:stop", "hook:post_tool(Write)", "cli:guard-check"]
    names += ["cli:" + n for n, _ in h.warp_battery()]
    return names


def test_repository_has_the_advertised_shape(scale_repo):
    m = scale_repo.meta
    assert m["modified"] == 400 and m["untracked"] == 8000 and m["tracked"] == 400


@pytest.mark.parametrize("op", _names())
def test_no_timeout_no_crash_within_bound(measurements, op):
    r = measurements["rows"][op]
    run = r["run"]
    assert not run.timed_out, f"{op}: exceeded its {r['bound']}s bound"
    assert not run.traceback, run.stderr[-300:]
    assert r["seconds"] < r["bound"], f"{op}: {r['seconds']:.1f}s >= bound {r['bound']}s"
    if r["kind"] == "hook":
        assert run.code == 0
    else:
        assert run.code in (0, 1, 2)


@pytest.mark.parametrize("op", _names())
def test_output_and_memory_are_bounded(measurements, op):
    r = measurements["rows"][op]
    limit = HOOK_OUT_MAX if r["kind"] == "hook" else CLI_OUT_MAX
    assert r["out_bytes"] <= limit, f"{op}: stdout {r['out_bytes']} bytes > {limit}"
    assert r["rss_mb"] < RSS_MAX_MB, f"{op}: peak RSS {r['rss_mb']:.0f} MB"
    if r["kind"] == "cli":
        assert isinstance(r["parsed"], dict), f"{op}: stdout is not a JSON object"
    elif r["out_bytes"]:
        text = r["run"].stdout.strip()
        if text.startswith("{"):
            assert isinstance(json.loads(text), dict)


def test_scale_run_did_not_mutate_the_repository(measurements):
    assert measurements["mutated"] == []


def test_degradation_is_reported_not_silent(measurements):
    """Honest degradation: whenever analysis was bounded (index capped below the history, truncated listings) the
    output must say so.  The automatic index is documented to cap at AUTO_CAP commits (< 6000)."""
    rows = measurements["rows"]
    idx = rows["cli:memory-index"]["parsed"]
    assert isinstance(idx, dict) and not idx.get("error"), idx
    indexed = idx.get("total_commits")
    if indexed is not None and indexed < COMMITS:
        assert idx.get("complete") is False or any("partial" in str(w).lower() or "only" in str(w).lower() for w in idx.get("warnings", [])), \
            f"index covers {indexed}/{COMMITS} commits but does not flag itself partial"
    # every CLI command either returns data or a structured error -- never an empty body
    for name, r in rows.items():
        if r["kind"] == "cli":
            assert r["out_bytes"] > 0, f"{name}: empty output"


def test_session_start_context_stays_small(measurements):
    """The SessionStart payload is model context: it must not grow with repository size (8000 untracked files)."""
    r = measurements["rows"]["hook:session_start"]
    assert r["out_bytes"] <= 16 * 1024, r["out_bytes"]
    assert r["out_bytes"] > 0


def test_guard_hook_is_fast_at_scale(measurements):
    """PreToolUse sits on the critical path of every Bash call; a slow guard is a fail-open risk (host timeout)."""
    assert measurements["rows"]["hook:git_guard"]["seconds"] < measurements["rows"]["hook:git_guard"]["bound"] / 2
