"""Execution-bearing options, environment assignments and config keys -> ASK; plain read forms stay DEFER."""
import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command


def v(cmd, branch="feature/x", mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), branch)


@pytest.mark.parametrize("cmd", [
    "git diff --ext-diff", "git diff --ext-diff HEAD~1", "git log -p --ext-diff", "git show --textconv HEAD:f", "git diff --textconv", "git blame --textconv f",
    "git diff --ext=1", "git fetch --upload-pack=/p/helper origin", "git fetch --upload-pack /p/helper origin", "git pull --upload-pack=/p/h",
    "git push --receive-pack=/p/helper origin main", "git push --exec=/p/h origin main", "git ls-remote --upload-pack=/p/h origin",
    "git ls-remote --exec=/p/h origin", "git archive --remote=host:r HEAD", "git archive --exec=/p/h --remote=r HEAD",
    "git clone --upload-pack=/p/h r d", "git clone -u /p/h r d", "git clone --template=/t r d", "git clone -c core.fsmonitor=/p/h r d",
    "git clone --config core.sshCommand=/p/h r d", "git submodule update --upload-pack=/p/h",
    "git difftool", "git difftool -y HEAD~1", "git mergetool", "git instaweb", "git daemon --export-all", "git http-backend", "git http-fetch x", "git http-push x",
    "git credential fill", "git credential-store get", "git credential-cache store", "git send-email p.patch", "git imap-send", "git gui", "git gitk",
    "git citool", "git remote-ext x y", "git -C d difftool", "sudo git diff --ext-diff", "git upload-pack .", "git receive-pack .",
])
def test_exec_bearing_options_and_subcommands_ask(cmd):
    got = v(cmd)
    assert got.decision == "ask" and got.rule == "exec-option", (cmd, got.rule)
    assert v(cmd, mode="strict").decision == "ask"


@pytest.mark.parametrize("cmd", [
    "GIT_EXTERNAL_DIFF=/p/h git diff", "GIT_SSH=/p/h git fetch", "GIT_SSH_COMMAND='/p/h -x' git fetch origin", "GIT_PAGER=/p/h git log",
    "GIT_EDITOR=/p/h git commit", "GIT_SEQUENCE_EDITOR=/p/h git rebase -i HEAD~2", "GIT_ASKPASS=/p/h git push origin main",
    "SSH_ASKPASS=/p/h git fetch", "GIT_PROXY_COMMAND=/p/h git fetch", "export GIT_SSH_COMMAND=/p/h; git fetch", "GIT_SSH=/p/h; export GIT_SSH; git fetch",
    "env GIT_EXTERNAL_DIFF=/p/h git diff", "env -i GIT_PAGER=/p/h git log", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=/p/h git status",
    "GIT_CONFIG_KEY_0=core.sshCommand git fetch", "echo '[core]' > g.cfg; GIT_CONFIG_GLOBAL=g.cfg git status",
    "echo x > ./g.cfg && GIT_CONFIG_SYSTEM=g.cfg git log", "GIT_SSH_COMMAND=$X git fetch", "GIT_TEMPLATE_DIR=/t git init",
])
def test_exec_environment_assignments_ask(cmd):
    got = v(cmd)
    assert got.decision in ("ask", "deny") and got.rule in ("exec-option", "generated-script"), (cmd, got.rule)


@pytest.mark.parametrize("cmd", [
    "git -c remote.o.uploadpack=/p/h fetch o", "git -c remote.o.receivepack=/p/h push o", "git -c remote.o.vcs=x fetch o", "git -c remote.o.proxy=x fetch o",
    "git -c core.gitProxy=x fetch", "git -c core.sshCommand=x fetch", "git -c core.fsmonitor=x status", "git -c core.hooksPath=/h commit",
    "git -c core.editor=x commit", "git -c sequence.editor=x rebase -i HEAD~1", "git -c core.askPass=x push", "git -c core.pager=x log", "git -c pager.diff=x diff",
    "git -c diff.external=x diff", "git -c diff.d.command=x diff", "git -c diff.d.textconv=x diff", "git -c merge.d.driver=x merge b", "git -c filter.f.clean=x add f",
    "git -c filter.f.smudge=x checkout b", "git -c filter.f.process=x add f", "git -c credential.helper=x push", "git -c credential.https://h.helper=x push",
    "git -c gpg.program=x commit", "git -c gpg.openpgp.program=x commit", "git -c gpg.ssh.defaultKeyCommand=x commit", "git -c sendemail.smtpServer=x send-email",
    "git -c browser.b.cmd=x instaweb", "git -c man.m.cmd=x help x", "git -c instaweb.browser=x log", "git -c web.browser=x log",
    "git config core.fsmonitor /p/h", "git config --global core.sshCommand x", "git config remote.o.uploadpack /p/h", "git config sendemail.smtpServer x",
    "git config diff.d.textconv x", "git config filter.f.clean x", "git config gpg.program x",
])
def test_program_running_config_keys_ask(cmd):
    got = v(cmd)
    assert got.decision == "ask" and got.rule in ("config-exec", "exec-option"), (cmd, got.rule)


@pytest.mark.parametrize("cmd", [
    "git diff", "git diff --stat", "git diff --cached", "git diff --no-ext-diff", "git fetch origin", "git fetch", "git push origin main", "git log -p",
    "git log --oneline --stat", "git show HEAD", "git show HEAD:f", "git blame f", "git clone r d", "git clone --depth 1 r d", "git ls-remote origin", "git archive HEAD",
    "git pull", "git submodule update --init", "git fetch -u origin", "git fetch --update-head-ok origin",
    "GIT_PAGER=cat git log", "GIT_PAGER=less git log", "GIT_EDITOR=true git commit", "GIT_EDITOR=: git commit", "FOO=bar git status",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x git status", "GIT_TRACE=0 git status", "GIT_CONFIG_GLOBAL=/dev/null git status",
    "git -c core.pager=cat log", "git -c pager.log=less log", "git -c color.ui=never diff", "git -c url.x.insteadOf=y fetch", "git -c user.name=a commit -m x",
    "git config user.name x", "git config --get core.pager", "git config url.x.insteadOf y", "git config --list", "git clone -c user.name=x r d",
    "git bundle verify b", "git hash-object f", "git help log",
])
def test_plain_forms_stay_deferred(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


def test_rebase_exec_and_bisect_run_keep_their_own_analysis():
    assert v("git rebase --exec 'git reset --hard' main").decision == "deny"
    assert v("git rebase -x make main").rule == "rebase"
    assert v("git bisect run git reset --hard").decision == "deny"
    assert v("git submodule foreach 'git reset --hard'").decision == "deny"
    assert v("git filter-branch --tree-filter x").decision == "deny"


def test_deny_beats_exec_option():
    assert v("GIT_SSH_COMMAND=x git reset --hard").decision == "deny"
