"""Convergence safety contracts (release gates for v0.1.0).

Each test encodes a behaviour established by the neutral Disposable bake-off (https://github.com/RajNori/Disposable).
The Disposable case id is given in each docstring so the two suites can be cross-referenced.  These tests were written
BEFORE the fixes: against ``warp/phoenix`` @ d909d6d they fail exactly where Phoenix had the confirmed defects.

Nothing here executes a destructive command: the guard only classifies text, and every fixture lives in ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.hook_helpers import SCRIPTS, run_hook
from tests.fake_secrets import ANTHROPIC, AWS_KEY, AWS_SECRET, GITHUB, HF, NPM, OPENAI

WARP = SCRIPTS / "warp.py"
STATE_FILES = ("flight-recorder.jsonl", "state.json", "warp.db")


# --------------------------------------------------------------------------- helpers

def warp(repo_path, *args, env=None, timeout=60):
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run([sys.executable, str(WARP), *args, "--repo", str(repo_path)], capture_output=True, text=True,
                       timeout=timeout, env=e, cwd=str(repo_path))
    return p.returncode, p.stdout, p.stderr


def post_tool(repo_path, tin=None, env=None, timeout=30):
    ev = {"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(repo_path), "tool_name": "Bash",
          "tool_input": tin or {"command": "git status"}, "tool_response": {}}
    return _hook("post_tool", ev, repo_path, env, timeout)


def _hook(name, event, cwd, env=None, timeout=30, raw=None):
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run([sys.executable, str(SCRIPTS / f"hook_{name}.py")],
                       input=raw if raw is not None else json.dumps(event), capture_output=True, text=True,
                       cwd=str(cwd), timeout=timeout, env=e)
    return p.returncode, p.stdout, p.stderr


def guard(command, cwd, env=None):
    ev = {"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(cwd), "tool_name": "Bash",
          "tool_input": {"command": command}}
    code, out, err = _hook("git_guard", ev, cwd, env)
    assert code == 0, err
    data = json.loads(out) if out.strip() else {}
    return (data.get("hookSpecificOutput") or {}).get("permissionDecision", "defer"), data


def exercise_state(repo_path, env=None):
    """Drive every path that writes Git Warp state: recorder, SessionStart/Stop (state.json), memory index (warp.db)."""
    post_tool(repo_path, env=env)
    _hook("session_start", {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(repo_path), "source": "startup"}, repo_path, env)
    _hook("stop", {"hook_event_name": "Stop", "session_id": "s", "cwd": str(repo_path), "stop_hook_active": False}, repo_path, env)
    warp(repo_path, "memory", "index", env=env)


def state_dir(repo):
    return repo.path / ".git" / "git-warp"


@pytest.fixture(autouse=True)
def _umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


# --------------------------------------------------------------------------- storage (GW-PRIVACY-004..008)

def test_state_directory_and_files_are_private(repo):
    """GW-PRIVACY-004: state dir 0700, every state file 0600 (Phoenix: 0755 / 0644)."""
    exercise_state(repo.path)
    sd = state_dir(repo)
    assert sd.is_dir()
    assert stat.S_IMODE(sd.stat().st_mode) == 0o700
    files = [p for p in sd.rglob("*") if p.is_file()]
    assert {p.name for p in files} >= {"flight-recorder.jsonl", "warp.db"}
    loose = {str(p.relative_to(sd)): oct(stat.S_IMODE(p.stat().st_mode)) for p in files if stat.S_IMODE(p.stat().st_mode) & 0o077}
    assert loose == {}


def test_preexisting_loose_state_is_tightened(repo):
    """Upgrade path: a directory/database left 0755/0644 by an earlier version becomes private again."""
    exercise_state(repo.path)
    sd = state_dir(repo)
    os.chmod(sd, 0o755)
    for p in sd.iterdir():
        if p.is_file():
            os.chmod(p, 0o644)
    exercise_state(repo.path)
    assert stat.S_IMODE(sd.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in sd.iterdir() if p.is_file())


def test_symlinked_state_directory_is_refused(repo, tmp_path):
    """GW-PRIVACY-005: a symlinked .git/git-warp is never followed (Phoenix wrote all three files through it)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, state_dir(repo))
    exercise_state(repo.path)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("name", STATE_FILES)
def test_symlinked_state_file_is_not_written_through(repo, tmp_path, name):
    """GW-PRIVACY-006: a symlinked state file is refused, never written through."""
    exercise_state(repo.path)
    target = tmp_path / "victim"
    target.write_text("ORIGINAL")
    p = state_dir(repo) / name
    p.unlink()
    os.symlink(target, p)
    exercise_state(repo.path)
    assert target.read_text() == "ORIGINAL"


@pytest.mark.parametrize("name", STATE_FILES)
def test_fifo_state_target_is_refused_without_hanging(repo, name):
    """GW-PRIVACY-007: a FIFO in place of a state file must not block a hook (Phoenix hung until the host timeout)."""
    exercise_state(repo.path)
    p = state_dir(repo) / name
    p.unlink()
    os.mkfifo(p)
    # bounded: subprocess.run raises TimeoutExpired if any hook blocks
    exercise_state(repo.path)
    assert stat.S_ISFIFO(p.lstat().st_mode)


@pytest.mark.parametrize("name", STATE_FILES)
def test_socket_state_target_is_refused(repo, name):
    """Non-regular targets of every kind are refused (socket).  AF_UNIX paths are short, so bind in a short temp dir
    and rename the socket into place."""
    import shutil
    import tempfile
    exercise_state(repo.path)
    p = state_dir(repo) / name
    p.unlink()
    short = Path(tempfile.mkdtemp(prefix="gw", dir="/tmp"))
    s = socket.socket(socket.AF_UNIX)
    try:
        s.bind(str(short / "s"))
        os.rename(short / "s", p)
        exercise_state(repo.path)
        assert stat.S_ISSOCK(p.lstat().st_mode)
    finally:
        s.close()
        shutil.rmtree(short, ignore_errors=True)


@pytest.mark.parametrize("name", STATE_FILES)
def test_directory_in_place_of_state_file_is_handled(repo, name):
    """GW-PRIVACY-008: an unexpected directory is neither deleted nor followed; hooks stay silent and exit 0."""
    exercise_state(repo.path)
    p = state_dir(repo) / name
    p.unlink()
    p.mkdir()
    (p / "keep.txt").write_text("keep")
    code, out, err = post_tool(repo.path)
    assert code == 0 and err == ""
    assert (p / "keep.txt").read_text() == "keep"


def test_linked_worktree_uses_the_common_git_dir(repo, tmp_path):
    """Never assume .git is a directory: a linked worktree's .git is a file; state lives in the common dir."""
    wt = tmp_path / "linked"
    repo.git("worktree", "add", "-q", str(wt), "-b", "wt-branch")
    assert (wt / ".git").is_file()
    post_tool(wt)
    assert (state_dir(repo) / "flight-recorder.jsonl").is_file()
    assert stat.S_IMODE(state_dir(repo).stat().st_mode) == 0o700
    assert not (wt / ".git" / "git-warp").exists()


def test_corrupt_database_is_quarantined_not_deleted(repo):
    """Corruption must preserve user data: the unusable database is moved aside, not removed."""
    exercise_state(repo.path)
    db = state_dir(repo) / "warp.db"
    db.write_bytes(b"this is not a sqlite database" * 50)
    rc, out, err = warp(repo.path, "memory", "index")
    assert rc == 0, (out, err)
    kept = [p for p in state_dir(repo).iterdir() if p.name.startswith("warp.db") and p.name != "warp.db"]
    assert any(b"this is not a sqlite database" in p.read_bytes() for p in kept if p.is_file())


# --------------------------------------------------------------------------- Guardian: dynamic expressions (GW-GUARD-003/007/049/050)

DYNAMIC_MUST_NOT_DEFER = [
    "git reset $(echo --hard)",
    "git reset `echo --hard`",
    "git reset ${FLAG}",
    "FLAG=--hard; git reset $FLAG",
    'cmd="git reset --hard"; $cmd',
    "GIT=git; $GIT reset --hard",
    "$(echo git) reset --hard",
    "git $(echo reset) --hard",
    "x=reset; git $x --hard",
    "eval 'git reset --hard'",
    "bash <(echo 'git reset --hard')",
    "source /dev/stdin <<< 'git reset --hard'",
    "echo 'git reset --hard' > gen.sh && bash gen.sh",
    "printf 'git clean -fdx' > gen.sh; sh ./gen.sh",
    "git push origin $(echo +main)",
    "git clean $(echo -fdx)",
    "git checkout $(echo --force) main",
]


@pytest.mark.parametrize("cmd", DYNAMIC_MUST_NOT_DEFER)
def test_dynamic_expressions_that_could_hide_destruction_never_defer(repo, cmd):
    """GW-GUARD-003/007/049/050: uncertainty is ASK (or DENY), never a confident safe classification."""
    decision, _ = guard(cmd, repo.path)
    assert decision in ("ask", "deny"), f"{cmd!r} -> {decision}"


DYNAMIC_SAFE_STAY_DEFERRED = [
    "git log $(git merge-base HEAD main)..HEAD",
    "git diff $(git rev-parse HEAD~1)",
    "git show $(git rev-parse HEAD)",
    'git commit -m "$(date)"',
    "echo $(git status --short)",
    "source venv/bin/activate",
    "git status",
]


@pytest.mark.parametrize("cmd", DYNAMIC_SAFE_STAY_DEFERRED)
def test_read_only_dynamic_commands_are_not_over_blocked(repo, cmd):
    """False-positive guard: dynamic words in read-only Git commands defer to ordinary Claude permissions."""
    decision, _ = guard(cmd, repo.path)
    assert decision == "defer", f"{cmd!r} -> {decision}"


@pytest.mark.parametrize("cmd", [
    "git reset --hard", "git reset --har", "echo 'git reset --hard' | sh", "bash -c 'git reset --hard'",
    "git submodule foreach 'git reset --hard'", "git checkout -B main", "git switch -C main",
    "git update-ref --delete refs/heads/main", "git read-tree --reset -u HEAD", "git checkout-index -f -a",
    "git push -fu origin main", "git push origin :main", "git push origin +HEAD:refs/heads/main", "rm -rf .git",
])
def test_phoenix_guardian_coverage_is_not_regressed(repo, cmd):
    """Phoenix coverage that Rebirth lacked must survive convergence (GW-GUARD-002/013/014/022/024/027/029/030/031/043)."""
    decision, _ = guard(cmd, repo.path)
    assert decision in ("ask", "deny")


@pytest.mark.parametrize("cmd", [
    "git clean -nfd", "git clean -n -f -d", "git clean -fdn", "git clean --dry-run -fd", "git clean -dnf", "git clean -ndf",
])
def test_clean_dry_run_is_not_destructive(repo, cmd):
    """GW-GUARD-010: -n/--dry-run wins regardless of bundling or option order; never DENY."""
    decision, _ = guard(cmd, repo.path)
    assert decision != "deny"


@pytest.mark.parametrize("cmd", ["git clean -fd", "git clean -xfd", "git clean -f -d", "git clean --force -d"])
def test_real_clean_is_still_denied(repo, cmd):
    decision, _ = guard(cmd, repo.path)
    assert decision == "deny"


# --------------------------------------------------------------------------- hook fail-safe (GW-HOOK-018..025)

@pytest.mark.parametrize("raw", [
    "", "{", "[]", "null", '{"tool_name": "Bash"}', '{"tool_name": "Bash", "tool_input": "oops"}',
    '{"tool_name": "Bash", "tool_input": {"command": 123}}', '{"tool_name": "Bash", "tool_input": {}}',
    '{"tool_name": ["Bash"], "tool_input": {"command": "git reset --hard"}}',
])
def test_guard_fails_safe_on_malformed_payloads(repo, raw):
    """GW-HOOK-018/020/021/024/025: a payload the guard cannot interpret is never silently allowed."""
    code, out, err = _hook("git_guard", None, repo.path, raw=raw)
    assert code == 0
    decision = (json.loads(out).get("hookSpecificOutput") or {}).get("permissionDecision", "defer") if out.strip() else "defer"
    assert decision in ("ask", "deny"), (raw, out)


def test_guard_defers_for_other_tools(repo):
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "x"}, "cwd": str(repo.path)}
    code, out, _ = _hook("git_guard", ev, repo.path)
    assert code == 0 and (not out.strip() or json.loads(out) == {})


# --------------------------------------------------------------------------- revision injection (GW-REV-*)

HOSTILE = ["--output={f}", "-o{f}", "--exec-path={f}", "--git-dir={f}", "--work-tree={f}"]


@pytest.mark.parametrize("tpl", HOSTILE)
@pytest.mark.parametrize("cmd", ["pr", "blast", "temporal"])
def test_option_shaped_base_cannot_write_files(repo, tmp_path, tpl, cmd):
    """GW-REV-001..: option-shaped revision input is rejected; no file appears; the repo is untouched."""
    sentinel = tmp_path / "PWNED"
    before = repo.git("rev-parse", "HEAD"), repo.git("status", "--porcelain")
    warp(repo.path, cmd, f"--base={tpl.format(f=sentinel)}")
    assert not sentinel.exists()
    assert (repo.git("rev-parse", "HEAD"), repo.git("status", "--porcelain")) == before


@pytest.mark.parametrize("tpl", HOSTILE)
def test_option_shaped_values_elsewhere_cannot_write_files(repo, tmp_path, tpl):
    sentinel = tmp_path / "PWNED"
    v = tpl.format(f=sentinel)
    for args in (("archaeology", f"--symbol={v}"), ("archaeology", f"--regex={v}"), ("archaeology", f"--since={v}"),
                 ("archaeology", f"--question={v}"), ("archaeology", "--", v), ("rescue", "scan", f"--since={v}"),
                 ("rescue", "scan", f"--grep={v}"), ("rescue", "scan", f"--path={v}"), ("bisect", "plan", f"--good={v}", "--bad=HEAD"),
                 ("bisect", "plan", "--good=HEAD~1", f"--bad={v}"), ("blast", "--", v)):
        warp(repo.path, *args)
    assert not sentinel.exists()


@pytest.mark.parametrize("ref", ["HEAD", "HEAD~1", "main", "feature/test", "FULL", "SHORT", "v1", "v1-annotated"])
def test_normal_refs_still_work(repo, ref):
    """Normal revision syntax keeps working (annotated tag, branch with slash, full/short SHA)."""
    repo.git("tag", "v1", "HEAD~1")
    repo.git("tag", "-a", "v1-annotated", "-m", "annotated", "HEAD~1")
    repo.branch("feature/test", "HEAD~1")
    value = {"FULL": repo.sha(), "SHORT": repo.sha()[:8]}.get(ref, ref)
    rc, out, err = warp(repo.path, "pr", f"--base={value}")
    assert rc == 0 and "error" not in json.loads(out), (out, err)


# --------------------------------------------------------------------------- hostile config / environment (GW-CONFIG-001/003/009, GW-ENV-*)

def _marker(tmp_path, name, passthrough=False):
    log = tmp_path / f"{name}.log"
    s = tmp_path / f"{name}.sh"
    body = f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{log}"\n' + ('cat "$1"\n' if passthrough else "") + "exit 0\n"
    s.write_text(body)
    s.chmod(0o755)
    return s, log


def _all_analysis(repo_path, env=None):
    exercise_state(repo_path, env)
    for args in (("xray",), ("pr", "--base=HEAD~1"), ("blast",), ("commits",), ("temporal",), ("conflict",), ("rescue", "scan"),
                 ("archaeology", "f0.txt"), ("bisect", "plan", "--good=HEAD~2", "--bad=HEAD"), ("memory", "status")):
        warp(repo_path, *args, env=env)


def test_repo_fsmonitor_is_not_executed(repo, tmp_path):
    """GW-CONFIG-001: core.fsmonitor from the repository's own config must not run during analysis or hooks."""
    s, log = _marker(tmp_path, "fsmonitor")
    repo.git("config", "core.fsmonitor", str(s))
    repo.write("dirty.txt", "d\n")
    _all_analysis(repo.path)
    assert not log.exists(), log.read_text() if log.exists() else ""


def test_repo_textconv_is_not_executed(repo, tmp_path):
    """GW-CONFIG-003: diff.<driver>.textconv must not run (Phoenix ran it in archaeology/commits/temporal)."""
    s, log = _marker(tmp_path, "textconv", passthrough=True)
    repo.write(".gitattributes", "*.txt diff=bk\n")
    repo.commit("attrs")
    repo.git("config", "diff.bk.textconv", str(s))
    repo.write("f0.txt", "changed\n")
    _all_analysis(repo.path)
    assert not log.exists(), log.read_text() if log.exists() else ""


def test_repo_external_diff_is_not_executed(repo, tmp_path):
    """GW-CONFIG-002: diff.external must not run."""
    s, log = _marker(tmp_path, "external")
    repo.git("config", "diff.external", str(s))
    repo.write("f0.txt", "changed\n")
    _all_analysis(repo.path)
    assert not log.exists()


def test_repo_hookspath_is_not_triggered_by_analysis(repo, tmp_path):
    """GW-CONFIG-009: read-only analysis must not fire core.hooksPath hooks (post-index-change etc.)."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    log = tmp_path / "hooks.log"
    for h in ("post-index-change", "reference-transaction", "post-checkout", "pre-commit", "post-commit", "post-merge"):
        f = hooks / h
        f.write_text(f'#!/bin/sh\necho {h} >> "{log}"\n')
        f.chmod(0o755)
    repo.git("config", "core.hooksPath", str(hooks))
    repo.write("dirty.txt", "d\n")
    repo.write("f0.txt", "changed\n")
    _all_analysis(repo.path)
    assert not log.exists(), log.read_text() if log.exists() else ""


def test_alias_and_pager_config_are_not_executed(repo, tmp_path):
    """GW-CONFIG-004..008/010: pagers and aliases are never executed by analysis."""
    s, log = _marker(tmp_path, "pager", passthrough=True)
    repo.git("config", "core.pager", str(s))
    repo.git("config", "pager.diff", "true")
    repo.git("config", "pager.log", "true")
    repo.git("config", "pager.show", "true")
    repo.git("config", "alias.st", f"!{s}")
    repo.write("f0.txt", "changed\n")
    _all_analysis(repo.path)
    assert not log.exists()


def test_hostile_environment_cannot_redirect_or_execute(repo, make_repo, tmp_path):
    """GW-ENV-001/002/004/010/011/012: ambient GIT_DIR / GIT_WORK_TREE / GIT_CONFIG_* must not redirect analysis or
    execute commands; the analysed repository is the one named by --repo."""
    decoy = make_repo("decoy", branch="decoybranch").seed(2)
    s, log = _marker(tmp_path, "envcfg")
    cfg = tmp_path / "global.cfg"
    cfg.write_text(f"[core]\n\tfsmonitor = {s}\n")
    ext, extlog = _marker(tmp_path, "extdiff")
    env = {
        "GIT_DIR": str(decoy.path / ".git"), "GIT_WORK_TREE": str(decoy.path),
        "GIT_INDEX_FILE": str(tmp_path / "hostile-index"),
        "GIT_OBJECT_DIRECTORY": str(tmp_path / "no-objects"), "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(tmp_path / "alt"),
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": str(s),
        "GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_SYSTEM": str(cfg), "GIT_CONFIG_NOSYSTEM": "",
        "GIT_EXTERNAL_DIFF": str(ext), "GIT_SSH": str(s), "GIT_SSH_COMMAND": str(s),
        "GIT_PAGER": str(s), "PAGER": str(s), "GIT_TRACE": "1", "GIT_TRACE2": str(tmp_path / "trace2.log"),
    }
    repo.write("f0.txt", "changed\n")
    rc, out, err = warp(repo.path, "xray", env=env)
    assert rc == 0, (out, err)
    data = json.loads(out)
    blob = json.dumps(data)
    assert "decoybranch" not in blob
    assert data.get("state", {}).get("branch") == "main"
    _all_analysis(repo.path, env)
    assert not log.exists() and not extlog.exists()
    assert not (tmp_path / "hostile-index").exists() and not (tmp_path / "trace2.log").exists()
    assert "GIT_TRACE" not in err


# --------------------------------------------------------------------------- trusted policy (precedence)

def _write_policy(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{body}\n---\n")


def test_repository_policy_cannot_remove_the_built_in_floor(repo):
    """Repository config may add protected refs; it must not shrink the built-in set."""
    _write_policy(repo.path / ".claude" / "git-warp.local.md", "protected_branches: [nothing-special]")
    decision, _ = guard("git push --force origin main", repo.path)
    assert decision == "deny"


def test_repository_policy_may_add_protected_refs(repo):
    _write_policy(repo.path / ".claude" / "git-warp.local.md", "protected_branches: [staging]")
    decision, _ = guard("git push --force origin staging", repo.path)
    assert decision == "deny"


def test_repository_policy_cannot_lower_user_strictness(repo, tmp_path):
    """Precedence: built-in floor < user policy < repository policy, where a lower-trust source can only tighten."""
    home = tmp_path / "home"
    _write_policy(home / ".claude" / "git-warp.local.md", "safety_mode: strict")
    _write_policy(repo.path / ".claude" / "git-warp.local.md", "safety_mode: standard")
    decision, _ = guard("git rebase -i HEAD~3", repo.path, env={"HOME": str(home)})
    assert decision == "deny"          # strict denies risky history rewrites; the repo cannot relax it


def test_repository_policy_can_raise_strictness(repo):
    _write_policy(repo.path / ".claude" / "git-warp.local.md", "safety_mode: strict")
    decision, _ = guard("git rebase -i HEAD~3", repo.path)
    assert decision == "deny"


def test_unconditional_rules_cannot_be_disabled_by_any_config(repo, tmp_path):
    home = tmp_path / "home"
    for where in (repo.path / ".claude" / "git-warp.local.md", home / ".claude" / "git-warp.local.md"):
        _write_policy(where, "safety_mode: off\nprotected_branches: []\nguard_enabled: false")
    decision, _ = guard("git reset --hard", repo.path, env={"HOME": str(home)})
    assert decision == "deny"


# --------------------------------------------------------------------------- privacy (GW-PRIVACY-001/002)

SECRETS = {
    "github": GITHUB,
    "aws-key": AWS_KEY,
    "aws-secret": AWS_SECRET,
    "openai": OPENAI,
    "anthropic": ANTHROPIC,
    "bearer": "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA",
    "cookie": "s3ss10nS3cr3tV4lue99",
    "basic-url": "hunter2S3cretPw",
    "npm": NPM,
    "hf": HF,
    "pw-flag": "Sup3rS3cr3tPwFlag",
    "ssh-key": "/keys/prod_ed25519_secretname",
}
SECRET_TEXT = [
    f"token {SECRETS['github']}", f"AWS_ACCESS_KEY_ID={SECRETS['aws-key']}", f"aws_secret_access_key={SECRETS['aws-secret']}",
    f"OPENAI_API_KEY={SECRETS['openai']}", f"key {SECRETS['anthropic']}",
    f"curl -H 'Authorization: Bearer {SECRETS['bearer']}' x", f"curl -H 'Cookie: sessionid={SECRETS['cookie']}' x",
    f"remote https://deploy:{SECRETS['basic-url']}@git.example.invalid/r.git",
    f"//registry.npmjs.org/:_authToken={SECRETS['npm']}", f"HF_TOKEN={SECRETS['hf']}",
    f"mysql -u root --password={SECRETS['pw-flag']} db", f"ssh -i {SECRETS['ssh-key']} deploy@host",
]


def _secret_repo(make_repo):
    r = make_repo("secrets").seed(2)
    for i, text in enumerate(SECRET_TEXT):
        r.commit(f"chore: rotate {text}", {f"n{i}.txt": f"{i}\n"})
    return r


def _leaks(blob: str):
    return sorted(k for k, v in SECRETS.items() if v in blob)


def test_commit_text_is_redacted_before_persistence(make_repo):
    """GW-PRIVACY-001: warp.db / flight recorder / state files hold no secret value (Phoenix kept all 12 in `commits`)."""
    r = _secret_repo(make_repo)
    exercise_state(r.path)
    blob = ""
    for p in state_dir(r).rglob("*"):
        if p.is_file():
            raw = p.read_bytes()
            blob += raw.decode("latin-1")
            if raw[:15] == b"SQLite format 3":
                import sqlite3
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                for (t,) in con.execute("select name from sqlite_master where type='table'").fetchall():
                    blob += "\n".join(" | ".join(map(str, row)) for row in con.execute(f'select * from "{t}"'))
                con.close()
    assert _leaks(blob) == []


def test_hook_and_cli_output_redact_commit_text(make_repo):
    """GW-PRIVACY-002: SessionStart context and CLI JSON never echo a secret found in commit text."""
    r = _secret_repo(make_repo)
    outs = []
    code, out, err = _hook("session_start", {"hook_event_name": "SessionStart", "cwd": str(r.path), "source": "startup"}, r.path)
    outs += [out, err]
    for args in (("xray",), ("pr", "--base=HEAD~15"), ("rescue", "scan"), ("archaeology", "n3.txt"), ("archaeology", "--question=rotate"),
                 ("temporal",), ("commits",), ("memory", "index"), ("memory", "status"), ("memory", "hotspots"),
                 ("memory", "authors"), ("memory", "reverts")):
        rc, o, e = warp(r.path, *args)
        outs += [o, e]
    assert _leaks("\n".join(outs)) == []


def test_secrets_in_hook_payloads_are_not_persisted(repo):
    """GW-PRIVACY-010: secrets only in tool_input / tool_response are never stored or echoed."""
    cmd = " ; ".join(SECRET_TEXT)
    code, out, err = post_tool(repo.path, {"command": cmd, "description": cmd})
    _hook("post_tool", {"hook_event_name": "PostToolUse", "cwd": str(repo.path), "tool_name": "Write",
                        "tool_input": {"file_path": str(repo.path / "f0.txt"), "content": cmd}, "tool_response": {"stdout": cmd}}, repo.path)
    blob = out + err + "".join(p.read_text(errors="replace") for p in state_dir(repo).rglob("*") if p.is_file())
    assert _leaks(blob) == []


# --------------------------------------------------------------------------- agents / plugin surface

def test_no_agent_declares_unscoped_bash():
    """Agents that describe themselves as read-only must not carry unrestricted Bash (prompt text is not enforcement)."""
    import re
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for f in sorted((root / "agents").glob("*.md")):
        m = re.search(r"^tools:\s*(.+)$", f.read_text(), re.M)
        if not m:
            continue
        tools = [t.strip().strip('"\'') for t in re.split(r",\s*(?![^()]*\))", m.group(1).strip("[]"))]
        if "Bash" in tools:
            offenders.append(f.name)
    assert offenders == []
