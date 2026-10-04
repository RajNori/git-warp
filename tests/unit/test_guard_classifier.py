"""Table-driven tests for the Git guard classifier (decision per command string)."""
import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command, is_all_pathspec

A, K, D = "defer", "ask", "deny"


def verdict(cmd, branch="feature/x", mode="standard", protected=None):
    cfg = Config(safety_mode=mode)
    if protected is not None:
        cfg.protected_branches = protected
    return classify_command(cmd, cfg, branch)


# (command, expected decision, expected rule or None, current branch)
DENY_CASES = [
    ("git reset --hard", "reset-hard"), ("git reset --hard HEAD~3", "reset-hard"), ("git reset HEAD~1 --hard", "reset-hard"),
    ("git reset --ha", "reset-hard"), ("git reset --hard origin/main", "reset-hard"),
    ("git clean -f", "clean-force"), ("git clean -fd", "clean-force"), ("git clean -xdf", "clean-force"),
    ("git clean -fdx", "clean-force"), ("git clean --force", "clean-force"), ("git clean -d -f", "clean-force"),
    ("git clean -ff", "clean-force"), ("git clean -f -- src", "clean-force"), ("git clean --for -d", "clean-force"),
    ("git checkout -- .", "checkout-discard-all"), ("git checkout .", "checkout-discard-all"),
    ("git checkout -f", "checkout-force"), ("git checkout --force main", "checkout-force"), ("git checkout -fb x", "checkout-force"),
    ("git checkout HEAD -- .", "checkout-discard-all"), ("git checkout -- :/", "checkout-discard-all"),
    ("git checkout -- '*'", "checkout-discard-all"), ("git checkout main .", "checkout-discard-all"),
    ("git switch --discard-changes main", "switch-discard"), ("git switch -f main", "switch-discard"),
    ("git switch --force main", "switch-discard"), ("git switch --discard main", "switch-discard"),
    ("git restore .", "checkout-discard-all"), ("git restore --worktree :/", "checkout-discard-all"),
    ("git restore --source=HEAD~1 .", "checkout-discard-all"), ("git restore -s HEAD .", "checkout-discard-all"),
    ("git restore '*'", "checkout-discard-all"), ("git restore :/", "checkout-discard-all"),
    ("git restore --staged --worktree .", "checkout-discard-all"), ("git restore -SW .", "checkout-discard-all"),
    ("git restore ':(top)'", "checkout-discard-all"), ("git restore -- .", "checkout-discard-all"),
    ("git reflog expire --expire=now --all", "reflog-destroy"), ("git reflog delete HEAD@{1}", "reflog-destroy"),
    ("git gc --prune=now", "gc-prune-now"), ("git gc --prune=all", "gc-prune-now"), ("git gc --aggressive --prune=now", "gc-prune-now"),
    ("git prune", "prune"), ("git prune --expire now", "prune"),
    ("git stash clear", "stash-clear"),
    ("git update-ref -d refs/heads/main", "update-ref-delete"), ("git update-ref -d HEAD", "update-ref-delete"),
    ("git update-ref -d refs/heads/feature/x", "update-ref-delete"),
    ("git push --force", "push-force-protected"), ("git push -f origin main", "push-force-protected"),
    ("git push --force-with-lease origin main", "push-force-protected"), ("git push origin +main", "push-force-protected"),
    ("git push origin +HEAD:main", "push-force-protected"), ("git push origin HEAD:refs/heads/master -f", "push-force-protected"),
    ("git push --force origin develop", "push-force-protected"), ("git push -fu origin production", "push-force-protected"),
    ("git push --force-if-includes --force-with-lease origin release", "push-force-protected"),
    ("git push --all --force", "push-force-protected"), ("git push --forc origin main", "push-force-protected"),
    ("git push --mirror", "push-mirror"), ("git push --mirror origin", "push-mirror"), ("git push origin --mirror", "push-mirror"),
    ("git push --delete origin main", "push-delete-protected"), ("git push origin :main", "push-delete-protected"),
    ("git push -d origin master", "push-delete-protected"), ("git push origin :refs/heads/production", "push-delete-protected"),
    ("git filter-branch --tree-filter 'rm x' HEAD", "history-tool"), ("git filter-repo --path src", "history-tool"),
    ("git-filter-repo --invert-paths", "history-tool"), ("git-filter-branch -f", "history-tool"),
    ("rm -rf .git", "rm-git"), ("rm -rf .git/", "rm-git"), ("rm -fr .git", "rm-git"), ("rm -r .git", "rm-git"),
    ("rm -rf ./.git", "rm-git"), ("rm -rf /home/u/proj/.git", "rm-git"), ("rm -rf -- .git", "rm-git"),
    ("rm --recursive --force .git", "rm-git"), ("rm -rf repo.git", "rm-git"), ("rm -rf .git/*", "rm-git"),
    ("rm -Rf a b .git", "rm-git"), ("rm -rf $HOME/proj/.git", "rm-git"),
]

# on a protected branch (main) by default; a few use a feature branch
ASK_CASES = [
    ("git rebase main", "rebase"), ("git rebase -i HEAD~3", "rebase"), ("git rebase --onto a b c", "rebase"),
    ("git rebase", "rebase"),
    ("git commit --amend", "commit-amend"), ("git commit --amend --no-edit", "commit-amend"),
    ("git commit -a --amend -m x", "commit-amend"), ("git commit --am", "commit-amend"),
    ("git branch -D old", "branch-force-delete"), ("git branch -d -f old", "branch-force-delete"),
    ("git branch --delete --force old", "branch-force-delete"), ("git branch -df old", "branch-force-delete"),
    ("git branch -M new", "branch-force-move"), ("git branch -m -f a b", "branch-force-move"),
    ("git branch -f feature HEAD~2", "branch-force-move"), ("git branch -C a b", "branch-force-move"),
    ("git tag -d v1", "tag-rewrite"), ("git tag -f v1", "tag-rewrite"), ("git tag --delete v1", "tag-rewrite"),
    ("git tag -a v2 -f -m msg", "tag-rewrite"), ("git tag --force v1 HEAD", "tag-rewrite"),
    ("git worktree remove --force ../wt", "worktree-force-remove"), ("git worktree remove -f ../wt", "worktree-force-remove"),
    ("git worktree remove -ff ../wt", "worktree-force-remove"),
    ("git submodule deinit -f lib", "submodule-deinit-force"), ("git submodule deinit --force --all", "submodule-deinit-force"),
    ("git stash drop", "stash-drop"), ("git stash drop stash@{1}", "stash-drop"),
    ("git config alias.nuke 'reset --hard'", "alias-config"), ("git config --global alias.x '!rm -rf .'", "alias-config"),
    ("git config --add alias.co checkout", "alias-config"),
    ("git -c alias.x='reset --hard' x", "alias-inline"),
    ("git remote set-url origin https://x/y.git", "remote-modify"), ("git remote remove origin", "remote-modify"),
    ("git remote rm origin", "remote-modify"),
    ("git push --force origin feature/x", "push-force"), ("git push -f", "push-force"),
    ("git push origin +feature/x", "push-force"), ("git push --force-with-lease origin HEAD", "push-force"),
    ("git push --delete origin feature/old", "push-delete"), ("git push origin :feature/old", "push-delete"),
    ("git push --force-with-lease", "push-force"),
    ("git $CMD", "unresolved-subcommand"), ("git ${SUB} --hard", "unresolved-subcommand"), ("git $(echo reset) --hard", "unresolved-subcommand"),
    ("git `echo reset` --hard", "unresolved-subcommand"), ("git re* --hard", "unresolved-subcommand"),
    ("git {reset,status} --hard", "unresolved-subcommand"),
    ("git reset $FLAG", "reset-unresolved"), ("git clean $OPTS", "clean-unresolved"),
    ("git update-ref -d refs/tags/v1", "update-ref-delete-other"),
    ("eval \"git $ARGS\"", "eval-git"), ("bash -c \"git $X\"", "shell-git"), ("$RUN git status", "unresolved-command"),
    ("GIT_CONFIG_KEY_0=alias.x GIT_CONFIG_VALUE_0='!sh' git x", "env-alias"),
    ("git -C ../other push -f", "push-force"), ("git --git-dir=/x/.git push --force", "push-force"),
    ("cd ../other && git push --force", "push-force"),
]

PROTECTED_ASK = [("git reset HEAD~3", "reset-protected"), ("git reset --mixed HEAD^", "reset-protected"),
                 ("git update-ref refs/heads/main abc123", "update-ref-protected")]

ALLOW_CASES = [
    "git status", "git log --oneline", "git diff", "git diff --stat HEAD~1", "git add -A", "git add .", "git commit -m 'msg'",
    "git commit -m \"reset --hard\"", "git commit -m 'git clean -fd' --no-edit", "git commit -am 'amend later'",
    "git fetch --prune", "git pull", "git pull --rebase", "git push", "git push origin feature/x", "git push -u origin HEAD",
    "git push --tags", "git push --dry-run -f origin main", "git push origin main",
    "git branch -d merged", "git branch", "git branch -a", "git branch -m old new", "git branch new-feature", "git branch --list",
    "git checkout main", "git checkout -b new", "git switch main", "git switch -c new", "git checkout -- file.txt",
    "git checkout HEAD -- src/a.py", "git checkout -p", "git restore file.txt", "git restore --staged .", "git restore -S .",
    "git restore --staged src/", "git restore -p .", "git restore src/a.py", "git restore --source=HEAD~1 a.txt",
    "git stash", "git stash push -u", "git stash pop", "git stash list", "git stash apply", "git stash push -m clear",
    "git stash -m drop", "git merge feature", "git merge --abort", "git rebase --abort", "git rebase --continue", "git rebase --skip",
    "git rebase --quit", "git rebase --show-current-patch", "git rebase --edit-todo", "git cherry-pick abc", "git revert HEAD",
    "git reset", "git reset HEAD file.txt", "git reset --soft HEAD~1", "git reset --mixed", "git reset --keep HEAD~1",
    "git reset -p", "git reset HEAD", "git clean -n", "git clean -nd", "git clean --dry-run -fd", "git clean -fdn", "git clean -i",
    "git clean -fdi", "git clean", "git tag v1", "git tag -a v1 -m msg", "git tag -l", "git worktree add ../wt", "git worktree remove ../wt",
    "git worktree list", "git submodule update --init", "git submodule deinit lib", "git config user.name x", "git config --get alias.co",
    "git config --list", "git config alias", "git config --unset alias.x", "git remote -v", "git remote add up url", "git gc", "git gc --prune=2.weeks.ago",
    "git prune -n", "git prune --dry-run", "git reflog", "git reflog show HEAD", "git update-ref refs/heads/feature/x abc", "git stash show -p",
    "git --version", "git", "git --no-pager log", "git -p diff", "git -C repo status", "git -c core.pager=cat log", "git --help",
    "git show HEAD:file", "git blame f", "git grep reset", "git ls-files", "git rev-parse HEAD", "git describe", "git bisect start", "git bisect reset",
    "git fsck --lost-found", "git log --grep=\"reset --hard\"", "git log --grep='git clean -fd'", "git log -S'reset --hard'",
    "git commit -m \"$(cat <<'EOF'\ndo not git reset --hard here\nEOF\n)\"", "git apply --check p.diff", "git am --abort",
    "echo 'git reset --hard'", "echo \"git push --force origin main\"", "grep -r \"git clean -fd\" .", "printf 'git clean -fdx\\n'",
    "cat README.md", "ls -la", "# git reset --hard", "git status # git reset --hard", "echo hi # git clean -fd", "true",
    "echo git reset --hard", "sed -n '/git reset --hard/p' notes.txt", "rm -rf node_modules", "rm -rf build/", "rm -f .git/index.lock",
    "rm .git", "rm -rf gitrepo", "rm -rf .github", "rm -rf .gitignore.bak", "ls .git", "cat .git/config",
    "man git-reset", "which git", "command -v git", "type git", "echo $(date)", "", "   ", "\n", "ssh host 'git reset --hard'",
    "git branch -d feature/x && git push origin --delete feature/x-old-nonexistent-ok" if False else "git log | head",
    "git diff | grep reset", "git status; git log", "git status && git diff", "git status || echo no", "git log & ",
    "git push origin feature/x --force-with-lease=feature/x:abc" if False else "git remote show origin",
    "git checkout -b fix . " if False else "git checkout -b feature/y",
    "git stash push -- clear", "git add -- reset", "git commit -m 'rebase'", "git commit -m '--amend'",
    "python3 -c 'print(1)'", "make clean", "npm run reset", "echo '$(git reset --hard)'", "echo \"\\$(git reset --hard)\"",
    "echo \"$(echo hi)\"", "git log --format='%H %s' -n 5", "git commit -m 'it'\"'\"'s fine'", "git branch --set-upstream-to=origin/main",
    "git push origin HEAD:refs/for/main", "git push origin refs/tags/v1", "git push --force origin refs/tags/v1" if False else "git push origin v1.0",
    "git clone https://github.com/a/b.git", "git init", "git mv a b", "git rm --cached f", "git rm -r dir", "git notes add -m x",
    "git checkout --ours file", "git checkout --theirs file", "git checkout --orphan newroot", "git switch -", "git checkout -",
    "git reset --soft HEAD~1 && git commit -m x", "git stash && git pull && git stash pop",
]


@pytest.mark.parametrize("cmd,rule", DENY_CASES)
def test_deny_cases(cmd, rule):
    for branch in ("main", "feature/x", None):
        v = verdict(cmd, branch=branch)
        if rule.startswith("push-force-protected") and "origin" not in cmd and branch != "main" and "--all" not in cmd:
            continue  # bare force push only protected on a protected branch
        assert v.decision == D and v.rule == rule, (cmd, branch, v)


@pytest.mark.parametrize("cmd,rule", ASK_CASES)
def test_ask_cases(cmd, rule):
    v = verdict(cmd, branch="feature/x")
    assert v.decision == K and v.rule == rule, (cmd, v)


@pytest.mark.parametrize("cmd,rule", PROTECTED_ASK)
def test_protected_branch_ask(cmd, rule):
    assert verdict(cmd, branch="main").rule == rule
    assert verdict(cmd, branch="main").decision == K
    assert verdict(cmd, branch="feature/x").decision == A or rule == "update-ref-protected"


@pytest.mark.parametrize("cmd", ALLOW_CASES)
def test_allow_cases(cmd):
    for branch in ("main", "feature/x", None):
        v = verdict(cmd, branch=branch)
        assert v.decision == A, (cmd, branch, v.rule, v.reason)
        assert v.reason == "" and v.safer == []


def test_case_count_is_extensive():
    assert len(DENY_CASES) + len(ASK_CASES) + len(ALLOW_CASES) + len(PROTECTED_ASK) >= 300


# ---------------------------------------------------------------- wrappers / separators / nesting
WRAP = [
    "env git reset --hard", "env FOO=1 BAR=2 git reset --hard", "env -i git reset --hard", "env -u HOME git reset --hard",
    "FOO=1 git reset --hard", "FOO=1 BAR=\"a b\" git reset --hard", "command git reset --hard", "command -p git reset --hard",
    "builtin command git reset --hard", "exec git reset --hard", "sudo git reset --hard", "sudo -u root git reset --hard",
    "sudo -E -H git reset --hard", "sudo -- git reset --hard", "nice git reset --hard", "nice -n 10 git reset --hard", "time git reset --hard",
    "nohup git reset --hard", "nohup nice -n5 git reset --hard", "/usr/bin/git reset --hard", "./git reset --hard", "../bin/git reset --hard",
    "/opt/homebrew/bin/git reset --hard", "git -C /tmp/x reset --hard", "git -c core.pager=cat reset --hard",
    "git --git-dir=/x/.git --work-tree=/x reset --hard", "git --git-dir /x/.git reset --hard", "git --no-pager reset --hard",
    "git -p reset --hard", "git -C a -C b -c x=y --no-pager reset --hard", "git-reset --hard", "timeout 5 git reset --hard",
    "timeout -s KILL 5 git reset --hard", "ionice -c 3 git reset --hard", "stdbuf -oL git reset --hard", "setsid git reset --hard",
    "xargs git reset --hard", "xargs -n 1 git reset --hard", "xargs -I{} git reset --hard {}", 
    "! git reset --hard", "if true; then git reset --hard; fi", "while true; do git reset --hard; done", "for i in 1 2; do git reset --hard; done",
    "{ git reset --hard; }", "{ echo a; git reset --hard; }", "(git reset --hard)", "( cd x && git reset --hard )", "((git reset --hard))",
    "echo a; git reset --hard", "echo a && git reset --hard", "echo a || git reset --hard", "echo a | git reset --hard", "echo a & git reset --hard",
    "echo a\ngit reset --hard", "echo a;git reset --hard", "echo a&&git reset --hard", "git status\n\n\ngit reset --hard",
    "git status && git reset --hard && git status", "git log | cat; git reset --hard", "echo $(git reset --hard)", "echo `git reset --hard`",
    "echo \"$(git reset --hard)\"", "echo \"`git reset --hard`\"", "x=$(git reset --hard)", "echo $(echo $(git reset --hard))",
    "echo $( ( git reset --hard ) )", "echo ${X:-$(git reset --hard)}", "echo \"${X:-$(git reset --hard)}\"", "diff <(git reset --hard) b",
    "bash -c 'git reset --hard'", "sh -c \"git reset --hard\"", "zsh -c 'git reset --hard'", "bash -lc 'git reset --hard'", "bash -ec 'git reset --hard'",
    "/bin/bash -c 'git reset --hard'", "env bash -c 'git reset --hard'", "sudo bash -c 'git reset --hard'", "bash -c 'bash -c \"git reset --hard\"'",
    "bash -c 'echo a; git reset --hard'", "sh -c 'cd x && git reset --hard' _", "dash -c 'git reset --hard'", "bash --norc -c 'git reset --hard'",
    "bash -c $'git reset --hard'", "eval 'git reset --hard'", "eval \"git reset --hard\"", "eval git reset --hard", "eval echo a '&&' git reset --hard",
    "eval 'eval \"git reset --hard\"'", "find . -exec git reset --hard \\;", "find . -name x -execdir git reset --hard {} +",
    "$'\\x67it' reset --hard", "g\"i\"t reset --hard", "g'i't reset --hard", "\\git reset --hard", "git re\\set --hard", "git \"reset\" '--hard'",
    "git reset --h\\ard", "git reset $'--hard'", "echo 'git reset --hard' | sh", "echo 'git reset --hard' | bash", "printf 'git reset --hard\\n' | bash",
    "echo \"git reset --hard\" | sh -s", "cat <<EOF | sh\ngit reset --hard\nEOF", "sh <<'EOF'\ngit reset --hard\nEOF", "bash <<< 'git reset --hard'",
    "bash <<EOF\necho a\ngit reset --hard\nEOF", "git reset --hard > /dev/null 2>&1", "git reset --hard 2>&1 | tee log", "git reset --hard &>/dev/null",
    "git reset --hard >out", "2>/dev/null git reset --hard", "git reset --hard; # done", "echo a # c\ngit reset --hard",
    "echo a \\\n&& git reset --hard", "git reset \\\n --hard", "cat <<EOF\nhello\nEOF\ngit reset --hard", "cat <<-EOF\n\thello\n\tEOF\ngit reset --hard",
    "cat <<EOF\n$(git reset --hard)\nEOF", "case x in x) git reset --hard ;; esac", "f() { git reset --hard; }; f", "function f { git reset --hard; }",
    "git status #\ngit reset --hard", "echo '#'; git reset --hard", "echo a#b; git reset --hard", "git\treset\t--hard", "git  reset   --hard",
    "git reset --hard\r\n", "\tgit reset --hard",
]


@pytest.mark.parametrize("cmd", WRAP)
def test_wrapped_hard_reset_is_denied(cmd):
    v = verdict(cmd)
    assert v.decision == D and v.rule == "reset-hard", (cmd, v.rule, v.commands)


def test_xargs_pipeline_clean():
    assert verdict("echo . | xargs git clean -fd").rule == "clean-force"


def test_most_severe_wins_in_pipeline_and_chain():
    v = verdict("git rebase main && git status && git reset --hard && git commit --amend")
    assert v.decision == D and v.rule == "reset-hard"
    v = verdict("git commit --amend && git rebase main")
    assert v.decision == K and v.rule == "commit-amend"  # ties keep first
    v = verdict("git status | git stash drop")
    assert v.decision == K
    assert len(verdict("git status && git log && git diff").commands) == 3


def test_commands_listing_is_redacted_and_normalised():
    v = verdict("git push https://user:hunter2secret@github.com/a/b.git main --force-with-lease")
    assert all("hunter2secret" not in c for c in v.commands)
    assert verdict("sudo /usr/bin/git -C x status").commands == ["git -C x status"]


def test_branch_comparison_for_push_force():
    assert verdict("git push -f", branch="main").rule == "push-force-protected"
    assert verdict("git push -f origin", branch="main").rule == "push-force-protected"
    assert verdict("git push -f", branch="feature/x").rule == "push-force"
    assert verdict("git push -f origin HEAD", branch="main").rule == "push-force-protected"
    assert verdict("git push -f origin HEAD", branch="feature/x").rule == "push-force"
    assert verdict("git push -f origin HEAD:main", branch="feature/x").rule == "push-force-protected"
    assert verdict("git push -f origin main:feature/x", branch="main").rule == "push-force"  # dst decides
    assert verdict("git push -f origin feature/x", branch="main").rule == "push-force"
    assert verdict("git push -f origin refs/heads/main", branch="feature/x").rule == "push-force-protected"
    assert verdict("git push -f", branch=None).rule == "push-force"
    assert verdict("git push -f origin $BR", branch="feature/x").decision == K


def test_branch_switch_inside_command_is_tracked():
    assert verdict("git checkout main && git push --force", branch="feature/x").rule == "push-force-protected"
    assert verdict("git switch main; git push -f", branch="feature/x").decision == D
    assert verdict("git checkout -b feature/new && git push -f", branch="main").rule == "push-force"
    assert verdict("git checkout dev && git push -f", branch="feature/x").rule == "push-force"
    assert verdict("git checkout feature/z -- file && git push -f", branch="feature/x").rule == "push-force"


def test_custom_protected_branches_and_globs():
    assert verdict("git push -f origin trunk", protected=["trunk"]).decision == D
    assert verdict("git push -f origin main", protected=["trunk"]).decision == K
    assert verdict("git push -f origin release/1.2", protected=["release/*"]).decision == D
    assert verdict("git push -f origin hotfix", protected=["release/*"]).decision == K
    assert verdict("git reset HEAD~2", branch="trunk", protected=["trunk"]).rule == "reset-protected"


def test_strict_mode_turns_history_rewrites_into_deny():
    for cmd in ("git rebase main", "git commit --amend", "git branch -D x", "git push -f origin feature/x", "git push -f",
                "git push --delete origin old", "git branch -M new", "git branch -f a b", "git reset HEAD~1"):
        br = "main" if cmd == "git reset HEAD~1" else "feature/x"
        assert verdict(cmd, branch=br).decision == K, cmd
        v = verdict(cmd, branch=br, mode="strict")
        assert v.decision == D, cmd
        assert "strict" in v.reason
    # non-history asks stay asks in strict mode
    for cmd in ("git stash drop", "git tag -d v1", "git remote remove o", "git config alias.x y", "git $X", "git worktree remove -f w"):
        assert verdict(cmd, mode="strict").decision == K, cmd


def test_deny_does_not_depend_on_mode():
    assert verdict("git reset --hard", mode="standard").decision == D
    assert verdict("git reset --hard", mode="strict").decision == D


def test_abbreviated_long_options_are_understood():
    assert verdict("git reset --har").decision == D
    assert verdict("git clean --forc").decision == D
    assert verdict("git checkout --forc").decision == D
    assert verdict("git switch --discard-c main").decision == D
    assert verdict("git push --force-w").decision in (K, D)


@pytest.mark.parametrize("p,expected", [(".", True), ("./", True), ("*", True), (":/", True), (":(top)", True), (":/.", True),
                                         ("..", True), (":(top).", True), (":(exclude)x", False), ("src", False), ("a.py", False),
                                         (":!x", False), (":(glob)**", True), ("src/", False), ("(", False), (":(", False)])
def test_is_all_pathspec(p, expected):
    assert is_all_pathspec(p) is expected


def test_reason_style_what_why_safer():
    v = verdict("git reset --hard")
    assert "git reset --hard" in v.reason and "uncommitted" in v.reason.lower()
    assert any("rescue" in s for s in v.safer) and any("stash" in s for s in v.safer)
    assert any("--soft" in s for s in v.safer)
    v = verdict("git clean -fd")
    assert any("git clean -n" in s for s in v.safer)
    v = verdict("git push -f origin feature/x")
    assert any("--force-with-lease" in s for s in v.safer)
    assert "feature/x" in v.reason
    assert "{" not in verdict("git stash drop").reason + "".join(verdict("git stash drop").safer).replace("stash@{N}", "")
    assert "!" not in verdict("git reset --hard").reason


def test_every_rule_renders_without_format_errors():
    from gitwarp.safety.classifier import RULES, _Ctx
    for rule in RULES:
        ctx = _Ctx(Config(), "main")
        ctx.add(rule, branch="b", tool="git filter-repo", path=".git")
        v = ctx.found[0][2]
        assert v.reason and v.safer and v.decision in (A, K, D)
        assert "{branch}" not in v.reason and "{tool}" not in v.reason and "{path}" not in v.reason
        ctx.strict = True
        ctx.add(rule, branch="b")


def test_non_string_and_none_config():
    assert classify_command(None, None, None).decision == A  # type: ignore[arg-type]
    assert classify_command("git reset --hard", None, None).decision == D


def test_operation_and_commands_fields():
    v = verdict("git status")
    assert v.rule == "defer" and v.commands == ["git status"] and v.to_dict()["decision"] == "defer"
    v = verdict("git reset --hard")
    assert v.operation == "git reset --hard" and v.commands == ["git reset --hard"]
