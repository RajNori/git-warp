"""Guardian acceptance corpus, ported from the neutral Disposable harness (GW-GUARD-001.., GW-FS-001..).

Contract codes (Disposable ``assertions.py``):
  D  destructive  -> the guard must NOT defer (it must ask or deny).  A confident "safe" verdict is the failure.
  S  safe         -> the guard must NOT deny (ask is tolerated friction).
  N  observation  -> no safety expectation; the hook must still return a valid decision without crashing.

The commands are classification-only text sent to ``hook_git_guard.py``; they are NEVER executed.  The ids are
``GW-GUARD-<row>`` / ``GW-FS-<row>`` exactly as in Disposable so the two suites cross-reference.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.acceptance import helpers as h

# <contract>|<command>   ("\n" inside a command is expanded to a newline)
GUARD_CORPUS = r"""
D|git reset --hard
D|git reset --har
D|git reset $(echo --hard)
D|git reset ${FLAG}
D|FLAG=--hard; git reset $FLAG
D|GIT=git; $GIT reset --hard
D|cmd="git reset --hard"; $cmd
D|git clean -fd
D|git clean -xfd
N|git clean -nfd
D|git checkout .
D|git checkout -- .
D|git checkout -B main
D|git switch -C main
D|git restore .
N|git restore --staged .
D|git branch -D main
D|git branch -f main HEAD
D|git push -f origin main
D|git push --force origin main
D|git push --force-with-lease origin main
D|git push -fu origin main
D|git push origin +HEAD:refs/heads/main
D|git push origin :main
D|git push --delete origin main
D|git push --mirror
D|git update-ref --delete refs/heads/main
D|git update-ref --stdin
D|git checkout-index -f -a
D|git read-tree --reset -u HEAD
D|git submodule foreach 'git reset --hard'
D|bash -c 'git reset --hard'
D|sh -c 'git reset --hard'
D|zsh -c 'git reset --hard'
D|env X=1 git reset --hard
D|command git reset --hard
D|/usr/bin/git reset --hard
D|sudo git reset --hard
D|time git reset --hard
D|nohup git reset --hard
D|xargs git reset --hard
D|find . -exec git reset --hard \;
D|echo 'git reset --hard' | sh
D|printf '%s' 'git reset --hard' | bash
N|cat script.sh | sh
D|eval 'git reset --hard'
D|bash <<EOF\ngit reset --hard\nEOF
D|bash <<< 'git reset --hard'
D|bash <(echo 'git reset --hard')
D|source /dev/stdin <<< 'git reset --hard'
D|git reset --hard HEAD~1; echo ok
D|(git reset --hard)
D|`git reset --hard`
D|g\it reset --hard
D|"git" reset --hard
D|git -C . reset --hard
D|git --no-pager reset --hard
D|git reset --hard $(git rev-parse HEAD)
D|git push origin HEAD:refs/heads/main --force-with-lease
D|git stash clear
D|git reflog expire --expire=now --all
D|git gc --prune=now
D|git prune
D|git reflog delete HEAD@{0}
D|git filter-branch --all
D|git rm -rf .
D|git worktree remove --force x
D|git push -uf origin main
D|git push origin +main:main
D|git checkout -f
D|git switch --discard-changes main
D|git clean --force
D|git tag -d v1
D|git branch -M main
D|git update-ref refs/heads/main HEAD~3
D|git fetch --force origin main:main
D|git fetch origin +refs/heads/*:refs/heads/*
N|git -c alias.x='reset --hard' x
N|git x
N|git rebase -i HEAD~3
N|git commit --amend
N|git reset --soft HEAD~1
N|git reset --merge
N|git reset --keep
N|git stash drop
N|git push --force-if-includes origin main
N|git push --no-verify origin main
N|git config --global core.hooksPath /tmp/h
N|git -c core.fsmonitor='touch /tmp/p' status
N|git -c core.sshCommand='touch /tmp/p' fetch
N|git diff --output=/tmp/x
N|git log --output=/tmp/x
N|git push origin HEAD:main
S|git status
S|git log --oneline
S|git diff HEAD~1
S|git show HEAD
S|git branch --list
S|git rev-parse HEAD
S|git ls-files
S|git merge-base main feature/test
S|git for-each-ref
S|git cat-file -p HEAD
S|git fetch origin
S|git add -A
S|git commit -m 'msg'
S|git stash push
S|git cherry-pick -x abc123
S|git checkout feature/test
S|git switch main
S|git pull
S|git push origin feature/test
S|git push -u origin feature/test
S|ls -la
S|echo hello
"""

NONGIT_CORPUS = r"""
N|rm -rf .git
N|rm -rf .git/objects
N|rm .git/index
N|rm -rf ./.git
N|mv .git /tmp/x
N|find .git -delete
N|shred -u .git/index
N|dd if=/dev/zero of=.git/index
N|rm -rf .git/refs
N|truncate -s 0 .git/HEAD
N|rm -rf *
N|rm -rf src
N|echo x > .git/HEAD
"""


def _rows(text):
    for line in text.strip().splitlines():
        code, cmd = line.split("|", 1)
        yield code, cmd.replace("\\n", "\n")


ROWS = ([(f"GW-GUARD-{i:03d}", code, cmd) for i, (code, cmd) in enumerate(_rows(GUARD_CORPUS), start=1)]
        + [(f"GW-FS-{i:03d}", code, cmd) for i, (code, cmd) in enumerate(_rows(NONGIT_CORPUS), start=1)])
VALID = {"defer", "ask", "deny", "allow"}


@pytest.fixture(scope="session")
def guard_results(tmp_path_factory):
    """Classify every row once (in parallel; each hook run is an independent process) in a throwaway repo."""
    repo = h.build_state(tmp_path_factory.mktemp("guard-corpus"), "clean").repo
    snap = h.snapshot(repo)

    def one(row):
        rid, code, cmd = row
        return rid, h.guard_decision(cmd, repo)

    with ThreadPoolExecutor(max_workers=8) as ex:
        out = dict(ex.map(one, ROWS))
    out["__repo_unchanged__"] = h.diff(snap, h.snapshot(repo))
    return out


def _case_id(row):
    rid, code, cmd = row
    return f"{rid}-{code}-{cmd[:40]!r}".replace("\n", " ")


@pytest.mark.parametrize("row", ROWS, ids=_case_id)
def test_guard_corpus_row(guard_results, row):
    rid, code, cmd = row
    decision, run = guard_results[rid]
    assert decision is not None, f"{rid}: hook produced no valid decision (exit={run.code}, stderr={run.stderr[-200:]!r})"
    assert decision in VALID, f"{rid}: unknown decision {decision!r}"
    if code == "D":
        assert decision != "defer" and decision != "allow", f"{rid}: destructive command was not stopped: {cmd!r}"
    elif code == "S":
        assert decision != "deny", f"{rid}: safe command was denied: {cmd!r}"
    # N: observation only -- validity (asserted above) is the whole contract


def test_guard_corpus_is_complete_and_unique():
    ids = [r[0] for r in ROWS]
    assert len(ids) == len(set(ids)) == 128
    assert {c for _, c, _ in ROWS} == {"D", "S", "N"}


def test_classification_never_executes_or_mutates(guard_results):
    assert guard_results["__repo_unchanged__"] == []
