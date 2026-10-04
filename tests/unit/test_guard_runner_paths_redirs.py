"""Runner exemption needs a BARE safe name; only real descriptor operations are not file writes; GIT_DIFF_OPTS digits."""
import os
import shutil
import subprocess

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command
from gitwarp.safety.tokenizer import parse_script


def v(cmd, mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), "feature/x")


# ------------------------------------------------------------------ 1. path-qualified heads / PATH changes
@pytest.mark.parametrize("cmd", [
    "git submodule foreach ./test", "git submodule foreach ./cat", "git submodule foreach ./echo hi", "git submodule foreach /bin/cat x",
    "git submodule foreach ~/bin/test", "git submodule foreach sub/test", "git submodule foreach ../test", "git submodule foreach '$PWD/test'",
    "git submodule foreach './test -f x'", "git submodule foreach 'echo hi && ./test'", "git submodule foreach 'cat x | ./grep y'",
    "git submodule foreach '\"./test\"'", "git submodule foreach $T", "git submodule foreach '$(echo test)'",
    "git bisect run ./test", "git bisect run ./cat x", "git bisect run /usr/bin/true", "git bisect run bin/echo", "git bisect run ~/t", "git bisect run $T",
    "PATH=.:$PATH git submodule foreach test", "export PATH=.:$PATH; git bisect run test -f x", "PATH=./bin git bisect run cat x", "env PATH=. git submodule foreach test",
    "PATH=$PATH:. ; git submodule foreach echo hi", "git submodule foreach 'PATH=. test'", "cd sub && PATH=.:$PATH git bisect run true",
])
def test_path_qualified_or_path_hijacked_runner_asks(cmd):
    got = v(cmd)
    assert got.decision == "ask" and got.rule == "exec-option", (cmd, got.rule)
    assert v(cmd, "strict").decision == "ask"


@pytest.mark.parametrize("cmd", [
    "git submodule foreach test -f x", "git submodule foreach cat x", "git submodule foreach echo hi", "git bisect run test -f x", "git bisect run cat x",
    "git bisect run true", "git bisect run grep -q x f", "git submodule foreach 'echo a && cat b | grep c'", "git submodule foreach 'git status'",
    "git bisect run git status", "FOO=1 git bisect run true", "cd sub && git bisect run true",
])
def test_bare_safe_names_stay_deferred(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


def _env():
    return dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@b",
                GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@b", GIT_ALLOW_PROTOCOL="file")


def _g(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=_env(), stdin=subprocess.DEVNULL)


def test_real_git_runs_a_tracked_script_named_like_a_safe_command(tmp_path):
    """A repo-controlled executable `./test` is run by `submodule foreach` / `bisect run`: both must be ASK."""
    sub = tmp_path / "sub"
    sub.mkdir()
    _g(sub, "init", "-q")
    script = sub / "test"
    script.write_text("#!/bin/sh\necho ran > \"$TOPMARK\"\n")
    script.chmod(0o755)
    _g(sub, "add", "test")
    _g(sub, "commit", "-qm", "s")
    top = tmp_path / "top"
    top.mkdir()
    _g(top, "init", "-q")
    r = _g(top, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "m")
    assert r.returncode == 0, r.stderr
    mark = tmp_path / "MARK"
    env = dict(_env(), TOPMARK=str(mark))
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "foreach", "./test"], cwd=top, env=env, capture_output=True,
                   stdin=subprocess.DEVNULL)
    assert mark.exists(), "real Git really executes the tracked ./test"
    assert v("git submodule foreach ./test").decision == "ask"
    assert v("git bisect run ./test").decision == "ask"
    assert v("git submodule foreach test").decision == "defer"      # bare name resolves via the user's PATH: documented residual


# ------------------------------------------------------------------ 2. redirection operator context
FILE_WRITES = ["echo x > 2", "echo x >2", "echo x 1>2", "echo x > -", "echo x >-", "echo x >> 2", "echo x >>2", "echo x <>2", "echo x <> 2", "echo x &>f",
               "echo x &>>f", "echo x >&f", "echo x >| 2", "echo x 2> 2", "echo x 2>-", "echo x > f", "echo x >&$F"]
DESCRIPTOR_OPS = ["echo x 2>&1", "echo x >&2", "echo x 1>&2", "echo x 2>&-", "echo x >&-", "cat <&0", "echo x 3>&1", "echo x > /dev/null", "echo x 2>/dev/null",
                  "echo x >/dev/null 2>&1"]


@pytest.mark.parametrize("inner", FILE_WRITES)
def test_file_writing_redirections_make_a_runner_ask(inner):
    cmd = f"git submodule foreach '{inner}'"
    got = v(cmd)
    assert got.decision == "ask" and got.rule == "exec-option", (cmd, got.rule)


@pytest.mark.parametrize("inner", DESCRIPTOR_OPS)
def test_descriptor_operations_do_not(inner):
    assert v(f"git submodule foreach '{inner}'").decision == "defer", inner


@pytest.mark.parametrize("inner", FILE_WRITES[:-1])
def test_tokenizer_records_file_writes_only(inner):
    cmds = parse_script(inner)
    assert len([t for c in cmds for t in c.redirs]) == 1, inner


@pytest.mark.parametrize("inner", DESCRIPTOR_OPS[:7])
def test_tokenizer_does_not_record_descriptor_operations(inner):
    assert [t for c in parse_script(inner) for t in c.redirs] == [], inner


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
@pytest.mark.parametrize("inner,creates", [
    ("echo x > 2", "2"), ("echo x >2", "2"), ("echo x 1>2", "2"), ("echo x > -", "-"), ("echo x >> 2", "2"), ("echo x <>2", "2"),
    ("echo x &>f", "f"), ("echo x >&f", "f"), ("echo x >| 2", "2"),
    ("echo x 2>&1", None), ("echo x >&2", None), ("echo x 2>&-", None), ("echo x >&-", None), ("echo x > /dev/null", None),
])
def test_real_shell_agrees_about_which_forms_create_files(tmp_path, inner, creates):
    subprocess.run(["bash", "-c", inner], cwd=tmp_path, capture_output=True, stdin=subprocess.DEVNULL)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ([creates] if creates else []), (inner, files)
    cmd = f"git submodule foreach '{inner}'"
    assert (v(cmd).decision == "ask") == bool(creates), cmd


def test_generated_script_detection_uses_real_writes_only():
    assert v("echo 'git reset --hard' > 2; bash 2").rule == "generated-script"
    assert v("echo hi >&2; bash 2").decision == "defer"           # `>&2` writes nothing: `bash 2` is just a (missing) file
    assert v("echo hi 2>&1; bash 1").decision == "defer"
    assert v("cat > g.sh <<EOF\ngit reset --hard\nEOF\nbash g.sh").rule == "generated-script"
    assert v("echo x | tee g.sh; bash g.sh").rule == "generated-script"


# ------------------------------------------------------------------ 3. GIT_DIFF_OPTS digits
@pytest.mark.parametrize("val", ["--unified=10", "--unified=10000", "--unified=0", "-u10000", "-U99999", "-u3", "-U0"])
def test_diff_opts_any_digit_run_is_allowed(val):
    assert v(f"GIT_DIFF_OPTS={val} git diff").decision == "defer", val


@pytest.mark.parametrize("val", ["--unified=", "--unified=x", "-u", "-U", "-u1x", "--unified=1 --ext-diff", "-u5 -p", "--ext-diff", "--output=f", "$X"])
def test_diff_opts_non_numeric_still_asks(val):
    assert v(f"GIT_DIFF_OPTS='{val}' git diff").decision == "ask" or val == "$X"
    assert " " in val or v(f"GIT_DIFF_OPTS={val} git diff").decision == "ask"
