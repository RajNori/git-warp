# Git Warp guard: what it can and cannot see

The guard is a deterministic classifier over the **command string** Claude is about to run through the Bash tool. It
tokenizes the shell text (quotes, separators, subshells, `$( )`, backticks, `bash -c`, `eval "literal"`, `xargs`,
`find -exec`, wrappers such as `env`/`sudo`/`command`, absolute git paths, `git -C/-c`) and classifies each Git
invocation as **DENY**, **ASK** or **DEFER**. It is a safety net against accidents, not a security sandbox. It does
not claim to be complete, and it does **not** emulate a shell: it never executes an expansion to discover intent.

## What it recognises (summary)

- `deny`: `reset --hard`, forced `clean`, whole-tree `checkout`/`restore`, `checkout -f`, `switch --discard-changes`, `reflog expire/delete`, `gc --prune=now` (also via `-c gc.pruneExpire=now` / `gc.reflogExpire=now`), `prune`, `stash clear`, `update-ref -d/--delete` on branches, force push or forced fetch into protected branches, `push --mirror`, `filter-branch`, `checkout-index -f -a`, `read-tree --reset -u`, whole-tree `git rm -f`, `rm -rf` of a path ending in `.git`.
- `ask`: history rewriting (`rebase`, `commit --amend`, `branch -D`, force push to other branches), forced fetch/pull into other local branches, `stash drop`, `tag -d/-f`, alias/remote edits, `update-ref --stdin`, `push --prune`, `checkout -B`/`switch -C` onto a protected or dynamic target, `checkout-index -f`, whole-tree `git rm -r`, an unrecognised first word after `git` (it may be an alias), dynamic words that could conceal a destructive option, subcommand or executable, a script written and then run in the same command, process substitution into a shell, and interpreter `-c/-e` strings containing a destructive git phrase.
- Strings nested in `submodule foreach`, `rebase --exec/-x` and `bisect run` are analysed. For an unknown launcher (`arch`, `xcrun`, `flock`, `watch`, ...) whose later words include a literal `git` word, the tail starting at that word is classified (text/search/print tools such as `echo`, `grep`, `cat`, and `ssh`/`docker`/`kubectl` are excluded).
- Whole-tree pathspec spellings (`.`, `./.`, `././`, `.//`, `a/..`, `:/`) are normalised.
- Piped and here-string scripts with literal text (`echo 'git reset --hard' | sh`, `bash <<< '...'`, `source /dev/stdin <<< '...'`) and literal `eval '...'` are analysed.

## How dynamic expressions are treated

A dynamic word is a command substitution, backticks, a parameter expansion, process substitution, or a variable
that is assigned more than once or to a computed value. The rule:

- In an **option, subcommand or executable position of a destructive-capable Git subcommand** (reset, clean, push,
  checkout, restore, branch, gc, reflog, stash, update-ref, tag, worktree, switch, prune, rm, read-tree,
  checkout-index, submodule, rebase, fetch, pull) the result is **ASK**, or **DENY** when the literal part already
  proves destruction.
- In a **read-only Git command** (`log`, `diff`, `show`, `status`, ...) or a non-Git command it is DEFER. A
  substitution made only of read-only Git commands or of `date`/`pwd`/`whoami` and similar is treated as harmless.
- Assignments are tracked literally within one command string and never evaluated. An *unresolved* variable (no assignment in the string) in a destructive-capable subcommand asks, in any position. A variable assigned exactly once to
  a static value is substituted (`FLAG=--hard; git reset $FLAG` is DENY); anything else is uncertain.

## Blind spots

- **Scripts and files.** `./cleanup.sh`, `make clean`, `npm run reset`, `python tool.py` may run any git command; the guard sees only the invocation. A file written in a *previous* command is not read; a file written and run in the *same* command is ASK without its content being analysed.
- **Values that only exist at run time.** Environment variables, files, `read`, loops and shell functions defined in an earlier command are invisible. A command longer than 20,000 characters that does not mention git is not analysed.
- **Computed `eval` / `bash -c "$X"`.** Only literal strings are analysed. A computed string that mentions `git` escalates to `ask`; one that does not is not inspected.
- **Streams.** `curl ... | sh` and `cat file | sh` cannot be inspected.
- **Friction by design.** Any unresolved variable in a destructive-capable subcommand asks, quoted or not, because its value may start with `-`, `+` or `:` (an option, a forced refspec or a deletion refspec: `BRANCH=:dev; git push origin "$BRANCH"` deletes a remote branch). So `git checkout "$BRANCH"` and `git push origin "$B"` prompt every time. Exempt: values of message/file options (`-m "$MSG"`), anything after a literal `--`, a word with a literal non-option prefix and no colon (`feature-$X`, `refs/heads/$B`, `HEAD~$N`), a variable with a known static assignment in the same command, and read-only subcommands.
- **Not covered:** `git checkout -- <file>` and `git restore <file>` on a single path, `git rm -f <single file>`, `git clean -i` (interactive), and `checkout -B`/`switch -C` onto a non-protected, literal branch (deferred). Dynamic words in `commit`, `config`, `merge`, `cherry-pick`, `remote` and `add` are not escalated.
- **Unknown subcommand list.** It is static (the built-ins of a recent Git plus a few common external commands). A subcommand added by a newer Git, or a custom `git-foo` on your PATH, asks until it is added to the list.
- **`GIT_*` environment tricks in the *command*.** `GIT_DIR`, `GIT_WORK_TREE`, `GIT_EXTERNAL_DIFF`, `GIT_SSH_COMMAND` and similar written into the command string are not interpreted. Only `GIT_CONFIG_KEY_n=alias.*` is flagged. (Git Warp's own Git calls are protected differently: see [git-boundary.md](git-boundary.md).)
- **Aliases.** Defining an alias in the command (`-c alias.x=...`, `git config alias.x ...`) asks, and *using* any word that is not a known Git subcommand asks. An alias that shadows nothing cannot hide behind a real subcommand name because Git does not allow it.
- **Other code-running config.** `core.hooksPath`, `core.fsmonitor`, `core.sshCommand`, `diff.external` and hooks inside the repository are not inspected by the guard for the *commands Claude runs*. Git Warp neutralises them for its **own** Git calls only.
- **Branch tracking.** The current branch is read before the command runs. Within one command line, `cd`, `-C`, `--git-dir`, and `checkout`/`switch` make the branch uncertain; the guard then treats force pushes with no explicit target as unknown (`ask`, or `deny` if any possible branch is protected).
- **Other tools.** Only the `Bash` tool is guarded. Edits via Write/Edit (including writes under `.git/`), MCP servers, IDE actions, remote shells (`ssh host 'git ...'`), containers (`docker exec`) and other programs that call Git are outside its view.
- **Execution-bearing options.** `--ext-diff`, `--textconv`, `--upload-pack`, `--receive-pack`, `--exec`, `archive --remote`, `clone -u/--template`, the always-prompting helpers (`difftool`, `mergetool`, `instaweb`, `daemon`, `credential`, `send-email`, `gui`, `gitk`, ...) and environment assignments such as `GIT_EXTERNAL_DIFF=`, `GIT_SSH_COMMAND=`, `GIT_PAGER=`, `GIT_EDITOR=`, `GIT_ASKPASS=`, `GIT_PROXY_COMMAND=` ask (rule `exec-option`); program-running config keys on `-c`/`git config` ask (rule `config-exec`). Config that is already in a file on disk is not read by the guard.
- **Output options.** `--output=<file>` and other file-writing options on `git diff/log/show/blame/format-patch/archive/bundle/...` are flagged `ask` (rule `output-file`). Skills still pre-approve `git diff/log/show` *prefixes*, and an allowed-tools prefix cannot exclude flags, so what protects you from `--output` is the guard's `ask`, which Claude Code does not override with a pre-approved `Bash(...)` rule (verified for a CLI allow rule; see [../planning/LIVE_ACCEPTANCE.md](../planning/LIVE_ACCEPTANCE.md) for the skill-level result). Raw `git status/diff/blame` that Claude runs can also run repository-configured fsmonitor, textconv or external-diff helpers (the `warp.py` paths neutralise these).
- **Non-Git destruction.** Only `rm -rf` on a path ending in `.git` is recognised. `find -delete`, `mv .git`, `shred`, `dd` and similar are not.
- **Pathological input.** Commands over 20,000 characters, nesting deeper than the parser limit, or inputs that exhaust its step budget are not analysed; if they mention `git` they are escalated to `ask`.
- **Policy sources.** A repository can only *tighten* the policy (add protected branches, raise strictness); it cannot remove the built-in floor. See [configuration.md](configuration.md).

## Failure behaviour

- If the guard itself errors, a command that mentions `git`, or a payload that could not be parsed, yields `ask` ("Git Warp guard error — verify manually"); other commands defer.
- Malformed hook input (empty stdin, malformed JSON, a non-object, a Bash call without a usable `command`, a payload over 2,000,000 characters) yields `ask`; events for other tools yield `{}`.
- The guard's Git lookups (repo root, branch) use a 2-second timeout; on timeout or error it falls back to the policy floor and no known branch and still classifies.
- An internal 6-second wall-clock deadline makes the guard answer `ask` ("Git Warp guard timed out") before the host's 10-second hook timeout. **If the host nevertheless kills the hook (for example under extreme machine load), Claude Code proceeds without the guard**: hook timeouts fail open. Measured guard latency at scale is well under a second ([../planning/SCALE_RESULTS.md](../planning/SCALE_RESULTS.md)).
- Redaction (used for echoed command text) is bounded in time and size so hostile input cannot stall the hook.

## Tuning

Policy keys (`protected_branches`, `safety_mode`) can be set in the user policy file and the repository policy file;
the effective policy is the strictest combination. `deny` rules for destructive operations do not depend on the
mode and cannot be configured away.
