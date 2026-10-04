"""Guard hardening regressions (security review M1, M2, M4, M5, H1): dynamic flags, piped echo, extra rules, launchers."""
import time

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command, is_all_pathspec

SEV = {"defer": 0, "ask": 1, "deny": 2}


def v(cmd, branch="feature/x", mode="standard"):
    t0 = time.monotonic()
    out = classify_command(cmd, Config(safety_mode=mode), branch)
    assert time.monotonic() - t0 < 3.0
    return out


def at_least(cmd, floor, branches=("feature/x", "main", None)):
    for b in branches:
        got = v(cmd, b)
        assert SEV[got.decision] >= SEV[floor], (cmd, b, got.decision, got.rule)


# ------------------------------------------------------------------ M1: dynamic flags / heads
@pytest.mark.parametrize("cmd", [
    "git reset --hard$IFS", "git${IFS}reset${IFS}--hard", "git$IFS reset --hard", "$(echo git) reset --hard",
    "`echo git` reset --hard", '"$(which git)" reset --hard', "GIT=git; $GIT reset --hard", "$GIT reset --hard",
    "git clean -f$X", "git clean -${X}f", "git reset --ha$X", "git push --force$X origin x",
    "git${IFS}clean${IFS}-fd", "${GIT:-git} reset --hard",
])
def test_dynamic_option_or_head_is_at_least_ask(cmd):
    at_least(cmd, "ask")


@pytest.mark.parametrize("cmd", ["git reset --hard$IFS", "git${IFS}reset${IFS}--hard", "$GIT reset --hard", '"$(which git)" reset --hard'])
def test_literal_part_proving_destruction_is_denied(cmd):
    assert v(cmd).decision == "deny"


def test_dynamic_flag_on_destructive_subcommand_without_other_rule_asks():
    got = v("git branch --delete$X foo")
    assert got.decision == "ask"
    assert v("git restore --worktree$X f").decision == "ask"


@pytest.mark.parametrize("cmd", [
    "git status", "git commit -m 'reset --hard'", 'git commit -m "$MSG"', "echo git reset --hard", "git log --grep=reset",
    "grep -r 'git clean -fd' .", 'git checkout -b "feature-$X"', "git diff $FILES",
    "git commit -m \"fix $X\" --no-verify", "$EDITOR notes.txt", "$CC -o out main.c", "git stash push -m \"wip $X\"",
    'git log --since="$D" --oneline',
])
def test_dynamic_false_positives_stay_allowed(cmd):
    assert v(cmd).decision == "defer", cmd


def test_quoted_variable_operand_in_push_is_ask_because_it_can_be_a_deletion_refspec():
    """Changed from defer: BRANCH=:dev makes `git push origin "$BRANCH"` delete the remote branch (demonstrated by review)."""
    got = v('git push origin "$BRANCH"')
    assert got.decision == "ask" and got.rule == "dynamic-argument"


# ------------------------------------------------------------------ M2: echo / printf piped into a shell
@pytest.mark.parametrize("cmd,rule", [
    ("echo git reset --hard | sh", "reset-hard"), ("echo git reset --hard | bash -s", "reset-hard"),
    ("printf %s git reset --hard | bash", "reset-hard"), ("echo -n git clean -fd | sh", "clean-force"),
    ("echo -e git reset --hard | zsh", "reset-hard"), ("echo git clean -f | sh -s", "clean-force"),
    ("printf '%s\\n' 'git reset --hard' | sh", "reset-hard"), ("printf 'git reset --hard\\n' | bash", "reset-hard"),
    ("echo 'git reset --hard' | sh", "reset-hard"), ("echo git push --force origin main | sh", "push-force-protected"),
])
def test_piped_echo_is_analysed(cmd, rule):
    got = v(cmd, "main")
    assert got.decision == "deny" and got.rule == rule, (cmd, got.rule)


@pytest.mark.parametrize("cmd", [
    "echo git reset --hard", "echo git reset --hard > notes.txt", "printf %s git reset --hard", "echo git reset --hard | cat",
    "echo git reset --hard | grep reset", "echo hello | sh", "printf %s hello | bash", "echo -n | sh",
])
def test_echo_not_piped_to_shell_stays_allowed(cmd):
    assert v(cmd).decision == "defer", cmd


# ------------------------------------------------------------------ M4: extra destructive operations
def test_checkout_B_and_switch_C_on_protected_or_unknown_branch_ask():
    for cmd in ("git checkout -B main", "git switch -C main", "git checkout -B master HEAD~3", "git switch --force-create main",
                "git checkout -B $X", "git switch -C \"$B\""):
        assert v(cmd, "feature/x").decision == "ask", cmd
        assert v(cmd, "feature/x").rule == "branch-force-move", cmd
    assert v("git checkout -B main", mode="strict").decision == "deny"        # history rule: strict -> deny


def test_checkout_B_on_ordinary_branch_name_stays_allowed():
    for cmd in ("git checkout -B x", "git checkout -B feature/new", "git switch -C topic", "git checkout -b topic"):
        assert v(cmd).decision == "defer", cmd


@pytest.mark.parametrize("cmd,rule", [
    ("git submodule foreach 'git reset --hard'", "reset-hard"), ("git submodule foreach --recursive 'git clean -fdx'", "clean-force"),
    ("git submodule foreach \"git checkout .\"", "checkout-discard-all"),
    ("git rebase --exec 'git reset --hard' main", "reset-hard"), ("git rebase -x 'git clean -fd' main", "clean-force"),
    ("git rebase --exec='git reset --hard' main", "reset-hard"),
    ("git bisect run git reset --hard", "reset-hard"), ("git bisect run sh -c 'git clean -fd'", "clean-force"),
    ("git checkout-index -f -a", "checkout-discard-all"), ("git checkout-index --force --all", "checkout-discard-all"),
    ("git read-tree --reset -u HEAD", "read-tree-reset"),
    ("git rm -rf .", "rm-tree"), ("git rm -r -f ./.", "rm-tree"), ("git rm -rf :/", "rm-tree"),
    ("git update-ref --delete refs/heads/feature", "update-ref-delete"), ("git update-ref --delete HEAD", "update-ref-delete"),
    ("git -c gc.pruneExpire=now gc", "gc-prune-now"), ("git -c gc.pruneExpire=all gc --aggressive", "gc-prune-now"),
    ("git -c gc.reflogExpire=now gc", "reflog-destroy"), ("git -c gc.reflogExpireUnreachable=now gc", "reflog-destroy"),
])
def test_new_deny_rules(cmd, rule):
    for b in ("main", "feature/x", None):
        got = v(cmd, b)
        assert got.decision == "deny" and got.rule == rule, (cmd, b, got.decision, got.rule)


@pytest.mark.parametrize("cmd,rule", [
    ("git checkout-index -f src/a.c", "checkout-index-force"), ("git read-tree --reset HEAD", "read-tree-reset-index"),
    ("git rm -r .", "rm-tree-ask"), ("git update-ref --stdin", "update-ref-stdin"),
    ("git update-ref --stdin -z", "update-ref-stdin"), ("git push --prune origin 'refs/heads/*:refs/heads/*'", "push-prune"),
    ("git push --prune", "push-prune"), ("git rebase --exec 'make test' main", "rebase"),
])
def test_new_ask_rules(cmd, rule):
    got = v(cmd)
    assert got.decision == "ask" and got.rule == rule, (cmd, got.decision, got.rule)


@pytest.mark.parametrize("cmd", [
    "git submodule foreach 'echo hi'", "git submodule foreach git status", "git submodule update --init", "git bisect run make test",
    "git bisect start", "git bisect good", "git checkout-index -a", "git checkout-index src/a.c", "git read-tree -m HEAD",
    "git rm --cached -r .", "git rm -r dir", "git rm file.txt", "git rm -f one.txt", "git update-ref refs/heads/feature/x abc",
    "git push --dry-run --prune", "git push origin feature/x", "git -c gc.pruneExpire=2.weeks.ago gc", "git gc",
    "git -c core.pager=cat log", "git push --set-upstream origin x",
])
def test_new_rule_false_positives_stay_allowed(cmd):
    assert v(cmd).decision == "defer", cmd


@pytest.mark.parametrize("spec", [".", "./.", "././", ".//", "./", "./*", "a/..", "../", ".././..", ":/./", ":(top)./.", "*"])
def test_whole_tree_pathspec_normalisation(spec):
    assert is_all_pathspec(spec), spec


@pytest.mark.parametrize("spec", ["./src", "src/.", "src/..hidden", ".gitignore", "./.env", ":!x", ":(exclude)x", "a/b", "..foo"])
def test_not_whole_tree_pathspecs(spec):
    assert not is_all_pathspec(spec), spec


@pytest.mark.parametrize("cmd", ["git checkout -- ./.", "git checkout -- ././", "git checkout -- .//", "git restore ./.", "git checkout HEAD -- ./."])
def test_normalised_whole_tree_checkout_is_denied(cmd):
    assert v(cmd).rule == "checkout-discard-all"


# ------------------------------------------------------------------ M5: generic launcher fallback
@pytest.mark.parametrize("cmd", [
    "arch -arm64 git reset --hard", "xcrun git reset --hard", "flock /tmp/x git reset --hard", "watch git reset --hard",
    "parallel git reset --hard ::: x", "script -q /dev/null git reset --hard", "busybox git reset --hard", "chroot / git reset --hard",
    "unshare git reset --hard", "setsid git reset --hard", "timeout -s KILL 5 git reset --hard", "someunknowntool --opt git clean -fd",
    "arch -arm64 /usr/bin/git clean -fdx", "xcrun git-reset --hard",
])
def test_unknown_launchers_hide_nothing(cmd):
    assert v(cmd).decision == "deny", cmd


@pytest.mark.parametrize("cmd", [
    "arch -arm64 git status", "xcrun git log --oneline", "flock /tmp/x git fetch", "man git-reset", "which git", "type git",
    "grep git README.md", "rg 'git reset --hard' docs", "less git-notes.txt", "cat git reset", "head -1 git", "sed -n '/git reset/p' f",
    "awk '/git reset --hard/ {print}' f", "wc -l git", "ssh host git reset --hard", "ls git", "echo git reset --hard", "printf 'git clean -fd\\n'",
    "brew install git", "pip install gitpython", "gh pr create --title git --body x",
])
def test_text_tools_and_benign_launchers_stay_allowed(cmd):
    assert v(cmd).decision == "defer", cmd


@pytest.mark.parametrize("cmd", [
    "python3 -c \"import os; os.system('git reset --hard')\"",
    "python -c \"import subprocess; subprocess.run(['git', 'reset', '--hard'])\"",
    "node -e \"require('child_process').execSync('git clean -fd')\"",
    "ruby -e 'system(\"git reset --hard\")'", "perl -e 'system(\"git clean -fdx\")'",
    "python3.12 -c \"__import__('os').system('git push --force origin main')\"",
])
def test_interpreter_with_destructive_git_string_asks(cmd):
    got = v(cmd, "main")
    assert got.decision == "ask" and got.rule == "interpreter-git", (cmd, got.decision, got.rule)


@pytest.mark.parametrize("cmd", [
    "python3 -c 'print(1)'", "python3 -c \"print('git status')\"", "python3 tool.py", "node -e 'console.log(1)'",
    "python3 -c \"import os; os.system('git log')\"", "python3 -m pytest tests", "ruby -e 'puts 1'",
])
def test_interpreter_false_positives_stay_allowed(cmd):
    assert v(cmd).decision == "defer", cmd


# ------------------------------------------------------------------ H1: pathological padding through the classifier
@pytest.mark.parametrize("pad", ["a." * 9000, "a-" * 9000, "a_" * 9000, "a" * 18000])
def test_padding_does_not_slow_or_hide_the_verdict(pad):
    t0 = time.monotonic()
    got = classify_command("git reset --hard " + pad, Config(), "feature/x")
    assert time.monotonic() - t0 < 2.0
    assert got.decision == "deny" and got.rule == "reset-hard"
    got = classify_command("git clean -fd " + pad + " && git status", Config(), "main")
    assert got.decision == "deny"
    assert all(len(c) < 700 for c in got.commands)        # shown text is cut before redaction
