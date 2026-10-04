"""Shell glob / brace expansion that can synthesise an option in a mutating Git subcommand -> ASK."""
import os
import subprocess

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command


def v(cmd, branch="feature/x", mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), branch)


@pytest.mark.parametrize("cmd", [
    "git clean -*", "git clean {,-f}", "git clean [-]f", "git clean ?f", "git clean *", "git clean {-f,}", "git clean {1..3}", "git clean -{,f}x*",
    "git push origin -*", "git push {,--force} origin x", "git branch -?", "git branch {,-D} old", "git tag {,-d} v1", "git reset -*", "git checkout -*",
    "git checkout *", "git switch -*", "git restore *.py", "git stash -*", "git rebase -*", "git rm *.pyc", "git rm -*", "git worktree remove -*",
    "git update-ref -* x", "git gc -*", "git reflog -*", "git fetch -*", "git pull {,-f}", "git mv {,-f} a b", "git commit -*", "git merge -*", "git cherry-pick -*",
    "git config -*", "git remote -*", "git archive -*", "git format-patch -*", "git clone -*", "git submodule -*", "git prune -*", "git read-tree -*", "git checkout-index -*",
    "git clean -d {,-f}", "git clean -x -*",
])
def test_unquoted_glob_or_brace_that_can_begin_with_a_dash_asks(cmd):
    got = v(cmd)
    assert got.decision in ("ask", "deny"), (cmd, got.rule)


@pytest.mark.parametrize("cmd", [
    "git rm src/*.py", "git checkout refs/heads/*", "git reset HEAD~{1,2}", "git restore src/*.py", "git add *", "git add *.py", "git log -*", "git diff -*",
    "git status *", "git clean -n -*", "git clean -n *", "git clean --dry-run {,-f}", "git push -n origin -*", "git commit -m '*'", "git commit -m *.txt",
    "git commit -F *.msg", "git clean '-*'", 'git clean "-*"', "git clean \\-\\*", "git rm -- *.pyc", "git checkout main -- {a,b}",
    "git branch --list 'f*'", "git branch --list f*", "git stash push -m '*'", "git tag -a v1 -m {a,b}", "git show HEAD@{1}", "git reset HEAD^{commit}",
    "git ls-files *.py", "git grep -n *", "git push origin refs/heads/*:refs/heads/*", "git fetch origin 'refs/heads/*:refs/remotes/o/*'",
])
def test_exempt_forms_stay_deferred(cmd):
    got = v(cmd)
    assert got.decision == "defer" or got.rule in ("push-force", "push-delete", "branch-force-move"), (cmd, got.rule)


def test_quoted_or_literal_prefixed_words_cannot_become_options():
    for cmd in ("git clean '-*'", "git clean \"{,-f}\"", "git rm 'src/*.py'", "git rm src/*.py"):
        assert v(cmd).decision == "defer", cmd


def test_real_shell_expansion_proves_the_attack(tmp_path):
    """With an untracked file literally named `-f`, `-*` expands to `-f`; both the dash-glob and the brace form really do."""
    (tmp_path / "-f").write_text("x")
    out = subprocess.run(["sh", "-c", "printf '%s ' git clean -*"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert out.split()[-1] == "-f"
    out = subprocess.run(["bash", "-c", "printf '%s ' git clean {,-f}"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert out.split()[-1] == "-f"
    assert v("git clean -*").decision == "ask" and v("git clean {,-f}").decision == "ask"
    assert (tmp_path / "-f").exists()                       # nothing was executed


def test_real_git_would_have_deleted_so_ask_is_justified(tmp_path):
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    subprocess.run(["git", "init", "-q", str(tmp_path)], env=env, check=True)
    (tmp_path / "-f").write_text("x")
    (tmp_path / "victim").write_text("x")
    subprocess.run(["sh", "-c", "git clean -*"], cwd=tmp_path, env=env, capture_output=True, stdin=subprocess.DEVNULL)
    assert not (tmp_path / "victim").exists()


def test_strict_mode_keeps_ask():
    assert v("git clean -*", mode="strict").decision == "ask"
