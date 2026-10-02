"""Extra branch-coverage cases for rarely used classifier paths."""
import pytest

from gitwarp.core.config import Config
from gitwarp.safety.cli import main as cli_main
from gitwarp.safety.classifier import classify_command


def v(cmd, branch="feature/x"):
    return classify_command(cmd, Config(), branch)


@pytest.mark.parametrize("cmd,decision", [
    ("env -S 'git reset --hard'", "deny"), ("env --split-string 'git clean -fd'", "deny"), ("env -u X -C /tmp git reset --hard", "deny"),
    ("su -c 'git reset --hard' root", "deny"), ("su root", "allow"), ("bash", "allow"), ("bash script.sh", "allow"), ("bash -o pipefail -c 'git reset --hard'", "deny"),
    ("bash -c", "allow"), ("sh -c $X", "allow"), ("bash -c \"git $X\"", "ask"), ("sh -s", "allow"), ("echo -e 'git reset --hard' | sh", "deny"),
    ("cat <<EOF | sh\ngit reset --hard\nEOF", "deny"), ("echo $X | sh", "allow"), ("$X | sh", "allow"),
    ("find . -name x", "allow"), ("find . -exec", "allow"), ("find . -exec git status \;", "allow"),
    ("git --exec-path", "allow"), ("git --version", "allow"), ("git -c", "allow"), ("git -C", "allow"), ("git --config-env=alias.x=Y x", "ask"),
    ("git --config-env alias.x=Y x", "ask"), ("git --namespace n reset --hard", "deny"), ("git -P log", "allow"), ("git -x reset --hard", "deny"),
    ("git push -fo opt origin feature/x", "ask"), ("git push -o opt origin main", "allow"), ("git push --push-option x origin main", "allow"),
    ("git push --follow-tags origin main", "allow"), ("git push --force origin :refs/heads/x", "ask"), ("git push origin +:feature", "ask"),
    ("git push -f origin refs/tags/v1", "ask"), ("git push -f origin '$B'", "ask"), ("git push -f origin 'fea*'", "ask"),
    ("git push --all", "allow"), ("git push --branches -f", "deny"), ("git push --dry-run --mirror", "allow"), ("git push -n -f origin main", "allow"),
    ("git update-ref -d $X", "deny"), ("git update-ref -d", "deny"), ("git update-ref HEAD abc", "ask"), ("git update-ref $X abc", "ask"),
    ("git update-ref -m msg refs/heads/feature/x abc", "allow"), ("git branch -D", "ask"), ("git branch -f", "allow"), ("git branch -c a b", "allow"),
    ("git branch --move --force a b", "ask"), ("git branch -u origin/x", "allow"), ("git branch --sort=-date", "allow"),
    ("git reset -- file", "allow"), ("git reset -- HEAD~1", "allow"), ("git reset abc1234", "allow"), ("git reset --patch", "allow"),
    ("git reset --merge", "allow"), ("git reset --soft $X", "allow"), ("git reset $(git merge-base a b)", "allow"),
    ("git clean --interactive -f", "allow"), ("git clean -e '*.log' -n", "allow"), ("git clean -e x -fd", "deny"), ("git clean -n $X", "allow"),
    ("git checkout -b new .", "allow"), ("git checkout --patch .", "allow"), ("git checkout --conflict=merge file", "allow"), ("git checkout -B x", "allow"),
    ("git switch -c n", "allow"), ("git switch --create n", "allow"), ("git restore --patch .", "allow"), ("git restore --worktree file", "allow"),
    ("git restore --pathspec-from-file=list", "allow"), ("git restore -W .", "deny"), ("git restore --staged --worktree :/", "deny"),
    ("git restore ':(exclude)x'", "allow"), ("git restore ':!x'", "allow"), ("git restore ':(top'", "allow"),
    ("git stash push -m 'a' -- x", "allow"), ("git stash --message x", "allow"), ("git stash -m x", "allow"),
    ("git tag -m msg v1", "allow"), ("git tag -F f v1", "allow"), ("git tag --sort=-v:refname", "allow"), ("git tag -u key -a v1 -m x", "allow"),
    ("git config -f x alias.a b", "ask"), ("git config get alias.x", "allow"), ("git config set alias.x y", "ask"), ("git config --type=bool a.b true", "allow"),
    ("git config --global --get-all alias.x", "allow"), ("git config -l", "allow"),
    ("git remote", "allow"), ("git remote -v update", "allow"), ("git worktree", "allow"), ("git submodule", "allow"), ("git rebase -x 'make' main", "ask"),
    ("git rebase --exec 'make' main", "ask"), ("git rebase --abo", "allow"), ("git commit -c abc --amend", "ask"), ("git commit -F msg", "allow"),
    ("git commit --fixup=amend:abc", "allow"), ("git gc --prune", "allow"), ("git gc --no-prune", "allow"), ("git prune --expire=1.day.ago", "deny"),
    ("git reflog expire", "deny"), ("git reflog -n 5", "allow"), ("git filter-branch", "deny"),
    ("rm", "allow"), ("rm -rf", "allow"), ("rm -rf --", "allow"), ("rm -rf -- .git", "deny"), ("rm -rf x -- y", "allow"), ("rm --force x", "allow"),
    ("rm -rf .git/.", "deny"), ("rm -rf .git//", "deny"), ("rm -rf /", "allow"), ("rm -rf .git/hooks", "allow"),
    ("GIT_CONFIG_KEY_0=other git status", "allow"), ("FOO=bar", "allow"), ("FOO=bar BAZ=1", "allow"),
    ("function f { :; }", "allow"), ("function", "allow"), ("command -v git", "allow"), ("command -V git", "allow"),
    ("time", "allow"), ("sudo", "allow"), ("env", "allow"), ("env -i", "allow"), ("xargs", "allow"), ("timeout 5", "allow"),
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
    assert out["decision"] == "allow" and out["safety_mode"] == "strict"
    assert cli_main(["guard"]) == 2
    assert "error" in json.loads(capsys.readouterr().out)
    assert cli_main(["guard", "check"]) == 2
    assert cli_main(["guard", "check", "x", "--mode", "weird"]) == 2
    assert cli_main(None) == 2
