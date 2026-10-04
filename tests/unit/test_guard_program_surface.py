"""Program-running surface (derived from Git 2.53 documentation) is ASK; harmless forms stay DEFER; drift guard vs the docs."""
import os
import re
import subprocess

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command


def v(cmd, mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), "feature/x")


# Small, explicit, checked-in expectations (documented in `git help config` / `git help git`, Git 2.53).
EXPECTED_EXEC_CONFIG_KEYS = [
    "core.fsmonitor", "core.gitProxy", "core.sshCommand", "core.alternateRefsCommand", "core.askPass", "core.hooksPath", "core.editor",
    "core.pager", "credential.helper", "diff.external", "diff.<driver>.command", "diff.<driver>.textconv", "difftool.<tool>.cmd",
    "filter.<driver>.clean", "filter.<driver>.smudge", "filter.<driver>.process", "gc.recentObjectsHook", "gpg.program", "gpg.<format>.program",
    "gpg.ssh.defaultKeyCommand", "guitool.<name>.cmd", "help.browser", "imap.tunnel", "init.templateDir", "instaweb.browser", "instaweb.httpd",
    "interactive.diffFilter", "man.viewer", "man.<tool>.cmd", "merge.<driver>.driver", "mergetool.<tool>.cmd", "mergetool.<tool>.path",
    "pager.<cmd>", "remote.<name>.proxy", "remote.<name>.receivepack", "remote.<name>.uploadpack", "remote.<name>.vcs", "sequence.editor",
    "trailer.<keyAlias>.command", "uploadpack.packObjectsHook", "browser.<tool>.cmd", "web.browser",
]
EXPECTED_EXEC_ENV = ["GIT_EXTERNAL_DIFF", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_ASKPASS",
                     "GIT_PROXY_COMMAND", "GIT_CONFIG_PARAMETERS", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "GIT_DIR", "GIT_WORK_TREE",
                     "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_COMMON_DIR",
                     "GIT_ALLOW_PROTOCOL", "GIT_PROTOCOL_FROM_USER", "GIT_ATTR_SOURCE", "GIT_SSL_NO_VERIFY", "GIT_TEST_FOO"]


def _concrete(key):
    return re.sub(r"<[^>]+>", "x", key)


@pytest.mark.parametrize("key", EXPECTED_EXEC_CONFIG_KEYS)
def test_expected_program_running_config_keys_ask(key):
    k = _concrete(key)
    assert v(f"git -c {k}=/tmp/helper status").decision == "ask", k
    assert v(f"git config {k} /tmp/helper").decision == "ask", k


@pytest.mark.parametrize("name", EXPECTED_EXEC_ENV)
def test_expected_environment_variables_ask(name):
    for cmd in (f"{name}=/tmp/x git status", f"export {name}=/tmp/x; git status", f"env {name}=/tmp/x git status", f"{name}=/tmp/x\ngit status"):
        got = v(cmd)
        assert got.decision == "ask" and got.rule == "exec-option", (cmd, got.rule)


def _git_help(topic):
    env = dict(os.environ, MANPAGER="cat", PAGER="cat", GIT_PAGER="cat", MANWIDTH="200", TERM="dumb")
    try:
        r = subprocess.run(f"git help {topic} 2>/dev/null | col -b", shell=True, capture_output=True, text=True, env=env, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout


def test_drift_guard_documented_keys_in_the_expectation_list_are_asked():
    """Only keys that are IN the checked-in list AND still documented by the installed Git are enforced."""
    doc = _git_help("config")
    if not doc:
        pytest.skip("git manual not available")
    checked = 0
    for key in EXPECTED_EXEC_CONFIG_KEYS:
        if key in doc or key.replace("<", "").replace(">", "") in doc:
            checked += 1
            assert v(f"git -c {_concrete(key)}=/tmp/helper status").decision == "ask", key
    assert checked > 0


def test_drift_guard_documented_environment_variables_in_the_expectation_list_are_asked():
    doc = _git_help("git")
    if not doc:
        pytest.skip("git manual not available")
    checked = 0
    for name in EXPECTED_EXEC_ENV:
        if name in doc:
            checked += 1
            assert v(f"{name}=/tmp/x git status").decision == "ask", name
    assert checked > 0


@pytest.mark.parametrize("cmd", [
    "GIT_CONFIG_PARAMETERS=\"'core.fsmonitor=/tmp/helper'\" git status", "git config --edit", "git config -e", "git config --global --edit",
    "git config --local -e", "git config edit", "git bisect run /tmp/helper", "git bisect run make test", "git bisect run ./test.sh",
    "git hook run pre-commit", "git help -w log", "git help -m log", "git help -i log", "git help --web log", "git web--browse http://x",
    "git merge -s evil b", "git merge --strategy=evil b", "git pull -sevil", "git rebase -s evil main", "git --exec-path=/tmp/x status",
    "git fetch ext::sh", "git clone 'ext::sh -c x' d", "git init --template=/t", "git -c protocol.ext.allow=always fetch",
    "git replace --edit abc", "git submodule foreach make", "git submodule foreach --recursive ./run.sh", "git bisect run $CMD",
    "GIT_CONFIG_GLOBAL=/tmp/evil git status", "GIT_TRACE=/tmp/t git status", "GIT_TRACE2_EVENT=/tmp/t git status", "GIT_PAGER=/tmp/h git log",
    "LD_PRELOAD=/tmp/x.so git status", "SSH_ASKPASS=/tmp/h git fetch", "GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=/h GIT_CONFIG_COUNT=1 git status",
])
def test_reviewer_and_survey_cases_ask(cmd):
    assert v(cmd).decision in ("ask", "deny"), (cmd, v(cmd).rule)
    assert v(cmd, mode="strict").decision in ("ask", "deny")


@pytest.mark.parametrize("cmd", [
    "git config user.name x", "git config --get core.pager", "git config --list", "git config -l", "git config --global user.email a@b",
    "git config --global --get-all x.y", "git config --unset x.y", "git bisect start", "git bisect good", "git bisect run git status",
    "git bisect run echo ok", "git submodule foreach 'echo hi'", "git submodule foreach git status", "git hook list x", "git help log", "git help -a",
    "git merge -s ort b", "git merge -s recursive -X theirs b", "git merge -s ours b", "git pull --strategy=resolve", "git fetch origin", "git clone r d",
    "GIT_AUTHOR_NAME=a GIT_AUTHOR_EMAIL=a@b GIT_AUTHOR_DATE=now git commit -m x", "GIT_COMMITTER_NAME=a git commit -m x", "GIT_TERMINAL_PROMPT=0 git fetch",
    "GIT_OPTIONAL_LOCKS=0 git status", "GIT_MERGE_AUTOEDIT=no git merge b", "GIT_CONFIG_NOSYSTEM=1 git status", "GIT_PAGER=cat git log",
    "GIT_EDITOR=true git commit", "GIT_TRACE=1 git status", "GIT_TRACE=0 git fetch", "GIT_CONFIG_GLOBAL=/dev/null git status",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x git status", "GIT_CEILING_DIRECTORIES=/tmp git status",
    "FOO=1 git status", "PATH_X=1 git status", "echo GIT_EXEC_PATH=x", "GIT_EXEC_PATH=x echo hi", "GIT_DIR=x ls",
])
def test_harmless_forms_stay_deferred(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


def test_destructive_still_denied_with_exec_context():
    assert v("GIT_SSH=x git reset --hard").decision == "deny"
    assert v("git bisect run git reset --hard").decision == "deny"
    assert v("git submodule foreach 'git clean -fd'").decision == "deny"


def test_bisect_run_baseline_changed_to_ask():
    """`git bisect run make test` used to DEFER: git itself executes the program, so approving `git bisect:*` would cover it."""
    got = v("git bisect run make test")
    assert got.decision == "ask" and got.rule == "exec-option"
