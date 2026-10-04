"""Extra branch-coverage cases for rarely used classifier paths."""
import pytest

from gitwarp.core.config import Config
from gitwarp.safety.cli import main as cli_main
from gitwarp.safety.classifier import classify_command


def v(cmd, branch="feature/x"):
    return classify_command(cmd, Config(), branch)


@pytest.mark.parametrize("cmd,decision", [
    ("env -S 'git reset --hard'", "deny"), ("env --split-string 'git clean -fd'", "deny"), ("env -u X -C /tmp git reset --hard", "deny"),
    ("su -c 'git reset --hard' root", "deny"), ("su root", "defer"), ("bash", "defer"), ("bash script.sh", "defer"), ("bash -o pipefail -c 'git reset --hard'", "deny"),
    ("bash -c", "defer"), ("sh -c $X", "defer"), ("bash -c \"git $X\"", "ask"), ("sh -s", "defer"), ("echo -e 'git reset --hard' | sh", "deny"),
    ("cat <<EOF | sh\ngit reset --hard\nEOF", "deny"), ("echo $X | sh", "defer"), ("$X | sh", "defer"),
    ("find . -name x", "defer"), ("find . -exec", "defer"), ("find . -exec git status \;", "defer"),
    ("git --exec-path", "defer"), ("git --version", "defer"), ("git -c", "defer"), ("git -C", "defer"), ("git --config-env=alias.x=Y x", "ask"),
    ("git --config-env alias.x=Y x", "ask"), ("git --namespace n reset --hard", "deny"), ("git -P log", "defer"), ("git -x reset --hard", "deny"),
    ("git push -fo opt origin feature/x", "ask"), ("git push -o opt origin main", "defer"), ("git push --push-option x origin main", "defer"),
    ("git push --follow-tags origin main", "defer"), ("git push --force origin :refs/heads/x", "ask"), ("git push origin +:feature", "ask"),
    ("git push -f origin refs/tags/v1", "ask"), ("git push -f origin '$B'", "ask"), ("git push -f origin 'fea*'", "ask"),
    ("git push --all", "defer"), ("git push --branches -f", "deny"), ("git push --dry-run --mirror", "defer"), ("git push -n -f origin main", "defer"),
    ("git update-ref -d $X", "deny"), ("git update-ref -d", "deny"), ("git update-ref HEAD abc", "ask"), ("git update-ref $X abc", "ask"),
    ("git update-ref -m msg refs/heads/feature/x abc", "defer"), ("git branch -D", "ask"), ("git branch -f", "defer"), ("git branch -c a b", "defer"),
    ("git branch --move --force a b", "ask"), ("git branch -u origin/x", "defer"), ("git branch --sort=-date", "defer"),
    ("git reset -- file", "defer"), ("git reset -- HEAD~1", "defer"), ("git reset abc1234", "defer"), ("git reset --patch", "defer"),
    ("git reset --merge", "defer"), ("git reset --soft $X", "defer"), ("git reset $(git merge-base a b)", "defer"),
    ("git clean --interactive -f", "defer"), ("git clean -e '*.log' -n", "defer"), ("git clean -e x -fd", "deny"), ("git clean -n $X", "defer"),
    ("git checkout -b new .", "defer"), ("git checkout --patch .", "defer"), ("git checkout --conflict=merge file", "defer"), ("git checkout -B x", "defer"),
    ("git switch -c n", "defer"), ("git switch --create n", "defer"), ("git restore --patch .", "defer"), ("git restore --worktree file", "defer"),
    ("git restore --pathspec-from-file=list", "defer"), ("git restore -W .", "deny"), ("git restore --staged --worktree :/", "deny"),
    ("git restore ':(exclude)x'", "defer"), ("git restore ':!x'", "defer"), ("git restore ':(top'", "defer"),
    ("git stash push -m 'a' -- x", "defer"), ("git stash --message x", "defer"), ("git stash -m x", "defer"),
    ("git tag -m msg v1", "defer"), ("git tag -F f v1", "defer"), ("git tag --sort=-v:refname", "defer"), ("git tag -u key -a v1 -m x", "defer"),
    ("git config -f x alias.a b", "ask"), ("git config get alias.x", "defer"), ("git config set alias.x y", "ask"), ("git config --type=bool a.b true", "defer"),
    ("git config --global --get-all alias.x", "defer"), ("git config -l", "defer"),
    ("git remote", "defer"), ("git remote -v update", "defer"), ("git worktree", "defer"), ("git submodule", "defer"), ("git rebase -x 'make' main", "ask"),
    ("git rebase --exec 'make' main", "ask"), ("git rebase --abo", "defer"), ("git commit -c abc --amend", "ask"), ("git commit -F msg", "defer"),
    ("git commit --fixup=amend:abc", "defer"), ("git gc --prune", "defer"), ("git gc --no-prune", "defer"), ("git prune --expire=1.day.ago", "deny"),
    ("git reflog expire", "deny"), ("git reflog -n 5", "defer"), ("git filter-branch", "deny"),
    ("rm", "defer"), ("rm -rf", "defer"), ("rm -rf --", "defer"), ("rm -rf -- .git", "deny"), ("rm -rf x -- y", "defer"), ("rm --force x", "defer"),
    ("rm -rf .git/.", "deny"), ("rm -rf .git//", "deny"), ("rm -rf /", "defer"), ("rm -rf .git/hooks", "defer"),
    ("GIT_CONFIG_KEY_0=other git status", "defer"), ("FOO=bar", "defer"), ("FOO=bar BAZ=1", "defer"),
    ("function f { :; }", "defer"), ("function", "defer"), ("command -v git", "defer"), ("command -V git", "defer"),
    ("time", "defer"), ("sudo", "defer"), ("env", "defer"), ("env -i", "defer"), ("xargs", "defer"), ("timeout 5", "defer"),
    ("cd /tmp && git push -f", "ask"), ("pushd x && git push -f", "ask"), ("popd; git push -f", "ask"),
    ("git checkout -b new-b origin/x && git push -f", "ask"), ("git switch -c a -- b && git push -f", "ask"),
    ("git -C x checkout main && git push -f", "ask"), ("git checkout $B && git push -f", "ask"),
    ("git checkout . && git push", "deny"),
])
def test_cases(cmd, decision):
    got = v(cmd, "feature/x")
    assert got.decision == decision, (cmd, got.rule, got.reason)


def test_hostile_protected_branch_checkout_chain():
    assert v("git checkout -B main && git push -f").decision == "deny"
    assert v("git switch -C master; git push --force").decision == "deny"


def test_cli_main_handles_all_argument_shapes(capsys):
    import json
    assert cli_main(["guard", "check", "git reset --hard", "--branch", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "deny"
    assert cli_main(["check", "git status", "--branch", "x", "--protected", "a, b", "--mode", "strict"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "defer" and out["safety_mode"] == "strict"
    assert cli_main(["guard"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)
    assert cli_main(["guard", "check"]) == 2
    assert cli_main(["guard", "check", "x", "--mode", "weird"]) == 2
    assert cli_main(None) == 2
