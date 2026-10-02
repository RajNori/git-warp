# Safety model

Git Warp's guard is a deterministic classifier over the **command string** that Claude is about to run with the
`Bash` tool. It is a safety net against accidents, not a security sandbox. Read
[guard-limitations.md](guard-limitations.md) alongside this page.

## How a decision is made

1. Claude Code calls the `PreToolUse` hook (`hooks/hooks.json`, matcher `Bash`) which runs
   `scripts/hook_git_guard.py` with the hook JSON on stdin.
2. `gitwarp/hooks/git_guard.py` extracts `tool_input.command`. Empty or non-Bash input is allowed (`{}`).
3. If the command text contains `git` (and is at most 20,000 characters), it loads `.claude/git-warp.local.md` and
   reads the current branch from `cwd`.
4. `gitwarp/safety/classifier.py` tokenizes the shell text (`gitwarp/safety/tokenizer.py`), finds every Git
   invocation, applies the rules below and returns the most severe verdict (`deny` > `ask` > `allow`).
5. The hook answers with `permissionDecision` `deny` or `ask` plus a message (what was detected, why it matters,
   up to four safer alternatives; deny messages add "ask the user to run it themselves"). For `allow` it prints `{}`.

What the tokenizer follows: quotes, `;`, `&&`, `||`, pipes, subshells, `$( )`, backticks, `bash -c`, `eval` with a
literal string, `xargs`, `find -exec`, here-documents piped into a shell (literal text only), wrappers (`env`,
`sudo`, `command`, `nohup`, `time`, `timeout`, ...), absolute paths to `git`, `git -C`, `git -c` and `--git-dir`.

No model is involved in the decision. A `deny` is not overridden by the model; the user would have to run the
command themselves.

## The rules

Verified with `python3 scripts/warp.py guard check "<cmd>" --branch feature/x` unless the branch is noted.

### deny

| Rule | Triggered by (examples) |
|---|---|
| `reset-hard` | `git reset --hard`, `git reset --hard HEAD~1` (any branch) |
| `clean-force` | `git clean -f`, `-fd`, `-fdx`, `git clean -f $X` |
| `checkout-discard-all` | `git checkout .`, `git checkout -- .`, `git restore .`, `git checkout main -- .` |
| `checkout-force` | `git checkout -f main`, `--force` |
| `switch-discard` | `git switch -f main`, `--discard-changes` |
| `reflog-destroy` | `git reflog expire ...`, `git reflog delete ...` |
| `gc-prune-now` | `git gc --prune=now`, `--prune=all` |
| `prune` | `git prune` |
| `stash-clear` | `git stash clear` |
| `update-ref-delete` | `git update-ref -d refs/heads/foo` |
| `push-force-protected` | `git push --force origin main`, `-f` while on `main`, `+main`, `--force-with-lease ... main`, any protected glob |
| `push-mirror` | `git push --mirror` |
| `push-delete-protected` | `git push --delete origin main`, `git push origin :main` |
| `history-tool` | `git filter-branch`, `git filter-repo` |
| `rm-git` | `rm -rf .git` (a path ending in `.git`) |

### ask

| Rule | Triggered by (examples) |
|---|---|
| `push-force` | `git push --force origin feature/x`, `-f`, `--force-with-lease` to a non-protected branch |
| `push-delete` | `git push origin --delete old-branch` |
| `rebase` | `git rebase main` |
| `commit-amend` | `git commit --amend` |
| `branch-force-delete` | `git branch -D old` |
| `branch-force-move` | `git branch -f foo HEAD~1`, `git branch -M newname` |
| `reset-protected` | `git reset HEAD~1` / `--mixed` while on a protected branch (`reset --hard` is already `deny`) |
| `update-ref-protected` | `git update-ref refs/heads/main <sha>` |
| `update-ref-delete-other` | `git update-ref -d refs/tags/v1` |
| `stash-drop` | `git stash drop` |
| `tag-rewrite` | `git tag -d v1`, `git tag -f v1` |
| `worktree-force-remove` | `git worktree remove --force` |
| `submodule-deinit-force` | `git submodule deinit --force` |
| `alias-config`, `alias-inline`, `env-alias` | `git config alias.x ...`, `git -c alias.x=...`, `GIT_CONFIG_KEY_0=alias.x` |
| `remote-modify` | `git remote set-url`, `git remote remove` |
| `unresolved-subcommand`, `unresolved-command` | `git $CMD ...`; a command word built from a variable on a line that mentions git |
| `reset-unresolved`, `clean-unresolved` | `git reset $MODE`, `git clean $FLAGS` |
| `eval-git`, `shell-git` | `eval "$X"` / `bash -c "$X"` where the computed text mentions git |
| `too-complex` | over 20,000 characters, nesting beyond the parser limit or step budget, and it mentions git |

### allow (examples that were checked)

`git status`, `git log`, `git add -A && git commit -m wip`, `git push origin feature/x`, `git checkout -b topic`,
`git switch topic`, `git stash push -u`, `git gc`, `git branch -d old`, `git clean -n`, `git reset --soft HEAD~1`,
`git reset HEAD~1` on a non-protected branch, `git worktree remove ../x`, and non-Git commands such as `ls -la`.

### Allowed, but you might not expect it

- `git checkout -- src/app.py` and `git restore src/app.py` (one named file's uncommitted edits are discarded).
  Only whole-tree forms (`.`, `*`, `:/`, `..`) are denied.
- `git reset --soft` and `git reset <rev>` on non-protected branches.
- `git clean -n` (dry run) is correctly allowed; `git clean -f` is denied.

## Protected branches

Default: `main`, `master`, `develop`, `development`, `production`, `prod`, `release`. Override with
`protected_branches` (shell-style globs such as `release/*`, matched with `fnmatch`). The current branch is looked
up before the command runs. Within a single command line, `cd`, `-C`, `--git-dir`, `checkout` and `switch` make the
branch uncertain; force pushes with no explicit target are then treated as unknown (`ask`, or `deny` if any
possible branch is protected).

## Strict mode

`safety_mode: strict` turns these `ask` rules into `deny`: `rebase`, `commit-amend`, `branch-force-delete`,
`branch-force-move`, `push-force`, `push-delete`, `reset-protected`, `update-ref-protected`. Verified:
`git rebase main` is `ask` in standard mode and `deny` in strict mode. The other `deny` rules are the same in both
modes. You can try a mode without a config file: `guard check "..." --mode strict`.

## Failure behaviour

- Guard internal error: a command that mentions `git` yields `ask` with "Git Warp guard error — verify manually";
  a command that does not mention git is allowed.
- Malformed hook input: no-op (`{}`).
- Input over 20,000 characters or too deeply nested: `ask` if it mentions git (`too-complex`), otherwise not
  analysed.

## What it does not cover

Scripts, variable expansion, computed `eval`, `curl | sh`, global-config aliases, `GIT_DIR`-style environment
tricks, `ssh`/`docker exec` wrappers, other tools (Write/Edit, MCP, IDE), non-Git deletion other than
`rm -rf .git`. Two examples observed while writing this page: `$GIT reset --hard` and `./cleanup.sh` were both
`allow`. See [guard-limitations.md](guard-limitations.md).

## Other safety properties of the plugin

- The CLI uses argument lists for Git (no shell), a single execution layer (`gitwarp/core/git.py`), timeouts, and
  `GIT_OPTIONAL_LOCKS=0` for read-only analysis (ADR-3).
- Refs passed to Git are validated and paths are passed after `--`.
- Git Warp never runs repository-provided code: no test commands, hooks or scripts. `bisect plan --test` only
  validates and echoes the string.
- The only commands that mutate: `rescue preserve` (creates a new branch, refuses to overwrite) and
  `memory forget --yes` (deletes Git Warp's own state files). Memory/index commands also write `warp.db`.
- Skills instruct Claude to propose rather than run mutating Git commands. That is prompt guidance, not enforcement.

## Try it

```bash
python3 scripts/warp.py guard check "git push --force origin main" --branch feature/x
python3 scripts/warp.py guard check "git rebase main" --mode strict
python3 scripts/warp.py guard check "git push --force origin release/1.2" --protected "release/*"
```

`guard check` never executes the command.
