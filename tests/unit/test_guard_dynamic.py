"""Dynamic expressions: uncertainty is ASK (or DENY), never a confident DEFER; read-only / non-Git stays DEFER."""
import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command

SEV = {"defer": 0, "ask": 1, "deny": 2}


def v(cmd, branch="feature/x", mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), branch)


@pytest.mark.parametrize("cmd", [
    # command / process substitution and backticks as options
    "git reset $(echo --hard)", "git reset `echo --hard`", 'git reset "$(printf %s --hard)"', "git clean $(echo -fdx)",
    "git clean `echo -fd`", "git checkout $(echo --force) main", "git checkout -- . $(echo -f)", "git push origin $(echo +main)",
    "git push $(echo --force) origin main", "git push origin +$(echo main)", "git switch $(echo --discard-changes) main",
    "git restore $(echo --worktree) .", "git branch $(echo -D) topic", "git stash $(echo clear)", "git tag $(echo -d) v1",
    "git rebase $(echo -i) HEAD~3", "git worktree remove $(echo --force) w", "git update-ref $(echo -d) refs/heads/x",
    # variable as option / executable / subcommand
    "FLAG=--hard; git reset $FLAG", "F=-fdx; git clean $F", "FORCE=--force; git push $FORCE origin main",
    'cmd="git reset --hard"; $cmd', "GIT=git; $GIT reset --hard", "g=git; $g clean -fd", "x=reset; git $x --hard",
    "x=reset; git $x", "git $(echo reset) --hard", "$(echo git) reset --hard", "`echo git` reset --hard",
    "S=--hard; git reset $S", "A=--hard; B=$A; git reset $B", "OPTS='-f -d'; git clean $OPTS",
    # eval / shells / generated scripts / stdin
    "eval 'git reset --hard'", "eval \"git $ARGS\"", "bash <(echo 'git reset --hard')", "sh <(printf 'git clean -fd')",
    "source /dev/stdin <<< 'git reset --hard'", ". /dev/stdin <<< 'git reset --hard'", "source <(echo 'git reset --hard')",
    "echo 'git reset --hard' > gen.sh && bash gen.sh", "printf 'git clean -fdx' > gen.sh; sh ./gen.sh", "echo 'git reset --hard' > g.sh; ./g.sh",
    "echo 'git reset --hard' >> g.sh; source g.sh", "cat > g.sh <<'EOF'\ngit reset --hard\nEOF\nbash g.sh",
    "echo 'git reset --hard' | tee g.sh; bash g.sh", "echo 'git reset --hard' > g.py; python3 g.py", "echo x > g.sh; bash < g.sh",
    "bash /dev/stdin <<< 'git reset --hard'",
])
def test_uncertain_constructs_never_defer(cmd):
    assert SEV[v(cmd).decision] >= SEV["ask"], (cmd, v(cmd).rule)
    assert SEV[v(cmd, "main").decision] >= SEV["ask"], cmd


@pytest.mark.parametrize("cmd", [
    "git log $(git merge-base HEAD main)..HEAD", "git diff $(git rev-parse HEAD~1)", "git show $(git rev-parse HEAD)",
    'git commit -m "$(date)"', "echo $(git status --short)", "source venv/bin/activate", ". ./env.sh", "git status",
    "git log $(echo --oneline)", "git diff $(echo --stat)", "git show `echo HEAD`", "git rev-parse $(echo HEAD)", "git ls-files $(echo x)",
    'git commit -m "$(cat <<EOF\nfix things\nEOF\n)"', "git tag -a v1 -m \"$(cat notes.txt)\"", 'git stash push -m "$(date)"',
    "git push origin $(git rev-parse --abbrev-ref HEAD)", "git checkout $(git rev-parse HEAD) -- f.txt",
    "git reset $(git merge-base a b)", "git branch --list $(echo 'f*')", "git branch --show-current", "git stash list $(echo -1)",
    'git checkout -b "feature-$X"', "git diff $FILES", "git log --since=\"$D\"",
    "B=feature; git push origin $B", "B=feature; git checkout $B", "N=3; git reset --soft HEAD~$N",
    "echo $(echo --hard)", "ls $(pwd)", "$EDITOR notes.txt", "EDITOR=vim; $EDITOR notes.txt", "x=$(date); echo $x",
    "bash script.sh", "bash ./build.sh && echo ok", "echo hi > out.txt; cat out.txt", "echo 'git reset --hard' > notes.txt; cat notes.txt",
    "make test > log.txt; grep FAIL log.txt", "python3 build.py", "source ~/.bashrc",
])
def test_read_only_and_non_git_dynamic_words_stay_deferred(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd,decision", [
    ("GIT=git; $GIT reset --hard", "deny"), ("FLAG=--hard; git reset $FLAG", "deny"), ('cmd="git reset --hard"; $cmd', "deny"),
    ("x=reset; git $x --hard", "deny"), ("source /dev/stdin <<< 'git reset --hard'", "deny"),
    ("F=-fd; git clean $F", "deny"), ("git reset $(echo --hard)", "ask"), ("git clean $(echo -fdx)", "ask"),
])
def test_literal_proof_keeps_the_stronger_result(cmd, decision):
    assert v(cmd).decision == decision, (cmd, v(cmd).rule)


def test_variable_reassignment_is_uncertain_not_resolved():
    # last-write-wins would resolve to the harmless value; a conditional assignment makes that unsafe
    assert SEV[v("F=-n; [ -z \"$x\" ] && F=--hard; git reset $F").decision] >= 1
    assert SEV[v("F=main; F=--force; git push $F origin x").decision] >= 1
    assert SEV[v("read F; git reset $F").decision] >= 1


def test_for_loop_variable_is_unknown_not_resolved():
    assert SEV[v("for f in --hard; do git reset $f; done").decision] >= 1


def test_strict_mode_does_not_turn_uncertainty_into_deny():
    assert v("git reset $(echo --hard)", mode="strict").decision == "ask"


def test_rules_are_stable_for_callers():
    assert v("git reset $(echo --hard)").rule == "dynamic-argument"
    assert v("echo x > g.sh; bash g.sh").rule == "generated-script"
    assert v("bash <(echo git)").rule == "stdin-script"
    assert v("git $X").rule == "unresolved-subcommand"
    assert v("git status").decision == "defer" and v("git status").rule == "defer"
