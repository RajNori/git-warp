"""Unresolved variables in destructive-capable subcommands (ASK) and file-writing options on read-looking commands (ASK)."""
import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command

SEV = {"defer": 0, "ask": 1, "deny": 2}


def v(cmd, branch="feature/x", mode="standard"):
    return classify_command(cmd, Config(safety_mode=mode), branch)


# --------------------------------------------------------------- (1) unresolved variables: unquoted anywhere, quoted before the first operand
UNQUOTED = [
    "git push $FLAGS origin main", "git push origin $FLAGS", "git push origin $BRANCH", "git branch $FLAGS old", "git branch old $FLAGS",
    "git tag $FLAGS v1", "git tag v1 $FLAGS", "git fetch $FLAGS origin", "git fetch origin $REF", "git pull $FLAGS", "git pull origin $B",
    "git clean $FLAGS", "git clean -d $FLAGS", "git reset $FLAGS", "git reset --soft $X", "git checkout $FLAGS main", "git checkout main $X",
    "git switch $FLAGS main", "git switch $B", "git restore $FLAGS f", "git restore f $X", "git stash $FLAGS", "git stash push $FLAGS",
    "git rebase $FLAGS main", "git rebase main $X", "git worktree $SUB w", "git worktree remove $FLAGS w", "git rm $FLAGS f", "git rm f $X",
    "git update-ref $FLAGS refs/heads/x", "git update-ref refs/heads/x $X", "git gc $FLAGS", "git reflog $SUB", "git checkout-index $FLAGS f",
    "git read-tree $FLAGS HEAD", "git submodule update $FLAGS", "git submodule deinit $FLAGS x", "git prune $FLAGS",
    "git push ${FLAGS} origin main", "git push $A$B origin", "git push ${FLAGS:-} origin main",
]


@pytest.mark.parametrize("cmd", UNQUOTED)
def test_unquoted_variable_in_destructive_subcommand_asks(cmd):
    got = v(cmd)
    assert SEV[got.decision] >= 1, (cmd, got.rule)
    assert SEV[v(cmd, "main").decision] >= 1


QUOTED_FIRST = [
    "git push \"$FLAGS\" origin main", "git branch \"$FLAGS\" old", "git tag \"$FLAGS\" v1", "git fetch \"$FLAGS\" origin", "git pull \"$FLAGS\"",
    "git clean \"$FLAGS\"", "git reset \"$FLAGS\"", "git checkout \"$FLAGS\" main", "git switch \"${FLAGS}\" main", "git restore \"$FLAGS\" f",
    "git stash \"$FLAGS\"", "git rebase \"$FLAGS\" main", "git worktree \"$FLAGS\" remove w", "git rm \"$FLAGS\" f", "git update-ref \"$FLAGS\" refs/heads/x",
    "git gc \"$FLAGS\"", "git reflog \"$FLAGS\"", "git checkout-index \"$FLAGS\" f", "git read-tree \"$FLAGS\" HEAD", "git submodule \"$FLAGS\" update",
    "git checkout \"$BRANCH\"",
]


@pytest.mark.parametrize("cmd", QUOTED_FIRST)
def test_quoted_variable_before_first_operand_asks(cmd):
    assert SEV[v(cmd).decision] >= 1, (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd", [
    'git push origin "$BRANCH"', 'git push origin "${BRANCH}"', 'git checkout -b "feature-$X"', 'git stash push -m "wip $X"',
    'git tag v1 -m "$MSG"', 'git push origin "refs/heads/$B"', 'git branch -m old "$NEW"', 'git checkout main -- "$FILE"',
    'git reset --soft HEAD~"$N"', 'git fetch origin "$REF"', 'git pull origin "$B"',
])
def test_quoted_variable_after_an_operand_keeps_baseline_behaviour(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd", [
    "git log $RANGE", "git diff $X", "git status $P", "git show $REV", "git log --oneline $RANGE -- $PATH", "git diff --stat $A $B",
    "git blame $F", "git ls-files $P", "git rev-parse $REF", "git merge-base $A $B", "git grep $PAT", "git add $FILES", "git commit -m $MSG",
    "git clean -n $X", "git clean --dry-run $FLAGS", "git push -n $FLAGS origin", "git cherry-pick $SHA", "git merge $BRANCH",
    "echo $FLAGS", "ls $FLAGS", "$EDITOR notes.txt", "git branch --list $PATTERN", "git stash list $X", "git log -- $PATH",
])
def test_read_only_and_non_destructive_commands_with_variables_stay_deferred(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd,want", [
    ("B=feature; git push origin $B", "defer"), ("B=feature; git checkout $B", "defer"), ('B=feature; git checkout "$B"', "defer"),
    ("R=$(git rev-parse HEAD); git reset $R", "defer"), ("R=$(git rev-parse HEAD); git checkout $R", "defer"),
    ("F=--force; git push $F origin main", "deny"), ("F=-D; git branch $F old", "ask"), ("F=$(echo -d); git tag $F v1", "ask"),
    ("read F; git tag $F v1", "ask"), ("F=a; F=--force; git push $F origin x", "ask"),
])
def test_known_assignments_keep_exact_handling(cmd, want):
    assert v(cmd).decision == want, (cmd, v(cmd).rule)


def test_inherited_variable_cannot_be_proven_harmless():
    # FLAGS comes from the environment of the shell; nothing in the string assigns it
    for cmd in ("git push $FLAGS origin main", "git branch $FLAGS old", "git tag $FLAGS v1"):
        assert v(cmd).decision == "ask" and v(cmd).rule == "dynamic-argument", cmd


def test_dry_run_still_proves_clean_harmless():
    assert v("git clean -n $X").decision == "defer"


def test_reset_soft_with_dynamic_word_is_not_safe():
    """Real Git: `git reset --soft --hard` is a HARD reset (last mode flag wins), so `--soft $X` is not provably harmless."""
    assert v("git reset --soft $X").decision == "ask"


# --------------------------------------------------------------- (2) file-writing options
@pytest.mark.parametrize("cmd", [
    "git diff --output=/tmp/x", "git diff --output /tmp/x", "git diff HEAD~1 --output=/etc/passwd", "git diff --outp=/tmp/x", "git diff --outpu=x",
    "git diff --out=x", "git log --output=/tmp/x", "git log -p --output /tmp/x", "git show --output=/tmp/x HEAD", "git whatchanged --output=x",
    "git reflog show --output=x", "git diff-tree -p --output=x HEAD", "git diff-index --output=x HEAD", "git diff-files --output=x",
    "git range-diff --output=x a b c", "git shortlog --output=x", "git blame --output=x f", "git rev-list --output=x HEAD",
    "git format-patch -o out -1", "git format-patch -oout -1", "git format-patch --output-directory out -1", "git format-patch --output-directory=out -1",
    "git format-patch --output=x -1", "git archive -o a.tgz HEAD", "git archive --output=a.tgz HEAD", "git archive -oa.tgz HEAD",
    "git bundle create b.bundle HEAD", "git bundle create -q b HEAD", "git fast-export --export-marks=m HEAD", "git grep -Ovim x",
    "git grep --open-files-in-pager=vim x", "git -C d diff --output=x", "sudo git diff --output=/x",
    "git diff --output=$F", "git diff --output $F",
])
def test_file_writing_options_ask(cmd):
    got = v(cmd)
    assert got.decision == "ask" and got.rule == "output-file", (cmd, got.rule)
    assert v(cmd, mode="strict").decision == "ask"           # strict leaves it ASK


@pytest.mark.parametrize("cmd", [
    "git diff", "git diff --stat", "git diff --output-indicator-new=+", "git diff --output-indicator-old=- --output-indicator-context=' '",
    "git log --oneline", "git log --grep=output", "git log -- output", "git show HEAD", "git diff -- --output", "git format-patch --stdout -1",
    "git format-patch -1", "git archive HEAD", "git archive --format=tar HEAD", "git bundle verify b", "git bundle list-heads b", "git grep output",
    "git grep -n -e x", "git fsck", "git fsck --lost-found", "git fast-export HEAD", "git fast-export --import-marks=m HEAD", "git commit -m '--output=x'",
    "git diff --name-only", "git diff -O orderfile", "git log -o", "git blame -L 1,2 f",
])
def test_plain_read_forms_stay_deferred(cmd):
    assert v(cmd).decision == "defer", (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd", [
    "git -c core.pager='sh -c x' diff", "git -c core.fsmonitor=/tmp/x status", "git -c core.sshCommand=x fetch",
    "git -c core.hooksPath=/tmp/h commit", "git -c diff.external=x diff", "git -c diff.x.textconv=y diff", "git -c core.editor=x commit",
    "git -c pager.log=x log", "git -c credential.helper=x push",
])
def test_inline_config_that_executes_programs_asks(cmd):
    assert v(cmd).decision == "ask" and v(cmd).rule == "config-exec", (cmd, v(cmd).rule)


@pytest.mark.parametrize("cmd", ["git -c color.ui=always log", "git -c user.name=a commit -m x", "git -c core.quotepath=false status", "git -c gc.auto=0 gc"])
def test_harmless_inline_config_stays_deferred(cmd):
    assert v(cmd).decision == "defer", cmd
