# The Git process boundary: what Git Warp's own Git calls do and do not defend against

Git Warp's evidence commands and hooks run `git` on repositories you did not necessarily write. A repository is
attacker-influenced input: its `.git/config`, `.gitattributes` and hooks can name programs that Git will run during
ordinary read-only commands such as `git status` or `git diff` (`core.fsmonitor`, `diff.external`,
`diff.<driver>.textconv`, `filter.<name>.clean`, `core.hooksPath`, pagers, ...), and the process environment can
redirect Git (`GIT_DIR`, `GIT_CONFIG_*`, `GIT_EXTERNAL_DIFF`, ...). This page states exactly what Git Warp does about
that. It does **not** claim that "all Git configuration is disabled".

**Scope.** This boundary covers the Git commands Git Warp itself runs (`scripts/gitwarp/core/git.py`). It does not
cover the raw `git` commands Claude runs through the Bash tool; those are governed by Claude Code's permissions and the
[guard](safety-model.md). The text below is generated from the module docstring of `core/git.py`, which is the
authoritative source (the tests in `tests/security/test_git_env_config.py` and `tests/unit/test_core_git_*.py` check it).

---

Every Git invocation in Git Warp goes through `run`; no other module may import `subprocess`
(enforced by `tests/unit/test_architecture_invariants.py` and `tests/unit/test_core_git_boundary.py`).

What `run` guarantees
---------------------------
* argv list only, `shell=False`, explicit `cwd` (the current directory when none is given), finite timeout;
* stdin is `/dev/null` unless `input` is given; the child can never prompt (`GIT_TERMINAL_PROMPT=0`);
* stdout AND stderr are streamed into capped buffers (`max_output` / `max_stderr`); a command that exceeds
  the cap is killed and the `Result` carries `truncated=True` (its `returncode` is then reported as 0
  because the captured prefix is valid output; callers that need completeness must check `truncated`);
* the child runs in its own session/process group; on timeout, on over-long output and on exit with lingering
  descendants the WHOLE group is SIGKILLed, so helper processes (fsmonitor, textconv, filters, pagers, ssh)
  die with the git process;
* typed errors: `GitNotFound`, `NotARepository`, `GitTimeout`, `GitRefused`
  (a command outside the allowlist or carrying a forbidden option), `GitError`.

Hostile ENVIRONMENT boundary (the child never inherits these)
-------------------------------------------------------------
`GIT_*` is handled by ALLOWLIST: no `GIT_*` variable of the parent is passed on.  Git Warp sets only
`GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `GIT_EDITOR=true` and `GIT_ATTR_NOSYSTEM=1`
(+ `LC_ALL=C`, `PAGER=cat`).  So `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
`GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`, `GIT_NAMESPACE`, `GIT_CEILING_DIRECTORIES`,
`GIT_CONFIG`, `GIT_CONFIG_COUNT`/`_KEY_n`/`_VALUE_n`, `GIT_CONFIG_PARAMETERS`, `GIT_EXTERNAL_DIFF`,
`GIT_DIFF_OPTS`, `GIT_SSH`, `GIT_SSH_COMMAND`, `GIT_ASKPASS`, `GIT_PROXY_COMMAND`, `GIT_EXEC_PATH`,
`GIT_TRACE*` (every variant), `GIT_PAGER` (replaced), `GIT_ALLOW_PROTOCOL` ... cannot redirect the repository
or execute anything.  The only config-redirection variables that survive are the inert forms
`GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` equal to the null device and a truthy `GIT_CONFIG_NOSYSTEM` (they
can only REMOVE configuration; this is how the test-suite isolates itself).  Any other value is dropped, so the
real `~/.gitconfig` / `/etc/gitconfig` apply: they are the user's own trusted files.  Also removed:
`PAGER`/`MANPAGER`/`LESS*` (replaced), `EDITOR`/`VISUAL`, `SSH_ASKPASS*`, `LANG`/`LANGUAGE`/`LC_*`
(replaced by `LC_ALL=C`), `LD_PRELOAD`/`LD_AUDIT`/`DYLD_INSERT_LIBRARIES`.  Everything else (`PATH`,
`HOME`, `USER`, `TMPDIR`, `XDG_*` ...) is kept because git needs it.  Environment VALUES are never logged
and never appear in error messages.

Hostile LOCAL CONFIG boundary (suppressed at invocation, whatever file defines it)
----------------------------------------------------------------------------------
Every command gets `--no-pager --no-optional-locks` and `-c` overrides (see `_HARDENING`): `core.fsmonitor`
(false), `core.hooksPath` (null device: no hook of any kind fires), `core.pager`/`core.editor`, `diff.external`,
`core.sshCommand`, `core.askPass`, `credential.helper`, `protocol.allow`/`protocol.ext.allow` (never),
`core.untrackedCache` (false), `log.showSignature` (false; no gpg), `gc.auto`/`maintenance.auto` (off),
`color.ui`, `core.quotePath`.  The `-c` options are inherited by git's own child processes (submodule
status etc.).  Patch-producing commands (diff, log, show, blame) additionally get `--no-ext-diff` and
`--no-textconv`, and callers cannot re-enable them (`--ext-diff`/`--textconv` are refused).  Clean/smudge/
process FILTERS are run by git itself during `status`/`diff` (to compare working-tree content); filters
defined in the repository's own config (local/worktree/command scope) are therefore enumerated with
`git config --show-scope` and emptied with `-c filter.<name>.clean=` etc. (plus `required=false`, so git does
not die on a filter it may no longer run) for commands that read the work tree.  Aliases are never invoked: only builtin subcommands from the allowlist
(`ALLOWED_SUBCOMMANDS`) are accepted, and the mutating/dangerous ones are restricted to one exact form each
(`branch <name> <sha>`, `stash list`, `worktree list`, `reflog show`, read-only `config`, `symbolic-ref
<name>`).  Options that write files or run programs (`--output`, `--open-files-in-pager`/`grep -O`,
`--exec-path`, `--git-dir=`, `--work-tree=`, `--lost-found`) are refused anywhere before `--`.

What is NOT suppressed (be honest about it)
-------------------------------------------
* `include.path` / `includeIf` in any config file are still processed (they can only add the same
  suppressed keys, but they can alter other settings);
* attributes: `.gitattributes` drivers other than textconv/external diff/filter (e.g. `diff=<driver>`
  funcname/word-regex settings, `merge` drivers - we never merge) are honoured; the global
  attributes file and `info/attributes` are read;
* filters defined in the USER's global/system config (e.g. git-lfs) keep working - they are trusted like the
  user's shell; only repository-scoped filter definitions are emptied (so an LFS repository configured with
  `git lfs install --local` may show its LFS files as modified in status/diff output);
* `safe.directory` / dubious-ownership checks are git's own and are not touched; a `-c` can't bypass them;
* replace refs/grafts (`refs/replace`), `core.excludesFile`, `core.worktree`/`core.bare` of the
  repository itself, `extensions.*`, submodule `.git` files and `gitdir:` pointers are used as git uses them;
* `PATH` is trusted (the `git` binary is found through it) and `HOME`-relative config is trusted;
* a hostile repository can still make git CPU/IO heavy (huge packs, pathological history): the timeout and the
  output caps bound the damage, they do not prevent it.

---

## Revisions

Every revision, branch, tag or SHA that reaches Git comes from one service, `scripts/gitwarp/core/revisions.py`:
option-shaped input (a leading `-`, NUL, control characters, `:` syntax such as `rev:path`) is rejected, the value is
resolved with `git rev-parse --verify --quiet --end-of-options <input>^{commit}`, the answer must be a full 40/64-hex
object id, and only that id is used in later `diff`, `log`, `show` or `merge-base` commands. The human label is kept
separately for display. Ranges (`A..B`, `A...B`) are accepted only by normalising each endpoint separately. Pathspecs
always follow `--`. `git.check_ref` no longer exists and a test forbids reintroducing a second path.

## How this was tested

Hostile fixtures install a marker command (it appends to a log outside the repository) through each vector and then
run every hook and every `warp.py` command; a control run with plain `git` proves the vector actually fires in the test
environment. Vectors: `core.fsmonitor`, `diff.external`, `diff.<driver>.textconv`, `filter.<name>.clean/smudge`,
`include.path`, `core.pager`, `pager.<cmd>`, `core.hooksPath`, `alias.*`, `core.sshCommand`, and the environment
variables `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
`GIT_CONFIG_COUNT/KEY_0/VALUE_0`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_EXTERNAL_DIFF`, `GIT_DIFF_OPTS`,
`GIT_SSH`, `GIT_SSH_COMMAND`, `GIT_PAGER`, `PAGER` and the `GIT_TRACE*` file variables (`tests/acceptance/test_hostile_fixtures.py`,
`tests/security/test_convergence_contracts.py`).
