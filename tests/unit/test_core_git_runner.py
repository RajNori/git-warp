"""core/git.py runner: argv/env/cwd policy, bounded output, process-group kill, refusal policy.

Stand-in ``git`` executables placed first on PATH observe exactly what the runner executes; the real git is used for
the behavioural checks.
"""
import json
import os
import stat
import time

import pytest

from gitwarp.core import git

HOSTILE_ENV = {
    "GIT_DIR": "/nonexistent/dir", "GIT_WORK_TREE": "/nonexistent/wt", "GIT_INDEX_FILE": "/nonexistent/index",
    "GIT_OBJECT_DIRECTORY": "/nonexistent/obj", "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/nonexistent/alt",
    "GIT_COMMON_DIR": "/nonexistent/common", "GIT_NAMESPACE": "ns", "GIT_CEILING_DIRECTORIES": "/",
    "GIT_CONFIG": "/nonexistent/cfg", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
    "GIT_CONFIG_VALUE_0": "/bin/evil", "GIT_CONFIG_PARAMETERS": "'core.fsmonitor=/bin/evil'",
    "GIT_CONFIG_GLOBAL": "/nonexistent/global", "GIT_CONFIG_SYSTEM": "/nonexistent/system", "GIT_CONFIG_NOSYSTEM": "",
    "GIT_EXTERNAL_DIFF": "/bin/evil", "GIT_DIFF_OPTS": "--output=x", "GIT_SSH": "/bin/evil", "GIT_SSH_COMMAND": "/bin/evil",
    "GIT_ASKPASS": "/bin/evil", "SSH_ASKPASS": "/bin/evil", "GIT_PAGER": "/bin/evil", "PAGER": "/bin/evil",
    "GIT_EDITOR": "/bin/evil", "EDITOR": "/bin/evil", "VISUAL": "/bin/evil", "GIT_SEQUENCE_EDITOR": "/bin/evil",
    "GIT_EXEC_PATH": "/nonexistent/exec", "GIT_TRACE": "1", "GIT_TRACE2": "/nonexistent/t2", "GIT_TRACE_PACKET": "1",
    "GIT_TRACE_SETUP": "1", "GIT_TRACE2_EVENT": "/nonexistent/t2e", "GIT_PROXY_COMMAND": "/bin/evil",
    "GIT_ALLOW_PROTOCOL": "ext", "GIT_FLUSH": "1", "GIT_ATTR_SOURCE": "HEAD", "GIT_REPLACE_REF_BASE": "refs/x/",
    "LD_PRELOAD": "/nonexistent.so", "DYLD_INSERT_LIBRARIES": "/nonexistent.dylib", "LANG": "de_DE.UTF-8", "LC_MESSAGES": "de_DE",
}


def _fake_git(tmp_path, body, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "git"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return exe


def _dump_env_git(tmp_path, seen, monkeypatch):
    script = tmp_path / "dump_env.py"
    script.write_text(f"import json, os\njson.dump(dict(os.environ), open({str(seen)!r}, 'w'))\n")
    return _fake_git(tmp_path, f'exec python3 {script}\n', monkeypatch)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------- argv / env / cwd

def test_argv_env_and_cwd_policy(tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    _fake_git(tmp_path, f'python3 - "$@" <<\'EOF\'\nimport json, os, sys\n'
                        f'json.dump({{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(), '
                        f'"stdin_tty": os.isatty(0)}}, open({str(seen)!r}, "w"))\nEOF\n', monkeypatch)
    for k, v in HOSTILE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    r = git.run(["rev-parse", "HEAD"], cwd=work)
    assert r.ok
    data = json.loads(seen.read_text())
    argv, env = data["argv"], data["env"]
    assert os.path.realpath(data["cwd"]) == os.path.realpath(work)
    # global options come first, the subcommand last (before its own args)
    assert argv[:2] == ["--no-pager", "--no-optional-locks"]
    assert argv[-2:] == ["rev-parse", "HEAD"]
    pairs = dict(a.split("=", 1) for a in argv if "=" in a and not a.startswith("-"))
    for key, val in {"core.fsmonitor": "false", "core.hooksPath": os.devnull, "core.pager": "cat", "core.untrackedCache": "false",
                     "diff.external": "", "core.sshCommand": "", "credential.helper": "", "core.askPass": "",
                     "protocol.ext.allow": "never", "protocol.allow": "never", "core.quotePath": "false"}.items():
        assert pairs.get(key) == val, key
    assert argv.count("-c") >= 11
    # environment: every GIT_* is dropped except the explicit safe set; nothing hostile leaks
    gits = {k: v for k, v in env.items() if k.startswith("GIT_")}
    assert gits == {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "GIT_EDITOR": "true",
                    "GIT_ATTR_NOSYSTEM": "1"}
    assert env["LC_ALL"] == "C" and env["PAGER"] == "cat"
    for k in ("EDITOR", "VISUAL", "SSH_ASKPASS", "LANG", "LC_MESSAGES", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
        assert k not in env, k
    assert env["HOME"] == str(tmp_path) and "PATH" in env            # ordinary variables are kept
    assert data["stdin_tty"] is False


def test_inert_config_redirection_values_survive(tmp_path, monkeypatch):
    seen = tmp_path / "env.json"
    _dump_env_git(tmp_path, seen, monkeypatch)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git.run(["rev-parse", "HEAD"], cwd=tmp_path)
    env = json.loads(seen.read_text())
    assert (env["GIT_CONFIG_GLOBAL"], env["GIT_CONFIG_SYSTEM"], env["GIT_CONFIG_NOSYSTEM"]) == (os.devnull, os.devnull, "1")


def test_caller_env_cannot_reintroduce_git_variables(tmp_path, monkeypatch):
    seen = tmp_path / "env.json"
    _dump_env_git(tmp_path, seen, monkeypatch)
    git.run(["rev-parse", "HEAD"], cwd=tmp_path, env={"GIT_DIR": "/x", "GIT_TRACE": "1", "EDITOR": "vi", "MY_VAR": "ok"})
    env = json.loads(seen.read_text())
    assert "GIT_DIR" not in env and "GIT_TRACE" not in env and "EDITOR" not in env and env["MY_VAR"] == "ok"


def test_scrubbed_env_never_mutates_or_logs_the_parent(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/secret-token-value")
    before = dict(os.environ)
    env = git.scrubbed_env()
    assert dict(os.environ) == before and "GIT_DIR" not in env
    with pytest.raises(git.GitRefused) as ei:
        git.run(["push"], cwd=".")
    assert "secret-token-value" not in str(ei.value)


def test_explicit_cwd_even_when_none_given(tmp_path, monkeypatch):
    seen = tmp_path / "cwd.txt"
    _fake_git(tmp_path, f'pwd > {seen}\n', monkeypatch)
    monkeypatch.chdir(tmp_path)
    git.run(["rev-parse", "HEAD"])
    assert os.path.realpath(seen.read_text().strip()) == os.path.realpath(tmp_path)


# --------------------------------------------------------------------------- bounded output

def test_stdout_is_capped_and_reported(tmp_path, monkeypatch):
    _fake_git(tmp_path, 'yes 0123456789abcdef | head -c 50000000\n', monkeypatch)
    t0 = time.monotonic()
    r = git.run(["rev-parse", "HEAD"], cwd=tmp_path, max_output=100_000, timeout=30)
    assert r.truncated is True
    assert 0 < len(r.stdout) <= 100_000
    assert r.ok                               # the captured prefix is the result; callers check .truncated
    assert time.monotonic() - t0 < 20


def test_stderr_is_capped_too(tmp_path, monkeypatch):
    _fake_git(tmp_path, 'yes err | head -c 20000000 >&2\necho out\nexit 3\n', monkeypatch)
    r = git.run(["rev-parse", "HEAD"], cwd=tmp_path, max_stderr=50_000, timeout=30)
    assert r.truncated is True and len(r.stderr) <= 50_000


def test_small_output_is_not_truncated(repo):
    r = git.run(["rev-parse", "HEAD"], cwd=repo.path)
    assert r.ok and r.truncated is False and r.text == repo.sha()
    assert git.Result(("x",), 0, "a\n", "").truncated is False        # backwards-compatible constructor


# --------------------------------------------------------------------------- timeout / process group

def test_timeout_kills_the_whole_process_group(tmp_path, monkeypatch):
    pidfile = tmp_path / "child.pid"
    _fake_git(tmp_path, f'sleep 60 &\necho $! > {pidfile}\nwait\n', monkeypatch)
    t0 = time.monotonic()
    with pytest.raises(git.GitTimeout):
        git.run(["rev-parse", "HEAD"], cwd=tmp_path, timeout=1.0)
    assert time.monotonic() - t0 < 15
    pid = int(pidfile.read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), "helper child survived the timeout"


def test_lingering_descendant_does_not_hang_or_survive(tmp_path, monkeypatch):
    pidfile = tmp_path / "bg.pid"
    _fake_git(tmp_path, f'sleep 60 &\necho $! > {pidfile}\necho done\n', monkeypatch)   # leader exits, child keeps the pipes open
    t0 = time.monotonic()
    r = git.run(["rev-parse", "HEAD"], cwd=tmp_path, timeout=30)
    assert r.ok and r.text == "done"
    assert time.monotonic() - t0 < 10
    pid = int(pidfile.read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid)


def test_input_is_delivered_and_large_input_does_not_deadlock(repo):
    head = repo.sha()
    shas = f"{head}\n" * 20000
    r = git.run(["cat-file", "--batch-check"], cwd=repo.path, input=shas, timeout=30)
    assert r.ok and len(r.lines) == 20000


def test_typed_errors(tmp_path, monkeypatch):
    with pytest.raises(git.NotARepository):
        git.run(["status"], cwd=tmp_path / "does-not-exist")
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(git.GitNotFound):
        git.run(["status"], cwd=tmp_path)
    assert issubclass(git.GitRefused, git.GitError)


def test_check_flag_raises_on_failure(repo):
    with pytest.raises(git.GitError):
        git.run(["rev-parse", "--verify", "nope-nope"], cwd=repo.path, check=True)


# --------------------------------------------------------------------------- command policy

REFUSED = [
    ["push", "origin", "main"], ["commit", "-m", "x"], ["checkout", "-f", "main"], ["reset", "--hard"], ["clean", "-fd"],
    ["add", "-A"], ["merge", "x"], ["rebase", "x"], ["gc"], ["update-ref", "-d", "refs/heads/main"], ["tag", "x"],
    ["fetch"], ["clone", "x"], ["filter-branch"], ["restore", "."], ["rm", "-r", "."], ["init"], ["submodule", "update"],
    ["st"], ["slow"],                                                    # aliases / unknown builtins
    ["config", "user.name", "x"], ["config", "--add", "a.b", "c"], ["config", "--unset", "core.bare"],
    ["config", "--global", "--get", "user.name"], ["config", "--edit"],
    ["branch", "-D", "main"], ["branch", "-m", "a", "b"], ["branch", "-f", "main", "HEAD"], ["branch", "--set-upstream-to=x"],
    ["branch"], ["branch", "only-name"],
    ["stash", "drop"], ["stash", "push"], ["stash"], ["worktree", "add", "x"], ["worktree", "remove", "x"],
    ["reflog", "expire", "--all"], ["reflog", "delete", "HEAD@{0}"], ["symbolic-ref", "HEAD", "refs/heads/x"],
    ["symbolic-ref", "-d", "HEAD"],
    ["log", "--output=/tmp/x"], ["diff", "--output", "/tmp/x"], ["log", "--outp=/tmp/x"], ["show", "--ext-diff", "HEAD"],
    ["diff", "--textconv"], ["grep", "-O", "x"], ["grep", "--open-files-in-pager=x", "y"], ["fsck", "--lost-found"],
    ["rev-parse", "--git-dir=/x"], ["rev-parse", "--work-tree=/x"], ["log", "--exec-path=/x"],
    ["-c", "core.fsmonitor=/bin/evil", "status"], ["-c", "alias.x=!id", "x"], ["-C", "/tmp", "status"], ["--git-dir=/x", "status"],
    ["--work-tree=/x", "status"], ["--exec-path=/x", "status"], ["--config-env=a=B", "status"], ["-c"], [], ["--help"], ["-h"],
]


@pytest.mark.parametrize("args", REFUSED, ids=[" ".join(a) or "empty" for a in REFUSED])
def test_non_allowlisted_or_dangerous_commands_are_refused(repo, args):
    before = (repo.sha(), repo.git("status", "--porcelain"))
    with pytest.raises(git.GitRefused):
        git.run(args, cwd=repo.path)
    assert (repo.sha(), repo.git("status", "--porcelain")) == before


def test_alias_is_never_invoked(repo, tmp_path):
    marker = tmp_path / "alias-ran"
    repo.git("config", "alias.st", f"!touch {marker}")
    repo.git("config", "alias.log", f"!touch {marker}")           # an alias cannot shadow a builtin either
    with pytest.raises(git.GitRefused):
        git.run(["st"], cwd=repo.path)
    assert git.log_commits("HEAD", cwd=repo.path)
    assert not marker.exists()


def test_exact_allowed_mutations_work(repo):
    sha = repo.sha()
    assert git.run(["branch", "rescue/x", sha], cwd=repo.path).ok
    assert repo.git("rev-parse", "rescue/x") == sha


def test_allowed_read_forms_work(repo):
    repo.git("config", "user.name", "Someone")
    assert git.run(["config", "--get", "user.name"], cwd=repo.path).text == "Someone"
    assert git.run(["config", "--get-regexp", "^user\\."], cwd=repo.path).ok
    assert git.run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo.path).text == "main"
    assert git.run(["stash", "list"], cwd=repo.path).ok
    assert git.run(["worktree", "list", "--porcelain"], cwd=repo.path).ok
    assert git.run(["reflog", "show", "-n1", "HEAD"], cwd=repo.path).ok
    assert git.run(["--literal-pathspecs", "-c", "core.quotepath=off", "diff", "--stat", "HEAD", "--"], cwd=repo.path).ok
    assert git.run(["grep", "-I", "-l", "-F", "-e", "--output=x", "HEAD"], cwd=repo.path).returncode in (0, 1)   # pattern value after -e


def test_data_after_double_dash_is_not_mistaken_for_options(repo):
    # a path literally called "--output=x" is data after "--"; the runner must not refuse it
    r = git.run(["log", "-n1", "--format=%H", "HEAD", "--", "--output=x"], cwd=repo.path)
    assert r.ok


def test_patch_commands_get_no_ext_diff_and_no_textconv(tmp_path, monkeypatch):
    seen = tmp_path / "argv.txt"
    _fake_git(tmp_path, f'printf "%s\\n" "$@" > {seen}\n', monkeypatch)
    for sub, expect in (("diff", ["--no-ext-diff", "--no-textconv"]), ("log", ["--no-ext-diff", "--no-textconv"]),
                        ("show", ["--no-ext-diff", "--no-textconv"]), ("blame", ["--no-textconv"])):
        git.run([sub, "x"], cwd=tmp_path)
        argv = seen.read_text().split("\n")
        i = argv.index(sub)
        assert argv[i + 1:i + 1 + len(expect)] == expect, sub
    git.run(["rev-list", "x"], cwd=tmp_path)
    assert "--no-ext-diff" not in seen.read_text()


def test_state_dir_wrapper_still_delegates_to_storage(repo):
    d = git.state_dir(repo.path, create=True)
    assert d.name == "git-warp" and d.is_dir()
