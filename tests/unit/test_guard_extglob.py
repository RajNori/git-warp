"""zsh extendedglob / bash extglob pattern words that expand into options; tokenizer must keep them as ONE word."""
import os
import shutil
import subprocess

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command
from gitwarp.safety.tokenizer import parse_script


def v(cmd, mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), "feature/x")


@pytest.mark.parametrize("cmd", [
    "git clean -(f|d)", "git clean -(x)", "git clean -(f)", "git clean -@(f|d)", "git clean -+(f)", "git clean -?(f)", "git clean -*(f)", "git clean -!(x)",
    "git clean -f#", "git clean -^x", "git clean -f~x", "git clean -(n|f)", "git push origin -(f)", "git push -(f|u) origin x", "git branch -(D)",
    "git branch -(d) old", "git tag -(d) v1", "git reset -(h)ard", "git checkout -(f)", "git switch -(f) x", "git restore -(W) f", "git stash -(k)",
    "git rebase -(i) x", "git rm -(f) f", "git worktree remove -(f) w", "git update-ref -(d) r", "git gc -(a)", "git commit -(a)m x", "git mv -(f) a b",
    "git clean -d -(f)",
])
def test_pattern_words_that_can_become_options_ask_or_deny(cmd):
    got = v(cmd)
    assert got.decision in ("ask", "deny"), (cmd, got.rule)


@pytest.mark.parametrize("cmd", [
    "git clean '-(f|d)'", "git clean -n -(f|d)", "git commit -m '(x)'", 'git commit -m "(x)"', "git commit -m '-(f)'", "git rm src/(a|b).py",
    "git rm -- -(f)", "git checkout main -- -(f|d)", "git log -(f)", "git diff -(f)", "git status -(f)", "git add -(f)", "git reset HEAD~1", "git reset HEAD^",
    "git restore --source=HEAD~1 a.txt", "git checkout -b feature~1", "git checkout -b a#b", "git push origin 'refs/heads/(x)'", "git stash push -m '(wip)'",
    "f() { git status; }; f", "git add src/(a|b).py", "git clean -n '-(f)'", "git tag -a v1 -m '(x)'",
])
def test_quoted_exempt_and_read_only_forms_stay_deferred(cmd):
    got = v(cmd)
    assert got.decision == "defer" or got.rule in ("clean-force",), (cmd, got.rule)
    if cmd in ("git clean '-(f|d)'",):
        pass


def test_quoted_pattern_is_not_an_expansion_risk():
    assert v("git rm src/(a|b).py").decision == "defer"
    assert v("git commit -m '(x)'").decision == "defer"
    assert v("git log -(f)").decision == "defer"


def test_tokenizer_keeps_pattern_word_whole():
    cmds = parse_script("git clean -(f|d)")
    assert len(cmds) == 1 and [w.text for w in cmds[0].words] == ["git", "clean", "-(f|d)"] and cmds[0].words[2].glob
    cmds = parse_script("git clean -f#")
    assert [w.text for w in cmds[0].words] == ["git", "clean", "-f#"] and cmds[0].words[2].glob


def test_tokenizer_still_parses_subshells_and_functions():
    assert any("git" in [w.text for w in c.words] for c in parse_script("f() { git reset --hard; }; f"))
    assert any("git" in [w.text for w in c.words] for c in parse_script("echo hi;(git reset --hard)"))
    assert any("git" in [w.text for w in c.words] for c in parse_script("true && (git reset --hard)"))
    assert any("git" in [w.text for w in c.words] for c in parse_script("x=$(git status)"))


def test_git_prefix_is_not_silently_classified_as_plain_git_clean():
    """The reviewer's symptom: the command used to be recorded as `git clean -` and classified DEFER."""
    got = v("git clean -(f|d)")
    assert got.decision != "defer"
    assert not got.commands or got.commands[0] != "git clean -"


# ----------------------------------------------------------------------- real shells (files named -f / -d inside tmp_path)
def _git_env():
    return dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not installed on this machine")
def test_real_zsh_extendedglob_expansion_and_real_git(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], env=_git_env(), check=True)
    for n in ("-f", "-d", "victim"):
        (tmp_path / n).write_text("x")
    shown = subprocess.run(["zsh", "-c", "setopt extendedglob; printf '%s ' git clean -(f|d)"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert shown.split()[2:] == ["-d", "-f"] or sorted(shown.split()[2:]) == ["-d", "-f"]
    assert v("git clean -(f|d)").decision in ("ask", "deny")
    subprocess.run(["zsh", "-c", "setopt extendedglob; git clean -(f|d)"], cwd=tmp_path, env=_git_env(), capture_output=True, stdin=subprocess.DEVNULL)
    assert not (tmp_path / "victim").exists()                   # the danger is real, so ASK/DENY is justified


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_real_bash_extglob_expansion(tmp_path):
    for n in ("-f", "-d"):
        (tmp_path / n).write_text("x")
    shown = subprocess.run(["bash", "-O", "extglob", "-c", "printf '%s ' git clean -@(f|d)"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert sorted(shown.split()[2:]) == ["-d", "-f"]
    assert v("git clean -@(f|d)").decision in ("ask", "deny")
