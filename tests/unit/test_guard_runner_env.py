"""Runner exemption (bisect run / submodule foreach) contains only read-only commands; harmless environment allowlist additions."""
import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import RUNNER_SAFE, classify_command


def v(cmd, mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), "feature/x")


@pytest.mark.parametrize("cmd", [
    "git bisect run cp /tmp/payload .git/config", "git submodule foreach cp /tmp/payload .git/config",
    "git bisect run mv a b", "git bisect run rm -f x", "git bisect run touch x", "git bisect run tee x", "git bisect run sed -i s/a/b/ f",
    "git bisect run dd of=x", "git bisect run install a b", "git bisect run ln -s a b", "git bisect run chmod 777 f", "git bisect run curl http://x",
    "git bisect run make", "git bisect run ./test.sh", "git bisect run sh -c 'cp a b'", "git bisect run python3 t.py", "git bisect run sort -o f x",
    "git bisect run uniq a b", "git bisect run find . -delete", "git bisect run awk 'BEGIN{system(\"x\")}'", "git bisect run xargs rm", "git bisect run env X=1 true",
    "git bisect run git checkout x", "git bisect run git commit -m x", "git bisect run git clean -n",
    "git submodule foreach touch a", "git submodule foreach 'echo hi > f'", "git submodule foreach 'echo hi >> f'", "git submodule foreach 'echo hi &> f'",
    "git submodule foreach 'cat a <> f'", "git submodule foreach 'echo a && cp x y'", "git submodule foreach 'echo a; mv x y'", "git submodule foreach 'echo a || rm x'",
    "git submodule foreach 'git status | tee f'", "git submodule foreach 'cat x | sh'", "git submodule foreach 'echo $(cp a b)'", "git submodule foreach 'echo `touch x`'",
    "git submodule foreach 'ls | xargs rm'", "git submodule foreach make", "git submodule foreach --recursive ./run.sh", "git submodule foreach $CMD",
    "git submodule foreach 'git checkout x'", "git bisect run $CMD",
])
def test_runner_commands_with_side_effects_ask(cmd):
    got = v(cmd)
    assert got.decision in ("ask", "deny") and (got.decision == "deny" or got.rule == "exec-option"), (cmd, got.rule)
    assert v(cmd, "strict").decision in ("ask", "deny")


@pytest.mark.parametrize("cmd", [
    "git bisect run echo ok", "git bisect run true", "git bisect run false", "git bisect run test -f x", "git bisect run [ -f x ]", "git bisect run grep -q x f",
    "git bisect run git status", "git bisect run git diff --quiet", "git bisect run git rev-parse HEAD", "git bisect run cat f", "git bisect run ls", "git bisect run diff a b",
    "git submodule foreach 'echo hi'", "git submodule foreach git status", "git submodule foreach 'echo $name'", "git submodule foreach 'pwd'",
    "git submodule foreach 'cat x | grep y'", "git submodule foreach 'echo a && echo b'", "git submodule foreach 'git log --oneline | head -3'",
    "git submodule foreach 'echo x 2>&1'", "git submodule foreach 'ls 2>/dev/null'", "git submodule foreach 'echo $(date)'", "git submodule foreach 'echo $(git rev-parse HEAD)'",
    "git bisect start", "git bisect good", "git bisect bad", "git bisect reset", "git submodule update --init", "git submodule status",
])
def test_read_only_runners_stay_deferred(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd", ["git bisect run git reset --hard", "git submodule foreach 'git clean -fd'", "git bisect run sh -c 'git clean -fd'",
                                  "git submodule foreach 'echo a && git reset --hard'"])
def test_destructive_git_inside_a_runner_is_still_denied(cmd):
    assert v(cmd).decision == "deny", cmd


def test_runner_allowlist_has_no_writer_or_executor():
    forbidden = {"cp", "mv", "rm", "touch", "tee", "sed", "awk", "dd", "install", "ln", "chmod", "chown", "mkdir", "curl", "wget", "make", "sh", "bash",
                 "sort", "uniq", "find", "xargs", "env", "python3", "perl"}
    assert not (RUNNER_SAFE & forbidden)


# ------------------------------------------------------------------ environment allowlist additions
@pytest.mark.parametrize("cmd", [
    "GIT_DIFF_OPTS=--unified=10 git diff", "GIT_DIFF_OPTS=-u5 git diff", "GIT_DIFF_OPTS=-U3 git diff", "GIT_DIFF_OPTS='--unified=0' git diff --stat",
    "GIT_ATTR_NOSYSTEM=1 git diff", "GIT_NO_REPLACE_OBJECTS=1 git log", "GIT_ICASE_PATHSPECS=1 git ls-files", "GIT_GLOB_PATHSPECS=1 git status",
    "GIT_NOGLOB_PATHSPECS=1 git status", "GIT_LITERAL_PATHSPECS=1 git status", "GIT_ADVICE=0 git status", "GIT_REFLOG_ACTION=x git status",
    "GIT_MERGE_VERBOSITY=1 git status", "GIT_FLUSH=1 git log", "GIT_NO_LAZY_FETCH=1 git log",
])
def test_pure_data_environment_variables_are_not_asked(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd", [
    "GIT_DIFF_OPTS=--ext-diff git diff", "GIT_DIFF_OPTS='--output=/tmp/x' git diff", "GIT_DIFF_OPTS=--unified=10x git diff", "GIT_DIFF_OPTS=$X git diff",
    "GIT_DIFF_OPTS='-u5 --ext-diff' git diff", "GIT_SSL_NO_VERIFY=1 git fetch", "GIT_INDEX_FILE=/tmp/i git status", "GIT_DIR=/tmp/r git status",
    "GIT_EXTERNAL_DIFF_TRUST_EXIT_CODE=1 git diff", "GIT_INDEX_VERSION=4 git status", "GIT_REF_PARANOIA=0 git status", "GIT_SSH_VARIANT=ssh git fetch",
])
def test_everything_else_still_asks(cmd):
    got = v(cmd)
    assert got.decision == "ask" and got.rule == "exec-option", (cmd, got.rule)
