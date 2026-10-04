"""Structural invariants of the plugin (added after the recovery cleanup)."""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CORE_GIT = SCRIPTS / "gitwarp" / "core" / "git.py"


def _py_files():
    return [p for p in SCRIPTS.rglob("*.py") if "__pycache__" not in p.parts]


def _imports_subprocess(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "subprocess" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess":
            return True
    return False


def test_subprocess_only_in_core_git():
    offenders = [str(p.relative_to(ROOT)) for p in _py_files() if p != CORE_GIT and _imports_subprocess(ast.parse(p.read_text()))]
    assert offenders == []


def test_no_dangerous_calls_in_production_code():
    bad = []
    for p in _py_files():
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                if name in ("eval", "exec", "system", "popen"):
                    bad.append((str(p.relative_to(ROOT)), name))
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        bad.append((str(p.relative_to(ROOT)), "shell=True"))
    assert bad == []


def test_hooks_json_points_at_existing_scripts():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for ev in data["hooks"].values() for entry in ev for h in entry["hooks"]]
    assert cmds
    for c in cmds:
        m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?)\"?$", c)
        assert m, c
        assert (ROOT / m.group(1)).is_file(), c


def test_obsolete_scaffold_scripts_are_gone():
    for name in ("_common.py", "git_guard.py", "session_context.py", "change_tracker.py", "stop_report.py"):
        assert not (SCRIPTS / name).exists(), name
    assert not (ROOT / ".DS_Store").exists()
    assert ".DS_Store" in (ROOT / ".gitignore").read_text().split()
