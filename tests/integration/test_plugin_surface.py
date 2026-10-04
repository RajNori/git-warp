"""Mechanical proof that every advertised feature is reachable through the installed plugin surface.

Parses hooks.json, the skills, the agents, README and ``scripts/warp.py`` and cross-checks them; then runs the CLI of every
feature end to end against a temporary repository.  Nothing here trusts prose: a feature without a CLI command, a skill
that references a command that does not exist, or a script path that is not under ${CLAUDE_PLUGIN_ROOT} fails the build.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _ENV

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
WARP = SCRIPTS / "warp.py"
SKILLS = {p.parent.name: p for p in sorted((ROOT / "skills").glob("*/SKILL.md"))}
AGENTS = {p.stem: p for p in sorted((ROOT / "agents").glob("*.md"))}
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text())

# feature -> (skill or None, warp.py command(s), hook script or None)
FEATURES = {
    "Git Guardian": ("git-xray", ["guard"], "hook_git_guard.py"),
    "X-Ray": ("git-xray", ["xray"], None),
    "Rescue": ("git-rescue", ["rescue"], None),
    "Archaeology": ("git-archaeology", ["archaeology"], None),
    "AI Bisect": ("git-bisect-ai", ["bisect"], None),
    "PR Engineer": ("git-pr", ["pr"], None),
    "Semantic Commit Composer": ("git-commits", ["commits"], None),
    "Blast Radius": ("git-blast-radius", ["blast"], None),
    "Conflict Surgeon": ("git-conflict", ["conflict"], None),
    "Temporal Code Review": ("git-temporal-review", ["temporal"], None),
    "Flight Recorder": ("git-memory", ["memory"], "hook_post_tool.py"),
    "Repository Memory": ("git-memory", ["memory"], "hook_session_start.py"),
}


def commands_table():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import importlib
        mod = importlib.import_module("warp")
    finally:
        sys.path.pop(0)
    return dict(mod.COMMANDS)


COMMANDS = commands_table()


def fm_and_body(path):
    m = re.match(r"---\n(.*?)\n---\n(.*)", path.read_text(encoding="utf-8"), re.S)
    assert m, path
    return dict(line.partition(":")[::2] for line in m.group(1).splitlines()), m.group(2)


def warp_refs(text):
    """Commands referenced as ``warp.py <cmd>`` (quote after .py tolerated)."""
    return set(re.findall(r"warp\.py\"?\s+([a-z]+)", text))


def hook_commands():
    return [(ev, h["command"], h.get("timeout"), entry.get("matcher")) for ev, entries in HOOKS["hooks"].items()
            for entry in entries for h in entry["hooks"]]


# --------------------------------------------------------------------------- static structure

def test_every_json_file_parses():
    files = [p for p in ROOT.rglob("*.json") if ".git" not in p.parts and "node_modules" not in p.parts]
    assert {p.name for p in files} >= {"hooks.json", "plugin.json"}
    for p in files:
        json.loads(p.read_text(encoding="utf-8"))


def test_every_python_file_compiles():
    for p in list(SCRIPTS.rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        compile(p.read_text(encoding="utf-8"), str(p), "exec")


def test_plugin_manifest():
    pj = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert pj["name"] == "git-warp" and pj["version"] == "0.1.0"
    assert pj["description"] and pj["license"]
    assert not (ROOT / ".claude-plugin" / "marketplace.json").exists(), "nothing is published; docs/marketplace.md says so"


def test_hooks_json_shape_and_paths():
    assert set(HOOKS) == {"description", "hooks"}
    assert set(HOOKS["hooks"]) == {"SessionStart", "PreToolUse", "PostToolUse", "Stop"}
    for ev, entries in HOOKS["hooks"].items():
        assert isinstance(entries, list) and entries
        for entry in entries:
            assert set(entry) <= {"matcher", "hooks"}
            for h in entry["hooks"]:
                assert h["type"] == "command"
                assert isinstance(h["timeout"], (int, float)) and 0 < h["timeout"] <= 60
    for ev, cmd, _, _ in hook_commands():
        m = re.fullmatch(r'python3 "\$\{CLAUDE_PLUGIN_ROOT\}/(scripts/hook_[a-z_]+\.py)"', cmd)
        assert m, f"{ev}: script path must use ${{CLAUDE_PLUGIN_ROOT}} and be quoted: {cmd}"
        assert (ROOT / m.group(1)).is_file()
        assert str(ROOT) not in cmd and "~" not in cmd


def test_hooks_wire_guardian_recorder_session_start_and_stop():
    by_event = {ev: (cmd, matcher) for ev, cmd, _, matcher in hook_commands()}
    assert by_event["PreToolUse"][0].endswith("hook_git_guard.py\"") and by_event["PreToolUse"][1] == "Bash"
    assert by_event["PostToolUse"][0].endswith("hook_post_tool.py\"")
    assert {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"} <= set(by_event["PostToolUse"][1].split("|"))
    assert by_event["SessionStart"][0].endswith("hook_session_start.py\"")
    assert by_event["Stop"][0].endswith("hook_stop.py\"")


def test_every_hook_script_is_wired():
    wired = {re.search(r"(hook_[a-z_]+\.py)", c).group(1) for _, c, _, _ in hook_commands()}
    assert wired == {p.name for p in SCRIPTS.glob("hook_*.py")}


def test_guard_budget_fits_inside_hook_timeout():
    """The host kills a hook at its timeout and then runs the command unguarded: the guard must give up first."""
    src = (SCRIPTS / "gitwarp" / "hooks" / "git_guard.py").read_text()
    deadline = float(re.search(r"^GUARD_DEADLINE_S\s*=\s*([\d.]+)", src, re.M).group(1))
    timeout = {ev: t for ev, _, t, _ in hook_commands()}
    assert deadline + 2 <= timeout["PreToolUse"]


@pytest.mark.parametrize("name,budget_file", [("SessionStart", "session_start.py"), ("PostToolUse", "post_tool.py"), ("Stop", "stop.py")])
def test_non_guard_hook_budgets_fit_inside_hook_timeout(name, budget_file):
    src = (SCRIPTS / "gitwarp" / "hooks" / budget_file).read_text()
    budget = float(re.search(r"^BUDGET_S\s*=\s*([\d.]+)", src, re.M).group(1))
    timeout = {ev: t for ev, _, t, _ in hook_commands()}[name]
    assert budget + 2 <= timeout


# --------------------------------------------------------------------------- skills / agents / commands

def test_every_skill_maps_to_an_existing_command_with_matching_permission():
    for name, path in SKILLS.items():
        fm, body = fm_and_body(path)
        refs = warp_refs(body)
        assert refs, f"{name} never references a warp.py command"
        assert refs <= set(COMMANDS), f"{name} references unknown commands {refs - set(COMMANDS)}"
        allowed = fm["allowed-tools"]
        assert 'Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*)' in allowed, name
        for line in body.splitlines():
            if "warp.py" in line and "python3" in line:
                assert '"${CLAUDE_PLUGIN_ROOT}/scripts/warp.py"' in line, f"{name}: script path must use CLAUDE_PLUGIN_ROOT: {line}"
        assert "$HOME" not in body and str(ROOT) not in body


def test_every_command_is_reachable_from_a_skill_agent_or_hook():
    reachable = set()
    for p in list(SKILLS.values()) + list(AGENTS.values()):
        reachable |= warp_refs(p.read_text(encoding="utf-8"))
    assert set(COMMANDS) - reachable == set()


def test_every_command_module_exists_and_exposes_main():
    import importlib
    sys.path.insert(0, str(SCRIPTS))
    try:
        for cmd, modname in COMMANDS.items():
            assert callable(importlib.import_module(modname).main), cmd
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_feature_is_reachable(feature):
    skill, cmds, hook = FEATURES[feature]
    assert skill in SKILLS
    fm, body = fm_and_body(SKILLS[skill])
    for c in cmds:
        assert c in COMMANDS
        assert c in warp_refs(body), f"{skill} SKILL.md must reference warp.py {c}"
    if hook:
        assert any(hook in cmd for _, cmd, _, _ in hook_commands())


def test_agents_reference_only_real_commands():
    for name, p in AGENTS.items():
        assert warp_refs(p.read_text(encoding="utf-8")) <= set(COMMANDS), name


def test_readme_feature_sections_name_a_real_command_and_skill():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("## Features")
    section = readme[start:]
    parts = re.split(r"^### ", section, flags=re.M)[1:]
    names = {re.match(r"[^(\n]+", p).group(0).strip() for p in parts}
    assert len(parts) >= 12, names
    for p in parts:
        head = p.splitlines()[0]
        sk = re.findall(r"git-warp:(git-[a-z-]+)", head)
        cmds = set(re.findall(r"warp\.py\s+([a-z]+)", p)) & set(COMMANDS)
        hooky = "hook" in head.lower() or "hook" in p.lower()
        assert (sk and all(s in SKILLS for s in sk)) or cmds or hooky, f"README feature without a CLI/skill/hook: {head}"
        if sk:
            assert all(s in SKILLS for s in sk), head


def test_no_skill_claims_a_missing_command():
    """Every ``warp.py <word>`` anywhere in skills/agents/README must be a real command (no prose-only features)."""
    texts = [p.read_text(encoding="utf-8") for p in list(SKILLS.values()) + list(AGENTS.values())]
    texts.append((ROOT / "README.md").read_text(encoding="utf-8"))
    for t in texts:
        for w in warp_refs(t):
            assert w in COMMANDS, w


# --------------------------------------------------------------------------- end to end

def run_warp(repo, *argv, timeout=120):
    env = dict(os.environ, **_ENV)
    p = subprocess.run([sys.executable, str(WARP), *argv, "--repo", str(repo.path)], capture_output=True, text=True,
                       cwd=repo.path, env=env, timeout=timeout)
    assert "Traceback" not in p.stdout + p.stderr, p.stderr
    return p.returncode, json.loads(p.stdout)


@pytest.fixture
def worked_repo(make_repo):
    r = make_repo().seed(4)
    r.commit("add module", {"pkg/__init__.py": "", "pkg/core.py": "def f():\n    return 1\n", "tests/test_core.py": "from pkg import core\n"})
    r.write("pkg/core.py", "def f():\n    return 2\n").write("notes.txt", "new\n")
    return r


E2E = {
    "Git Guardian": [("guard", "check", "git status")],
    "X-Ray": [("xray",)],
    "Rescue": [("rescue", "scan", "--no-fsck")],
    "Archaeology": [("archaeology", "pkg/core.py")],
    "AI Bisect": [("bisect", "plan", "--good", "HEAD~2", "--bad", "HEAD")],
    "PR Engineer": [("pr",)],
    "Semantic Commit Composer": [("commits",)],
    "Blast Radius": [("blast",)],
    "Temporal Code Review": [("temporal",)],
    "Repository Memory": [("memory", "status")],
}


@pytest.mark.parametrize("feature", sorted(E2E))
def test_feature_runs_end_to_end(worked_repo, feature):
    for argv in E2E[feature]:
        code, out = run_warp(worked_repo, *argv)
        assert code == 0, (argv, out)
        assert isinstance(out, dict) and "error" not in out, (argv, out)


def test_conflict_surgeon_runs_end_to_end(make_repo):
    r = make_repo().seed(1)
    r.git("switch", "-q", "-c", "side")
    r.write("f0.txt", "side\n")
    r.git("commit", "-qam", "side change")
    r.git("switch", "-q", "main")
    r.write("f0.txt", "main\n")
    r.git("commit", "-qam", "main change")
    r.git("merge", "side", check=False)
    code, out = run_warp(r, "conflict")
    assert code == 0 and "error" not in out
    assert "f0.txt" in json.dumps(out)
    # and outside a conflict it still answers with JSON, never a traceback
    r2 = make_repo().seed(1)
    code, out = run_warp(r2, "conflict")
    assert isinstance(out, dict)


def test_flight_recorder_end_to_end(worked_repo):
    ev = {"hook_event_name": "PostToolUse", "session_id": "e2e", "cwd": str(worked_repo.path), "tool_name": "Write",
          "tool_input": {"file_path": str(worked_repo.path / "pkg" / "core.py"), "content": "x"}, "tool_response": {}}
    p = subprocess.run([sys.executable, str(SCRIPTS / "hook_post_tool.py")], input=json.dumps(ev), capture_output=True,
                       text=True, cwd=worked_repo.path, timeout=60, env=dict(os.environ, **_ENV))
    assert p.returncode == 0 and json.loads(p.stdout) == {}
    assert (worked_repo.path / ".git" / "git-warp" / "flight-recorder.jsonl").is_file()
    code, out = run_warp(worked_repo, "memory", "sessions")
    assert code == 0 and "e2e" in json.dumps(out)
