# Git Warp guard: what it can and cannot see

The guard is a deterministic classifier over the **command string** Claude is about to run through the Bash tool. It
tokenizes the shell text (quotes, separators, subshells, `$( )`, backticks, `bash -c`, `eval "literal"`, `xargs`,
`find -exec`, wrappers such as `env`/`sudo`/`command`, absolute git paths, `git -C/-c`) and classifies each Git
invocation. It is a safety net against accidents, not a security sandbox. It does not claim to be complete.

## What it recognises (summary)

- `deny`: `reset --hard`, forced `clean`, whole-tree `checkout`/`restore`, `checkout -f`, `switch --discard-changes`, `reflog expire/delete`, `gc --prune=now` (also via `-c gc.pruneExpire=now` / `gc.reflogExpire=now`), `prune`, `stash clear`, `update-ref -d/--delete` on branches, force push to protected branches, `push --mirror`, `filter-branch`, `checkout-index -f -a`, `read-tree --reset -u`, whole-tree `git rm -f`, `rm -rf` of a path ending in `.git`.
- `ask`: history rewriting (`rebase`, `commit --amend`, `branch -D`, force push to other branches), `stash drop`, `tag -d/-f`, alias/remote edits, `update-ref --stdin`, `push --prune`, `checkout -B`/`switch -C` onto a protected or dynamic target, `checkout-index -f`, whole-tree `git rm -r`, option words with a dynamic suffix on destructive-capable subcommands, and interpreter `-c/-e` strings containing a destructive git phrase.
- Strings nested in `submodule foreach`, `rebase --exec/-x` and `bisect run` are analysed. For an unknown launcher (`arch`, `xcrun`, `flock`, `watch`, ...) whose later words include a literal `git` word, the tail starting at that word is classified (text/search/print tools such as `echo`, `grep`, `cat`, and `ssh`/`docker`/`kubectl` are excluded).
- Whole-tree pathspec spellings (`.`, `./.`, `././`, `.//`, `a/..`, `:/`) are normalised.

## Blind spots

- **Scripts and files.** `./cleanup.sh`, `make clean`, `npm run reset`, `python tool.py` may run any git command; the guard sees only the invocation.
- **Variable expansion.** `git $CMD` cannot be resolved; an unresolved Git *subcommand* escalates to `ask`. A dynamic *suffix* on an option word of a destructive-capable subcommand (`git reset --hard$IFS`) asks (or denies when the literal part already proves destructiveness). A dynamic command head that could be git (`$(echo git) ...`, `$GIT ...`) asks, and its literal arguments are classified. Arbitrary computed values are still not resolved.
- **Computed `eval` / `bash -c "$X"`.** Only literal strings are analysed. A computed string that mentions `git` escalates to `ask`; one that does not is not inspected.
- **Piped scripts.** `echo git reset --hard | sh`, `printf %s ... | bash` and here-documents into a shell are analysed when the text is literal. `curl ... | sh`, `cat file | sh`, process substitution (`bash <(echo ...)`) or any generated stream is not.
- **Not covered:** `git checkout -- <file>` and `git restore <file>` on a single path, `git rm -f <single file>`, `git fetch -f` / `git pull -f` (can overwrite a local branch), and `checkout -B`/`switch -C` onto a non-protected, literal branch (allowed).
- **`GIT_*` environment tricks.** `GIT_DIR`, `GIT_WORK_TREE`, `GIT_EXTERNAL_DIFF`, `GIT_SSH_COMMAND` and similar in the environment are not interpreted. Only `GIT_CONFIG_KEY_n=alias.*` is flagged.
- **Aliases from global/system config** (`~/.gitconfig`). Defining an alias in the command (`-c alias.x=...`, `git config alias.x ...`) is flagged `ask`, but *using* an alias defined elsewhere (`git nuke`) is invisible.
- **Other code-running config.** `core.hooksPath`, `core.fsmonitor`, `core.sshCommand`, `diff.external` and hooks inside the repository are not inspected.
- **Branch tracking.** The current branch is read before the command runs. Within one command line, `cd`, `-C`, `--git-dir`, and `checkout`/`switch` make the branch uncertain; the guard then treats force pushes with no explicit target as unknown (`ask`, or `deny` if any possible branch is protected).
- **Other tools.** Only the `Bash` tool is guarded. Edits via Write/Edit (including writes under `.git/`), MCP servers, IDE actions, remote shells (`ssh host 'git ...'`), containers (`docker exec`) and other programs that call Git are outside its view.
- **Output options.** `--output=<file>` on `git diff/log/show` can overwrite a file; it is not flagged.
- **Non-Git destruction.** Only `rm -rf` on a path ending in `.git` is recognised. `find -delete`, `mv .git`, `shred`, `dd` and similar are not.
- **Pathological input.** Commands over 20,000 characters, nesting deeper than the parser limit, or inputs that exhaust its step budget are not analysed; if they mention `git` they are escalated to `ask`.
- **Project config is untrusted input.** `.claude/git-warp.local.md` lives in the working tree; a repository can set `protected_branches` to something narrower. The unconditional `deny` rules do not depend on it.

## Failure behaviour

- If the guard itself errors, a command that mentions `git` yields `ask` ("Git Warp guard error — verify manually"); other commands pass. Malformed hook input is a no-op.
- The guard's Git lookups (repo root, branch) use a 2-second timeout; on timeout or error it falls back to default config and no known branch and still classifies.
- An internal 6-second wall-clock deadline makes the guard answer `ask` ("Git Warp guard timed out") before the host's 10-second hook timeout. **If the host nevertheless kills the hook (for example under extreme machine load), Claude Code proceeds without the guard** — hook timeouts fail open.
- Redaction (used for echoed command text) is bounded in time and size so hostile input cannot stall the hook.

## Tuning

`.claude/git-warp.local.md` sets `protected_branches` (glob patterns allowed) and `safety_mode: strict` (history-rewriting `ask` verdicts become `deny`). `deny` rules for destructive operations do not depend on the mode.
