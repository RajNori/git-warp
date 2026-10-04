"""Forced fetch/pull into local branches, and alias-safe unknown subcommands."""
import subprocess

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import GIT_BUILTINS, classify_command

SEV = {"defer": 0, "ask": 1, "deny": 2}


def v(cmd, branch="feature/x", mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), branch)


@pytest.mark.parametrize("cmd", [
    "git fetch --force origin main:main", "git fetch -f origin main:main", "git fetch origin +refs/heads/*:refs/heads/*",
    "git fetch origin +main:main", "git fetch --update-head-ok origin main:main", "git fetch -u -f origin main:refs/heads/master",
    "git fetch origin +refs/heads/main:refs/heads/develop", "git fetch --force origin '+refs/heads/*:refs/heads/*'",
    "git fetch --forc origin main:main", "git fetch -fq origin master:master", "git fetch origin +x:main", "git fetch origin +*:*",
])
def test_forced_fetch_into_protected_or_glob_local_branch_is_denied(cmd):
    assert v(cmd).decision == "deny" and v(cmd).rule == "fetch-force-protected", cmd


@pytest.mark.parametrize("cmd", [
    "git fetch origin +feature/y:feature/y", "git fetch -f origin topic:topic", "git fetch origin +refs/heads/topic:refs/heads/topic",
    "git fetch -f origin 'x:$B'", "git fetch origin +a:$(echo main)", "git fetch origin +refs/heads/f/*:refs/heads/f/*",
])
def test_forced_fetch_into_other_local_branch_asks(cmd):
    assert v(cmd).decision == "ask" and v(cmd).rule == "fetch-force-local", (cmd, v(cmd).rule)


def test_strict_mode_denies_forced_local_fetch():
    assert v("git fetch -f origin topic:topic", mode="strict").decision == "deny"


@pytest.mark.parametrize("cmd", [
    "git fetch", "git fetch origin", "git fetch origin main", "git fetch --all", "git fetch --prune origin", "git fetch -f origin main",
    "git fetch --force", "git fetch origin main:refs/remotes/origin/main", "git fetch origin +refs/heads/*:refs/remotes/origin/*",
    "git fetch origin +refs/tags/*:refs/tags/*", "git fetch origin main:main-copy-not-forced", "git fetch --depth 1 origin main",
    "git fetch -j 4 origin", "git fetch origin --tags", "git pull", "git pull origin main", "git pull --rebase origin main", "git pull --ff-only",
])
def test_ordinary_fetch_and_pull_stay_deferred(cmd):
    got = v(cmd)
    assert got.decision == "defer", (cmd, got.rule)


@pytest.mark.parametrize("cmd", ["git pull --force", "git pull -f", "git pull -f origin main", "git pull --force --rebase"])
def test_pull_force_asks(cmd):
    assert SEV[v(cmd).decision] >= 1, cmd


def test_pull_force_with_protected_refspec_denies():
    assert v("git pull -f origin +main:main").decision == "deny"


def test_dynamic_force_flag_on_fetch_is_not_deferred():
    assert SEV[v("git fetch $(echo -f) origin main:main").decision] >= 1


# ------------------------------------------------------------------ alias-safe unknown subcommands
@pytest.mark.parametrize("cmd", ["git x", "git nuke", "git st", "git co main", "git unstage f", "git -C dir x", "git -c user.name=a x",
                                  "git --no-pager nuke", "git --git-dir=.git st", "git --git-dir .git st", "FOO=1 git x", "sudo git nuke",
                                  "git !reset", "git $(echo x) y"])
def test_unknown_subcommand_asks_as_possible_alias(cmd):
    got = v(cmd)
    assert got.decision == "ask", (cmd, got.rule)
    if "$(" not in cmd:
        assert got.rule == "unknown-subcommand" and "alias" in got.reason


@pytest.mark.parametrize("cmd", ["git --version", "git --help", "git help foo", "git -C dir status", "git -c core.pager=cat log", "git",
                                  "git -P log", "git --exec-path", "git status", "git log --oneline", "git lfs ls-files", "git svn rebase",
                                  "git subtree add", "git flow init", "git whatchanged", "git stage f",
                                  "git ls-remote origin", "git rev-list HEAD", "git cherry -v", "git maintenance run", "git sparse-checkout list"])
def test_builtins_and_listed_externals_are_unaffected(cmd):
    assert v(cmd).decision == "defer", cmd


def test_alias_does_not_hide_deny_of_literal_builtin():
    assert v("git reset --hard").decision == "deny"


def test_every_subcommand_the_classifier_handles_is_a_builtin():
    from gitwarp.safety.classifier import GIT_SUBS, _HANDLERS
    assert set(_HANDLERS) <= GIT_BUILTINS and GIT_SUBS <= GIT_BUILTINS
