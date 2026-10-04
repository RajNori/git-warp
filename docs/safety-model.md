# Safety model

Git Warp's guard is a deterministic classifier over the **command string** that Claude is about to run with the
`Bash` tool. It is a safety net against accidents, not a security sandbox. Read
[guard-limitations.md](guard-limitations.md) alongside this page.

## How a decision is made

1. Claude Code calls the `PreToolUse` hook (`hooks/hooks.json`, matcher `Bash`) which runs
   `scripts/hook_git_guard.py` with the hook JSON on stdin.
2. `gitwarp/hooks/git_guard.py` extracts `tool_input.command`. A payload with nothing to classify (an empty command, or a
   tool other than Bash) defers (`{}`). A payload the guard cannot interpret (malformed JSON, not a JSON object, a Bash call
   with a missing or non-object `tool_input`, a missing or non-string `command`, more than 2,000,000 characters) answers
   **ask**, never silence.
3. If the command text contains `git` (and is at most 20,000 characters), it loads the policy (built-in floor, then
   `~/.claude/git-warp.local.md`, then the repository's `.claude/git-warp.local.md`; see
   [configuration.md](configuration.md)) and reads the current branch from `cwd`.
4. `gitwarp/safety/classifier.py` tokenizes the shell text (`gitwarp/safety/tokenizer.py`), finds every Git
   invocation, applies the rules below and returns the most severe verdict (`deny` > `ask` > `defer`).
5. The hook answers with `permissionDecision` `deny` or `ask` plus a message (what was detected, why it matters,
   up to four safer alternatives; deny messages add "ask the user to run it themselves"). For `defer` it prints `{}`.

### DENY, ASK and DEFER

- **DENY**: a clearly recognised prohibited destructive operation. The command does not run.
- **ASK**: a risky but legitimate operation, or an ambiguous / dynamic construct whose behaviour the guard cannot
  establish. Claude Code asks the user to confirm. In a non-interactive session an ASK is a refusal.
- **DEFER**: the command is confidently outside the guard's scope, or confidently safe by explicit policy. **DEFER is
  not an approval.** The hook prints `{}` and Claude Code's ordinary permission rules decide. A DEFER is never turned
  into an allow by Git Warp, and a DENY or ASK is not overridden by a pre-approved `Bash(...)` rule (verified in a live
  Claude Code session, see [../planning/LIVE_ACCEPTANCE.md](../planning/LIVE_ACCEPTANCE.md)).

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
| `fetch-force-protected` | `git fetch --force origin main:main`, `git fetch origin +refs/heads/*:refs/heads/*` (a forced refspec whose destination is, or may be, a protected local branch) |
| `rm-git` | `rm -rf .git`; any `rm -rf` of a path ending in `.git` (so `rm -rf foo.git` is a known false positive) |
| `read-tree-reset` | `git read-tree --reset -u HEAD` |
| `rm-tree` | `git rm -rf .` (whole tree, forced) |
| `checkout-discard-all` | also `git checkout-index -f -a` |
| (same rules as above) | variable or launcher forms that still expose a literal destructive command: `$GIT reset --hard`, `GIT=git; $GIT reset --hard`, `git reset --hard$IFS`, `arch git reset --hard`, `flock x git clean -fd` |

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
| `option-unresolved` | an option word with a dynamic suffix on a destructive-capable subcommand, when the literal part does not already prove it destructive |
| `dynamic-argument` | a dynamic word (`$(...)`, backticks, `${VAR}`, process substitution, a variable assigned more than once or to a computed value) in an option, subcommand or executable position of a destructive-capable Git subcommand: `git reset $(echo --hard)`, ``git reset `echo --hard` ``, `git clean $(echo -fdx)` |
| `dynamic-argument` (variables) | an **unresolved variable** in a destructive-capable subcommand, quoted or not, in any position (its value may start with `-`, `+` or `:`): `git push $FLAGS origin main`, `git branch $FLAGS old`, `git checkout "$BRANCH"`, `git push origin "$B"` (with `B=:dev` that deletes a remote branch). Exempt: values of message/file options (`-m "$MSG"`), words after `--`, a literal non-option prefix without a colon (`feature-$X`, `HEAD~$N`), a variable with a known static assignment, and read-only subcommands |
| `exec-option` | an option, subcommand or environment assignment that makes Git run a helper program: `git diff --ext-diff`, `--textconv`, `git fetch --upload-pack=...`, `git push --receive-pack=...`, `--exec`, `git archive --remote`, `difftool`/`mergetool`/`credential`/`send-email`/`gui`, `GIT_EXTERNAL_DIFF=... git diff`, `GIT_SSH_COMMAND=...`, `GIT_PAGER=...` (harmless values such as `cat` or `less` are exempt) |
| `output-file` | a file-writing option on a read-looking command: `git diff --output=FILE` (and `--out`, `--outp`), the same on `log`, `show`, `blame`, `rev-list`, `format-patch -o`, `archive -o/--output`, `bundle create`, `fast-export --export-marks`, `grep -O` |
| `config-exec` | `git -c core.pager=... `, `-c core.sshCommand=...`, `-c core.fsmonitor=...`, `-c core.hooksPath=...`, `-c diff.external=...`, textconv, `credential.helper` and similar program-running config given on the command line (harmless pagers such as `cat` or `less` are exempt) |
| `generated-script` | a file written by redirection or `tee` and then run in the same command: `echo 'git reset --hard' > gen.sh && bash gen.sh` |
| `stdin-script` | process substitution into a shell: `bash <(echo '...')` |
| `unknown-subcommand` | `git x`, `git nuke`, `git st`: a first word that is not a known Git subcommand may be a configured alias such as `alias.x = reset --hard`. Aliases cannot shadow built-ins, so real subcommands are unaffected. The guard never reads Git config; the list is static |
| `fetch-force-local` | a forced fetch (`+`, `--force`, `-f`, `--update-head-ok`) into a non-protected local branch, and `git pull --force` / `-f` |
| `branch-force-move` | also `git checkout -B main HEAD~1`, `git switch -C main`, `git checkout -B $X` (protected or dynamic target) |
| `update-ref-stdin` | `git update-ref --stdin` |
| `push-prune` | `git push --prune origin` |
| `read-tree-reset-index` | `git read-tree --reset HEAD` (no `-u`) |
| `rm-tree-ask` | `git rm -r .` |
| `checkout-index-force` | `git checkout-index -f <path>` |
| `interpreter-git` | `python3 -c "import os; os.system('git reset --hard')"` (interpreter string containing a destructive git phrase) |
| `eval-git`, `shell-git` | `eval "$X"` / `bash -c "$X"` where the computed text mentions git |
| `too-complex` | over 20,000 characters, nesting beyond the parser limit or step budget, and it mentions git |

### defer (examples that were checked)

`git status`, `git log`, `git add -A && git commit -m wip`, `git push origin feature/x`, `git checkout -b topic`,
`git switch topic`, `git stash push -u`, `git gc`, `git branch -d old`, `git clean -n`, `git reset --soft HEAD~1`,
`git reset HEAD~1` on a non-protected branch, `git worktree remove ../x`, and non-Git commands such as `ls -la`.

### Allowed, but you might not expect it

- `git checkout -- src/app.py` and `git restore src/app.py` (one named file's uncommitted edits are discarded).
  Only whole-tree forms (`.`, `*`, `:/`, `..`) are denied.
- `git rm -f <one file>`, `git fetch -f origin main` (no `src:dst` refspec), and `git checkout -B topic` onto a non-protected literal branch.
- A dynamic word in a read-only Git command: `git log $(git merge-base HEAD main)..HEAD`, `git commit -m "$(date)"`.
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

- Guard internal error: a command that mentions `git`, or a payload that could not be parsed, yields `ask` with
  "Git Warp guard error — verify manually"; a command that does not mention git defers.
- Malformed hook input: `ask` for anything that could be a Bash command (see step 2 above); `{}` for other tools.
- The guard has an internal 6-second deadline and answers `ask` ("Git Warp guard timed out") before the host's 10-second
  hook timeout. If the host kills the hook anyway, Claude Code proceeds without the guard (hook timeouts fail open).
  See [guard-limitations.md](guard-limitations.md).
- Input over 20,000 characters or too deeply nested: `ask` if it mentions git (`too-complex`), otherwise not
  analysed.

## What it does not cover

Script contents (`./cleanup.sh`, `make clean`), values that only exist at run time, `curl | sh`, `ssh`/`docker exec`
wrappers, other tools (Write/Edit, MCP, IDE), non-Git deletion other than `rm -rf` of a `.git` path. The guard does not
emulate a shell: where destructive behaviour could be concealed by a dynamic expression it asks instead of guessing.
See [guard-limitations.md](guard-limitations.md).

## Other safety properties of the plugin

- The CLI uses argument lists for Git (no shell) through one execution layer (`gitwarp/core/git.py`) with timeouts,
  bounded output, a scrubbed environment and command-level suppression of repository-configured helpers. The actual
  boundary, including what is *not* suppressed, is in [git-boundary.md](git-boundary.md).
- Every revision that reaches Git passes through one normalisation service (`gitwarp/core/revisions.py`): option-shaped
  input is rejected, the value is resolved with `--end-of-options` to a full object id, and only that id is used
  afterwards. Paths are passed after `--`.
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
