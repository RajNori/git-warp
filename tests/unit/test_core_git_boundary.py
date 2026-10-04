"""Architecture: core/git.py is the ONE process boundary and revisions.py the ONE revision-normalization path."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"
SCRIPTS = PLUGIN / "scripts"
CORE_GIT = SCRIPTS / "gitwarp" / "core" / "git.py"
REVISIONS = SCRIPTS / "gitwarp" / "core" / "revisions.py"

FORBIDDEN_MODULES = {"subprocess", "pty", "ctypes", "multiprocessing", "pexpect"}
FORBIDDEN_OS_CALLS = {"system", "popen", "fork", "forkpty", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
                      "execlpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                      "posix_spawn", "posix_spawnp", "startfile"}
FORBIDDEN_CALL_PREFIXES = ("create_subprocess",)           # asyncio.create_subprocess_exec / _shell
FORBIDDEN_SHLEX = {"split", "join"}                         # shlex.quote (display text only) is allowed; building commands from text is not


def _py_files():
    return [p for p in SCRIPTS.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(p):
    return str(p.relative_to(ROOT))


def _modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            yield (node.module or "").split(".")[0]


def test_only_core_git_may_import_process_modules():
    offenders = [(_rel(p), m) for p in _py_files() if p != CORE_GIT for m in _modules(ast.parse(p.read_text())) if m in FORBIDDEN_MODULES]
    assert offenders == []


def test_no_process_spawning_calls_anywhere_outside_core_git():
    bad = []
    for p in _py_files():
        if p == CORE_GIT:
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Attribute):
                owner = f.value.id if isinstance(f.value, ast.Name) else ""
                if owner == "os" and f.attr in FORBIDDEN_OS_CALLS:
                    bad.append((_rel(p), f"os.{f.attr}"))
                if f.attr.startswith(FORBIDDEN_CALL_PREFIXES):
                    bad.append((_rel(p), f.attr))
                if owner == "shlex" and f.attr in FORBIDDEN_SHLEX:
                    bad.append((_rel(p), f"shlex.{f.attr}"))
            elif isinstance(f, ast.Name) and (f.id.startswith(FORBIDDEN_CALL_PREFIXES) or f.id in ("system", "popen")):
                bad.append((_rel(p), f.id))
    assert bad == []


def test_core_git_never_uses_a_shell_or_string_command():
    tree = ast.parse(CORE_GIT.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    assert isinstance(kw.value, ast.Constant) and kw.value.value is False
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"system", "popen", "shlex", "exec", "execv", "execvp", "run_command"}
    assert "Popen" in names and "start_new_session" in CORE_GIT.read_text()      # own process group, killed as a group


def test_subprocess_run_with_capture_output_is_not_used():
    """Unbounded buffering (capture_output / communicate) is exactly what the bounded runner replaces."""
    src = CORE_GIT.read_text()
    assert "capture_output" not in src and ".communicate(" not in src and "subprocess.run(" not in src


def test_check_ref_scatter_is_gone():
    """Revision/ref normalization lives only in core/revisions.py; nothing calls a ``check_ref`` helper any more."""
    offenders = []
    for p in _py_files():
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Attribute) and node.attr == "check_ref":
                offenders.append(_rel(p))
            if isinstance(node, ast.FunctionDef) and node.name == "check_ref":
                offenders.append(_rel(p))
    assert offenders == []


def test_revision_parsing_helpers_live_only_in_revisions_module():
    """Only core/revisions.py defines the hex-id / option-shape validation; other modules call it."""
    for p in _py_files():
        if p in (REVISIONS,):
            continue
        src = p.read_text()
        assert "_FULL_OID" not in src and "def check_syntax" not in src, _rel(p)


def test_call_sites_do_not_build_revision_commands_from_raw_input():
    """Every module that takes a base/head/good/bad/ref value imports the normalization service (directly or via git.py)."""
    expected = {
        "analysis/pr.py": "revisions.resolve", "analysis/xray.py": "revisions.resolve", "semantic/blast.py": "revisions.resolve",
        "semantic/temporal.py": "revisions.resolve", "history/bisect.py": "revisions.resolve", "history/rescue.py": "revisions.resolve",
    }
    for rel, needle in expected.items():
        assert needle in (SCRIPTS / "gitwarp" / rel).read_text(), rel


def test_no_raw_range_strings_with_user_values():
    """No production f-string builds ``{base}...HEAD`` / ``{base}..HEAD`` style ranges from an unresolved label."""
    bad = []
    for p in _py_files():
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.JoinedStr):
                text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
                names = {v.value.id for v in node.values if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)}   # bare names only
                if ".." in text and names & {"base", "base_arg", "rev", "ref", "good", "bad", "head_arg"}:
                    bad.append((_rel(p), text))
    assert bad == []
