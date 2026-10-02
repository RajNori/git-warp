# Git Warp guard: what it can and cannot see

The guard is a deterministic classifier over the **command string** Claude is about to run through the Bash tool. It
tokenizes the shell text (quotes, separators, subshells, `$( )`, backticks, `bash -c`, `eval "literal"`, `xargs`,
`find -exec`, wrappers such as `env`/`sudo`/`command`, absolute git paths, `git -C/-c`) and classifies each Git
invocation. It is a safety net against accidents, not a security sandbox. It does not claim to be complete.

## Blind spots

- **Scripts and files.** `./cleanup.sh`, `make clean`, `npm run reset`, `python tool.py` may run any git command; the guard sees only the invocation.
- **Variable expansion.** `git $CMD` and `$GIT reset --hard` cannot be resolved. An unresolved Git *subcommand* escalates to `ask`; an unresolved *argument* (for example `git push --force origin $BR`) is judged as far as the literal words allow.
- **Computed `eval` / `bash -c "$X"`.** Only literal strings are analysed. A computed string that mentions `git` escalates to `ask`; one that does not is not inspected.
- **Piped scripts.** `echo '...' | sh` and here-documents into a shell are analysed when the text is literal. `curl ... | sh`, `cat file | sh`, or any generated stream is not.
- **`GIT_*` environment tricks.** `GIT_DIR`, `GIT_WORK_TREE`, `GIT_EXTERNAL_DIFF`, `GIT_SSH_COMMAND` and similar in the environment are not interpreted. Only `GIT_CONFIG_KEY_n=alias.*` is flagged.
- **Aliases from global/system config** (`~/.gitconfig`). Defining an alias in the command (`-c alias.x=...`, `git config alias.x ...`) is flagged `ask`, but *using* an alias defined elsewhere (`git nuke`) is invisible.
- **Other code-running config.** `core.hooksPath`, `core.fsmonitor`, `core.sshCommand`, `diff.external` and hooks inside the repository are not inspected.
- **Branch tracking.** The current branch is read before the command runs. Within one command line, `cd`, `-C`, `--git-dir`, and `checkout`/`switch` make the branch uncertain; the guard then treats force pushes with no explicit target as unknown (`ask`, or `deny` if any possible branch is protected).
- **Other tools.** Only the `Bash` tool is guarded. Edits via Write/Edit, MCP servers, IDE actions, remote shells (`ssh host 'git ...'`), containers (`docker exec`) and other programs that call Git are outside its view.
- **Non-Git destruction.** Only `rm -rf` on a path ending in `.git` is recognised. `find -delete`, `mv .git`, `shred`, `dd` and similar are not.
- **Pathological input.** Commands over 20,000 characters, nesting deeper than the parser limit, or inputs that exhaust its step budget are not analysed; if they mention `git` they are escalated to `ask`.

## Failure behaviour

If the guard itself errors, a command that mentions `git` yields `ask` ("Git Warp guard error - verify manually"); other commands pass. Malformed hook input is a no-op.

## Tuning

`.claude/git-warp.local.md` sets `protected_branches` (glob patterns allowed) and `safety_mode: strict` (history-rewriting `ask` verdicts become `deny`). `deny` rules for destructive operations do not depend on the mode.
