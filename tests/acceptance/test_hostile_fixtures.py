"""Hostile repositories and hostile ambient environment.

A repository (or the process environment) is attacker-influenced input.  Every vector below installs a *marker*
command that appends a line to a log OUTSIDE the repository, then drives every hook and every ``warp.py`` command.
Contract: no marker may fire -- Git Warp must never cause Git to execute repository-controlled configuration.

Each vector also has a *control* run with plain ``git`` proving the vector is effective in this environment (a
vector that cannot fire would make the test vacuous, which is a bug in the test, so the control is asserted).

Disposable cross-references: GW-CONFIG-001..011 (config vectors), GW-ENV-001..012 (environment vectors).
Additional vectors beyond Disposable: clean/smudge filters, ``include.path``, ``diff.<driver>.command``,
``core.sshCommand``, direct ``pager.<cmd>=<command>``, GIT_PAGER / PAGER / GIT_TRACE* environment variables.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.acceptance import helpers as h

ATTRS = "*.py diff=bk filter=bk\n*.md filter=bk\n"
HOOK_NAMES = ("post-index-change", "reference-transaction", "pre-commit", "post-commit", "post-checkout", "post-merge",
              "pre-push", "pre-rebase", "post-rewrite", "prepare-commit-msg", "commit-msg", "fsmonitor-watchman")


def _hostile_base(root):
    """A committed history with a .gitattributes, then staged + unstaged + untracked changes (every diff path is live)."""
    fx = h.new_fixture(root, "hostile")
    h._base(fx)
    fx.write(".gitattributes", ATTRS)
    fx.commit("chore: attributes", 9)
    fx.write("src/util.py", "def add(a, b):\n    return a + b  # staged edit\n")
    fx.g("add", "src/util.py")
    fx.write("src/app.py", "def main():\n    return 7  # unstaged edit\n")
    fx.write("README.md", "# demo edited\n")
    fx.write("notes/todo.txt", "untracked\n")
    return fx


# --------------------------------------------------------------------------- config vectors

def _v_fsmonitor(fx):
    fx.g("config", "core.fsmonitor", str(fx.marker_script("fsmonitor")))


def _v_external(fx):
    fx.g("config", "diff.external", str(fx.marker_script("external")))


def _v_diff_command(fx):
    fx.g("config", "diff.bk.command", str(fx.marker_script("diffcommand")))


def _v_textconv(fx):
    fx.g("config", "diff.bk.textconv", str(fx.marker_script("textconv", passthrough=True)))


def _v_pager_core(fx):
    fx.g("config", "core.pager", str(fx.marker_script("pager-core", stdin_passthrough=True)))


def _v_pager_cmds(fx):
    for cmd in ("diff", "log", "show"):
        fx.g("config", f"pager.{cmd}", "true")
    fx.g("config", "core.pager", str(fx.marker_script("pager-cmds", stdin_passthrough=True)))


def _v_pager_direct(fx):
    s = str(fx.marker_script("pager-direct", stdin_passthrough=True))
    for cmd in ("diff", "log", "show", "status", "branch"):
        fx.g("config", f"pager.{cmd}", s)


def _v_hookspath(fx):
    hooks = fx.markers / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in HOOK_NAMES:
        p = hooks / name
        p.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "{name} $*" >> "{fx.markers / ("hook-" + name + ".log")}"\nexit 0\n')
        p.chmod(0o755)
    fx.g("config", "core.hooksPath", str(hooks))


def _v_alias(fx):
    fx.g("config", "alias.st", "!" + str(fx.marker_script("alias-shell")))
    fx.g("config", "alias.x", "reset --hard")
    fx.g("config", "alias.nuke", "clean -fdx")


def _v_filter_clean(fx):
    fx.g("config", "filter.bk.clean", str(fx.marker_script("filter-clean", stdin_passthrough=True)) + " %f")


def _v_filter_smudge(fx):
    fx.g("config", "filter.bk.smudge", str(fx.marker_script("filter-smudge", stdin_passthrough=True)) + " %f")


def _v_include(fx):
    inc = fx.outside / "extra.cfg"
    inc.write_text(f"[core]\n\tfsmonitor = {fx.marker_script('include-fsmonitor')}\n")
    fx.g("config", "include.path", str(inc))


def _v_sshcommand(fx):
    fx.g("config", "core.sshCommand", str(fx.marker_script("sshcommand")))
    fx.g("config", "remote.origin.url", "ssh://example.invalid/never.git")


def _plain(*cmds):
    return [list(c) for c in cmds]


# name -> (installer, pty?, control commands, extra guard commands)
CONFIG_VECTORS = {
    "fsmonitor": (_v_fsmonitor, False, _plain(["status"], ["diff"]), ()),
    "diff.external": (_v_external, False, _plain(["diff"]), ()),
    "diff.driver.command": (_v_diff_command, False, _plain(["diff"]), ()),
    "textconv": (_v_textconv, False, _plain(["diff"], ["log", "-p", "-1", "--", "src/app.py"]), ()),
    "core.pager(pty)": (_v_pager_core, True, _plain(["diff"], ["log", "-1"], ["show", "HEAD"]), ()),
    "pager.<cmd>=true(pty)": (_v_pager_cmds, True, _plain(["diff"], ["log", "-1"], ["show", "HEAD"]), ()),
    "pager.<cmd>=command(pty)": (_v_pager_direct, True, _plain(["diff"], ["log", "-1"], ["show", "HEAD"]), ()),
    "core.hooksPath": (_v_hookspath, False, _plain(["commit", "--allow-empty", "-q", "-m", "ctl"], ["checkout", "-q", "feature/test"]), ()),
    "aliases": (_v_alias, False, _plain(["st"]), ("git st", "git x", "git nuke", "git st --porcelain")),
    "filter.clean": (_v_filter_clean, False, _plain(["add", "src/app.py"]), ()),
    "filter.smudge": (_v_filter_smudge, False, _plain(["checkout", "HEAD", "--", "src/app.py"]), ()),
    "include.path": (_v_include, False, _plain(["status"]), ()),
    "core.sshCommand": (_v_sshcommand, False, _plain(["ls-remote", "origin"]), ()),
}


def _operations(fx, pty=False, env=None, extra_guard=()):
    t = fx.repo
    ops = []
    for name in ("session_start", "stop"):
        ops.append((f"hook:{name}", lambda n=name: h.run_hook(n, t, env=env, pty=pty)))
    for cmd in ("git status", *extra_guard):
        ops.append((f"hook:git_guard[{cmd}]", lambda c=cmd: h.run_hook("git_guard", t, event=h.hook_event("git_guard", t, command=c), env=env, pty=pty)))
    ops.append(("hook:post_tool(Bash)", lambda: h.run_hook("post_tool", t, env=env, pty=pty)))
    ops.append(("hook:post_tool(Write)", lambda: h.run_hook(
        "post_tool", t, event=h.hook_event("post_tool", t, tool="Write", file_path=str(t / "src" / "app.py")), env=env, pty=pty)))
    ops.append(("cli:guard-check", lambda: h.run_warp(t, *h.GUARD_CLI[1], env=env, pty=pty)))
    for name, args in h.warp_battery():
        ops.append(("cli:" + name, lambda a=args: h.run_warp(t, *a, env=env, pty=pty)))
    return ops


def _clear(fx):
    for p in fx.markers.glob("*.log"):
        p.unlink()


def _run_ops(fx, ops):
    results = {}
    for name, thunk in ops:
        _clear(fx)
        before = h.snapshot(fx.repo)
        r = thunk()
        results[name] = {"fired": sorted(fx.fired()), "run": r, "mutated": h.diff(before, h.snapshot(fx.repo))}
    return results


def _control_config(root, installer, pty, cmds):
    fx = _hostile_base(root)
    installer(fx)
    _clear(fx)
    for c in cmds:
        h.run_pty([h.REAL_GIT, *c], cwd=fx.repo, timeout=30) if pty else h._run([h.REAL_GIT, *c], cwd=fx.repo, timeout=30)
    return sorted(fx.fired())


def _exercise_config(root, name):
    installer, pty, control_cmds, extra = CONFIG_VECTORS[name]
    control = _control_config(root / "control", installer, pty, control_cmds)
    fx = _hostile_base(root / "run")
    installer(fx)
    return {"control": control, "ops": _run_ops(fx, _operations(fx, pty=pty, extra_guard=extra))}


@pytest.fixture(scope="session")
def config_results(tmp_path_factory):
    base = tmp_path_factory.mktemp("hostile-config")
    safe = {n: re.sub(r"[^A-Za-z0-9]+", "_", n) for n in CONFIG_VECTORS}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {n: ex.submit(_exercise_config, base / safe[n], n) for n in CONFIG_VECTORS}
        return {n: f.result() for n, f in futs.items()}


@pytest.mark.parametrize("vector", list(CONFIG_VECTORS))
def test_config_vector_is_effective_in_control(config_results, vector):
    """Vacuity guard: plain git with this configuration DOES execute the marker here."""
    assert config_results[vector]["control"], f"control: plain git did not fire the {vector} marker (test is vacuous)"


@pytest.mark.parametrize("vector", list(CONFIG_VECTORS))
def test_no_repo_config_command_runs(config_results, vector):
    """GW-CONFIG-001..011: no hook and no warp.py command makes Git execute repository-controlled config."""
    fired = {n: o["fired"] for n, o in config_results[vector]["ops"].items() if o["fired"]}
    assert fired == {}, f"{vector}: marker(s) executed during {sorted(fired)}: {fired}"


@pytest.mark.parametrize("vector", list(CONFIG_VECTORS))
def test_hostile_config_run_is_clean(config_results, vector):
    """Under every hostile configuration: no traceback / timeout, hooks exit 0, and the repository is not mutated."""
    bad = []
    for name, o in config_results[vector]["ops"].items():
        r = o["run"]
        if r.timed_out or r.traceback:
            bad.append(f"{name}: {'timeout' if r.timed_out else 'traceback ' + r.stderr[-160:]}")
        elif name.startswith("hook:") and r.code != 0:
            bad.append(f"{name}: exit {r.code}")
        if o["mutated"]:
            bad.append(f"{name}: mutated {o['mutated']}")
    assert bad == []


def test_alias_guard_never_executes_alias_and_flags_destructive_alias(config_results):
    """GW-CONFIG-010/011: the guard classifies `git x` (alias for reset --hard) but never runs repo aliases."""
    ops = config_results["aliases"]["ops"]
    assert ops["hook:git_guard[git st]"]["fired"] == []
    r = ops["hook:git_guard[git x]"]["run"]
    decision = (json.loads(r.stdout).get("hookSpecificOutput") or {}).get("permissionDecision", "defer") if r.stdout.strip() else "defer"
    assert decision in ("ask", "deny"), "alias.x = 'reset --hard' must not be treated as a safe command"


# --------------------------------------------------------------------------- environment vectors

TRACE_TS = re.compile(r"\b\d\d:\d\d:\d\d\.\d{6}\b")


def _decoy(fx):
    d = fx.outside / "decoy"
    d.mkdir()
    h.git(d, "init", "-q", "-b", "decoy-branch")
    (d / "decoy.txt").write_text("decoy\n")
    h.git(d, "add", "-A")
    h.git(d, "commit", "-q", "-m", "decoy commit")
    return d


def _env_spec(var, fx):
    """-> (env, pty, control argv list, control effect predicate)"""
    out = fx.outside
    ctl_fired = lambda: bool(fx.fired())
    if var == "GIT_DIR":
        d = _decoy(fx)
        return {"GIT_DIR": str(d / ".git")}, False, [["log", "--oneline"]], lambda: True
    if var == "GIT_WORK_TREE":
        d = _decoy(fx)
        return {"GIT_WORK_TREE": str(d)}, False, [["status", "--short"]], lambda: True
    if var == "GIT_INDEX_FILE":
        return {"GIT_INDEX_FILE": str(out / "hostile-index")}, False, [["read-tree", "HEAD"]], lambda: (out / "hostile-index").exists()
    if var == "GIT_OBJECT_DIRECTORY":
        (out / "objs").mkdir()
        return {"GIT_OBJECT_DIRECTORY": str(out / "objs")}, False, [["status"]], lambda: True
    if var == "GIT_ALTERNATE_OBJECT_DIRECTORIES":
        (out / "alt").mkdir()
        return {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(out / "alt")}, False, [["status"]], lambda: True
    if var == "GIT_EXTERNAL_DIFF":
        return {"GIT_EXTERNAL_DIFF": str(fx.marker_script("env-external"))}, False, [["diff"]], ctl_fired
    if var == "GIT_DIFF_OPTS":
        return {"GIT_DIFF_OPTS": "--unified=0"}, False, [["diff"]], lambda: True
    if var == "GIT_SSH":
        return {"GIT_SSH": str(fx.marker_script("env-ssh"))}, False, [["ls-remote", "ssh://example.invalid/x.git"]], ctl_fired
    if var == "GIT_SSH_COMMAND":
        return {"GIT_SSH_COMMAND": str(fx.marker_script("env-sshcmd"))}, False, [["ls-remote", "ssh://example.invalid/x.git"]], ctl_fired
    if var == "GIT_CONFIG_GLOBAL":
        cfg = out / "global.cfg"
        cfg.write_text(f"[core]\n\tfsmonitor = {fx.marker_script('env-cfg-global')}\n[user]\n\tname = x\n\temail = x@example.invalid\n")
        return {"GIT_CONFIG_GLOBAL": str(cfg)}, False, [["status"]], ctl_fired
    if var == "GIT_CONFIG_SYSTEM":
        cfg = out / "system.cfg"
        cfg.write_text(f"[core]\n\tfsmonitor = {fx.marker_script('env-cfg-system')}\n")
        return {"GIT_CONFIG_SYSTEM": str(cfg), "GIT_CONFIG_NOSYSTEM": None}, False, [["status"]], ctl_fired
    if var == "GIT_CONFIG_COUNT":
        s = fx.marker_script("env-cfg-count")
        return ({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": str(s)},
                False, [["status"]], ctl_fired)
    if var == "GIT_PAGER":
        return {"GIT_PAGER": str(fx.marker_script("env-gitpager", stdin_passthrough=True))}, True, [["diff"], ["log", "-1"]], ctl_fired
    if var == "PAGER":
        return ({"PAGER": str(fx.marker_script("env-pager", stdin_passthrough=True)), "GIT_PAGER": None},
                True, [["diff"], ["log", "-1"]], ctl_fired)
    if var == "GIT_TRACE=1":
        return {"GIT_TRACE": "1"}, False, [["status"]], lambda: True
    if var == "GIT_TRACE=file":
        return {"GIT_TRACE": str(out / "trace.log")}, False, [["status"]], lambda: (out / "trace.log").exists()
    if var == "GIT_TRACE2=file":
        return {"GIT_TRACE2": str(out / "trace2.log")}, False, [["status"]], lambda: (out / "trace2.log").exists()
    if var == "GIT_TRACE2_EVENT=file":
        return {"GIT_TRACE2_EVENT": str(out / "trace2e.log")}, False, [["status"]], lambda: (out / "trace2e.log").exists()
    if var == "GIT_TRACE_PERFORMANCE=file":
        return {"GIT_TRACE_PERFORMANCE": str(out / "perf.log")}, False, [["status"]], lambda: (out / "perf.log").exists()
    if var == "GIT_TRACE_SETUP=file":
        return {"GIT_TRACE_SETUP": str(out / "setup.log")}, False, [["status"]], lambda: (out / "setup.log").exists()
    raise KeyError(var)


ENV_VARS = ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_SSH",
            "GIT_SSH_COMMAND", "GIT_PAGER", "PAGER", "GIT_TRACE=1", "GIT_TRACE=file", "GIT_TRACE2=file",
            "GIT_TRACE2_EVENT=file", "GIT_TRACE_PERFORMANCE=file", "GIT_TRACE_SETUP=file"]
TRACE_FILES = {"GIT_TRACE=file": "trace.log", "GIT_TRACE2=file": "trace2.log", "GIT_TRACE2_EVENT=file": "trace2e.log",
               "GIT_TRACE_PERFORMANCE=file": "perf.log", "GIT_TRACE_SETUP=file": "setup.log"}


def _exercise_env(root, var):
    cfx = _hostile_base(root / "control")
    env, pty, ctl_cmds, effective = _env_spec(var, cfx)
    for c in ctl_cmds:
        full = [h.REAL_GIT, *c]
        (h.run_pty(full, cwd=cfx.repo, env=env, timeout=30) if pty else h._run(full, cwd=cfx.repo, env=h.clean_env(env), timeout=30))
    control_ok = bool(effective())
    fx = _hostile_base(root / "run")
    env, pty, _, _ = _env_spec(var, fx)
    decoy = fx.outside / "decoy"
    decoy_before = h.snapshot(decoy) if decoy.exists() else None
    before = h.snapshot(fx.repo)
    ops = _run_ops(fx, _operations(fx, pty=pty, env=env))
    # xray is the canonical "which repo did you analyse" probe
    xr = h.run_warp(fx.repo, "xray", env=env)
    return {"control_ok": control_ok, "ops": ops, "fx": fx, "xray": xr, "env": env,
            "repo_diff": h.diff(before, h.snapshot(fx.repo)),
            "decoy_diff": h.diff(decoy_before, h.snapshot(decoy)) if decoy_before else []}


@pytest.fixture(scope="session")
def env_results(tmp_path_factory):
    base = tmp_path_factory.mktemp("hostile-env")
    safe = {v: re.sub(r"[^A-Za-z0-9]+", "_", v) for v in ENV_VARS}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {v: ex.submit(_exercise_env, base / safe[v], v) for v in ENV_VARS}
        return {v: f.result() for v, f in futs.items()}


def _all_output(res):
    return "\n".join(o["run"].stdout + "\n" + o["run"].stderr for o in res["ops"].values())


@pytest.mark.parametrize("var", ENV_VARS)
def test_env_vector_is_effective_in_control(env_results, var):
    assert env_results[var]["control_ok"], f"control: plain git did not show the effect of {var} (test is vacuous)"


@pytest.mark.parametrize("var", ENV_VARS)
def test_hostile_env_runs_no_marker(env_results, var):
    """GW-ENV-001..012: ambient environment variables never cause a configured command to run."""
    fired = {n: o["fired"] for n, o in env_results[var]["ops"].items() if o["fired"]}
    assert fired == {}, f"{var}: {fired}"


@pytest.mark.parametrize("var", ENV_VARS)
def test_hostile_env_does_not_redirect_or_leak(env_results, var):
    """No decoy-repository data in any output, the analysed repository is the one named by --repo, hostile paths stay
    untouched, no trace output on stdout/stderr and no trace file is created."""
    res = env_results[var]
    blob = _all_output(res) + res["xray"].stdout
    for needle in ("decoy-branch", "decoy.txt", "decoy commit"):
        assert needle not in blob, f"{var}: output leaked decoy data ({needle!r})"
    assert not TRACE_TS.search(blob) and "trace:" not in blob, f"{var}: Git trace output reached the tool output"
    out = res["fx"].outside
    assert not (out / "hostile-index").exists(), "GIT_INDEX_FILE was written through"
    assert list((out / "objs").glob("*")) == [] if (out / "objs").exists() else True
    for fname in TRACE_FILES.values():
        assert not (out / fname).exists(), f"{var}: trace file {fname} created"
    data = json.loads(res["xray"].stdout)
    assert not data.get("error"), data.get("error")
    assert data.get("state", {}).get("branch") == "main"
    assert res["decoy_diff"] == [], "decoy repository was modified"


@pytest.mark.parametrize("var", ENV_VARS)
def test_hostile_env_run_is_clean(env_results, var):
    """No crash/timeout, hooks exit 0, repository unchanged under every hostile environment variable."""
    res = env_results[var]
    bad = []
    for name, o in res["ops"].items():
        r = o["run"]
        if r.timed_out or r.traceback:
            bad.append(f"{name}: {'timeout' if r.timed_out else 'traceback ' + r.stderr[-160:]}")
        elif name.startswith("hook:") and r.code != 0:
            bad.append(f"{name}: exit {r.code}")
    assert bad == []
    assert res["repo_diff"] == [] and not any(o["mutated"] for o in res["ops"].values())
