# Independent convergence review (Codex)

- **Reviewer:** Codex CLI 0.160.0 (`codex exec`, workspace-write sandbox confined to a disposable clone; no tracked file was modified by the reviewer in any round).
- **Process:** six rounds. Round 1 reviewed `origin/warp/phoenix..warp/convergence` (84 files). Each later round re-attacked the previous round's fixes and probed for regressions on the incremental diff. Every finding was reproduced by the reviewer against real Git or a real shell before it was reported; every fix was then checked by the implementation owner against a real-Git/shell regression test.
- **Final state:** round 6 reported **no Critical or High issue**; every earlier Critical/High/Medium finding is resolved (table below). No finding was dismissed as false.
- **Severity totals across rounds** (an item that a later round re-opened is counted again, as the table shows): Critical 0; High 10 reported, 10 resolved; Medium 3 reported, 3 resolved; Low/Uncertain 0 reported as findings.
- **Not verified by the reviewer in any round:** behaviour inside a live Claude Code host (covered by `planning/LIVE_ACCEPTANCE.md`), non-macOS platforms, and the five Unix-socket fixture tests (its sandbox denies socket binds; they pass in the project's own runs).

## Findings and resolutions

| Round | ID | Severity | Finding | Resolution (commit) | Regression tests |
|---|---|---|---|---|---|
| 1 | GW-SEC-01 | HIGH | Truncated repository-filter enumeration left a filter executable | Fails closed (`filter_enumeration_incomplete`, `too_many_filters`), cap 8 MiB (`ea367a1`) | `tests/security/test_git_env_config_filters.py` |
| 1 | GW-GUARD-01 | HIGH | Unresolved `$FLAGS` in option position classified DEFER | Unresolved variables ask (`dea510c`) | `tests/unit/test_guard_unresolved_vars_output.py` |
| 1 | GW-PLUGIN-01 | HIGH | `git diff --output=FILE` not flagged while skills pre-approve `git diff:*` | `output-file`, `config-exec` rules (`dea510c`) | same |
| 1 | GW-STATE-01 | MEDIUM | Recorder compaction released its lock before the atomic replace | Stable lock held across read, transform and replace (`e0218cf`) | `tests/unit/test_storage_compaction_race.py` |
| 2 | GW-GUARD-01 (residue) | HIGH | `git push origin "$BRANCH"` with `BRANCH=:dev` deleted a remote branch | Any unresolved variable in a destructive subcommand asks, quoted or not (`381e716`) | `tests/unit/test_guard_unresolved_vars_output.py` (real bare remote) |
| 2 | GW-GUARD-02 | HIGH | `--ext-diff`, `--upload-pack`, `--receive-pack`, `--exec` classified DEFER | `exec-option` rule, environment and config keys (`5eab9c7`) | `tests/unit/test_guard_exec_options.py` |
| 2 | GW-STATE-01 (residue) | MEDIUM | An appender proceeded unlocked after a 10 s lock timeout and its record was lost | `LockTimeout`: nothing proceeds without the lock (`6dfa98a`, `269fb07`) | `tests/unit/test_storage_lock_timeout.py` |
| 3 | GW-GUARD-01 (glob) | HIGH | `git clean -*`, `git clean {,-f}` expand to `-f` in a shell | Glob/brace words that can begin an option ask (`999182a`) | `tests/unit/test_guard_glob_expansion.py` (real shell, real `git clean`) |
| 3 | GW-GUARD-02 (surface) | HIGH | `GIT_CONFIG_PARAMETERS=`, `git config --edit`, `git bisect run` classified DEFER | Default-ask for `GIT_*` assignments, program-running surface derived from the Git 2.53 documentation (`3ee3308`) | `tests/unit/test_guard_program_surface.py` |
| 4 | GW-GLOB-02 | HIGH | zsh `extendedglob`: `git clean -(f\|d)` expands to `-f -d` | Pattern words kept as one word and ask (`28bebdc`) | `tests/unit/test_guard_extglob.py` (real zsh and bash) |
| 4 | GW-EXEC-02 | HIGH | `git bisect run cp ...` / `submodule foreach cp ...` deferred (file-writing tools exempt) | Strict read-only runner allowlist, no redirection (`cb8e9d6`) | `tests/unit/test_guard_runner_env.py` |
| 5 | GW-501 | HIGH | Runner allowlist matched by basename: `git submodule foreach ./test` deferred | Exact bare-name match; any path component or `PATH` assignment asks (`eb32390`) | `tests/unit/test_guard_runner_paths_redirs.py` (real submodule) |
| 5 | GW-502 | MEDIUM | `> 2` / `> -` mistaken for descriptor duplication | Real fd-vs-file redirection semantics (`eb32390`) | same (real shell) |
| 6 | (none) | | No Critical or High issue found | | |

Items surfaced by the project's own audits while fixing the above: `index_state['head']` read back from `warp.db` reached Git unvalidated (`5d29e5c`, `tests/security/test_storage_tampered_index.py`); `commits`/`temporal` output was unbounded at scale (`0e7527e`); friction fixes so that harmless `GIT_DIFF_OPTS=-u<N>`, `GIT_ATTR_NOSYSTEM` and similar pure-data variables do not prompt.

## Documented residual limitations (reviewed, accepted for v0.1.0)

The guard is a safety net, not a sandbox (`docs/guard-limitations.md`): it does not read config files, aliases, hooks or the ambient environment, assumes a bare utility name in a `foreach`/`bisect run` resolves through the user's normal `PATH`, does not model tilde forms, zsh `=cmd` or zsh numeric ranges, uses a static built-in subcommand list, and prompts (by design) on any unresolved variable in a destructive subcommand. Windows is unsupported.

## Round 1 report (verbatim)

# Convergence review

## Summary (5-10 lines)

Reviewed `origin/warp/phoenix..HEAD` and traced the runner, classifier, storage, hooks, plugin permissions, tests, and documentation.
No Critical findings. I found three High issues: repository filter enumeration can silently truncate, unresolved shell variables can hide destructive options, and a read-only skill permits `git diff` to overwrite files.
I also found a recorder compaction race that can lose concurrent entries.
The focused unit, security, and integration run had 2,572 passes and five failures caused by this environment denying Unix-domain socket binding.
No tracked files were changed.

## Findings

### [HIGH] GW-SEC-01 — Truncated filter enumeration can leave repository filters executable

**Evidence:** [`git.py`](/tmp/gw-codex-review/scripts/gitwarp/core/git.py:290) captures filter configuration with a 1 MiB limit but ignores `_exec`’s `truncated` flag. A reproduction using a temporary repository with 24,000 filter definitions returned overrides for earlier filters but omitted a final `zz-review-evil` filter (`evil filter disabled: False`). The runner then continues to parse that partial list and run the requested command.

**Impact:** If a worktree path uses the omitted filter, a Git Warp `status` or `diff` call can run its repository-configured filter command. This defeats the runner’s stated filter suppression boundary and can execute repository-supplied code.

**Suggested fix:** Treat truncated or incomplete enumeration as unsafe. Refuse the Git operation, or apply a strategy that guarantees every repository-scoped filter is disabled before running it. Add a regression case that exceeds the enumeration cap.

### [HIGH] GW-GUARD-01 — Unresolved variables can hide destructive options

**Evidence:** [`classifier.py`](/tmp/gw-codex-review/scripts/gitwarp/safety/classifier.py:542) considers an unassigned bare variable non-risky. Although [`Opts`](/tmp/gw-codex-review/scripts/gitwarp/safety/classifier.py:362) records it in an option-capable position, the dynamic escalation at lines 669–671 relies on that risk check. Direct reproduction: `git push $FLAGS origin main`, `git branch $FLAGS old`, and `git tag $FLAGS v1` all classify as `defer`. With inherited `FLAGS=--force`, `FLAGS=-D`, or `FLAGS=-d`, respectively, those commands become destructive.

**Impact:** A shell variable available at execution time can conceal a force push or deletion from the Guardian, producing a silent DEFER where the policy says uncertain destructive behavior should ASK.

**Suggested fix:** Treat unresolved variables in option-capable positions of destructive-capable Git commands as ASK unless the classifier can prove their value harmless. Add coverage for push, branch, tag, fetch, and similar handlers, including inherited variables.

### [HIGH] GW-PLUGIN-01 — The read-only X-Ray skill pre-approves file-overwriting `git diff`

**Evidence:** [`git-xray/SKILL.md`](/tmp/gw-codex-review/skills/git-xray/SKILL.md:5) grants `Bash(git diff:*)`. The classifier does not escalate `git diff --output=/path`, and the limitations doc acknowledges this at [`guard-limitations.md`](/tmp/gw-codex-review/docs/guard-limitations.md:44). Git’s `--output` option can create or truncate the specified file.

**Impact:** While this skill is active, its broad command prefix admits a file-writing form of a command presented as read-only. A command such as `git diff --output=/important/file` can overwrite a file without a Guardian ASK.

**Suggested fix:** Classify `git diff/log/show --output` as at least ASK and add a regression test. Review the other broad read-command prefixes for options that write files or run helpers.

### [MEDIUM] GW-STATE-01 — Recorder compaction releases its lock before replacing the file

**Evidence:** [`storage.py`](/tmp/gw-codex-review/scripts/gitwarp/core/storage.py:384) computes the transformed recorder contents while holding the append lock, then closes the locked descriptor at line 386 before calling `write_atomic` at line 388.

**Impact:** A concurrent recorder append can complete after the compaction snapshot and before the replacement. The replacement then writes the older snapshot, losing that new entry. Existing concurrency tests exercise appends and rotation, but do not cover an append racing with compaction.

**Suggested fix:** Keep the lock held through the atomic replacement, or use a generation/check-and-retry approach that detects appends made after the snapshot. Add a deterministic race test.

## Things verified OK (short bullets)

- The normal Git runner scrubs inherited `GIT_*` variables, limits subcommands, and kills the process group on timeout or output overflow.
- Revision inputs use `--end-of-options` and normalize accepted revisions to full object IDs before later use.
- The config merge keeps the built-in protected-branch floor; repository policy cannot remove it.
- I found no second Git subprocess boundary outside `core/git.py`; revision parsing is centralized in `core/revisions.py`.
- The baseline audit accounts for the renamed DEFER cases and stricter malformed-hook expectations; the documented changes are not unexplained test loosening.

## What you could not verify

- Five socket-fixture tests could not run to completion because the environment returned `PermissionError` for Unix-domain socket binding. The focused run otherwise reported 2,572 passes.
- I did not run the separate acceptance, crash, or scale suites, or verify hook behavior inside a live Claude Code host.


## Round 2 report (verbatim)

One new HIGH finding remains; there are no CRITICAL findings.

## Verdict on the four round-1 findings

| ID | CLOSED / STILL OPEN | evidence |
|---|---|---|
| GW-SEC-01 | CLOSED | I added repository filters through both an included config file and worktree-scoped config. `status` completed without running either filter helper. The filter security tests also passed. |
| GW-GUARD-01 | STILL OPEN | `git push origin "$BRANCH"` classifies as `defer`. With `BRANCH=:dev`, running that command against a temporary bare remote deleted its `dev` branch. The new option-position checks do not cover dangerous refspec values after the remote operand. |
| GW-PLUGIN-01 | CLOSED | The new classifier returns `ask` for `git diff --output` and tested abbreviations, including `--outp`. The related execution-helper gap is recorded below as a separate finding. |
| GW-STATE-01 | STILL OPEN | If a rewrite holds the lock longer than 10 seconds, an appender proceeds without acquiring it. In a reproduction, the append returned after 10 seconds, then the rewrite replaced the file; the appended record was lost. |

## New findings

### [HIGH] GW-GUARD-02 — Git helper execution options remain DEFER

**Evidence:** The classifier returns `defer` for `git diff --ext-diff`, `git fetch --upload-pack=/path/to/helper origin`, and `git push --receive-pack=/path/to/helper origin main`. I configured an external diff helper in a temporary repository; `git diff --ext-diff` ran it. I also used an upload-pack wrapper during a local fetch; the wrapper ran. The `git diff:*` prefix is pre-approved by several skills.

**Impact:** These commands can run helper programs while the guard treats them as safe. A crafted command can therefore execute code through a Git option that looks like a read or fetch operation.

**Suggested fix:** Add ASK coverage for execution-bearing options such as `--ext-diff`, `--textconv`, fetch `--upload-pack`, and push `--receive-pack`/`--exec`. Cover corresponding executable config keys such as `remote.<name>.uploadpack` and `remote.<name>.receivepack`.

## Regressions / false-positive risks

- Filter enumeration now refuses repositories with more than 1,000 repository-scoped filter names or over 8 MiB of matching config output. That is an intentional fail-closed limit, but could block unusually large repositories. A few filters, included filters, worktree filters, and the environment’s global LFS filters did not cause refusal.
- The storage lock wait is bounded at 10 seconds, but timeout does not preserve the locking guarantee: appends proceed unlocked, and `update_locked` also ignores the lock result. This can lose records or state updates under prolonged contention.
- Ordinary `git diff`, `git log`, and tested output-indicator/order options remained `defer`; I did not find an ASK false positive in those probes.

## What you could not verify

- I did not validate hook decisions inside a live Claude Code host.
- The focused test run reported 225 passed and one failure: the socket fixture could not bind because this environment denied Unix-domain socket creation. I did not run the full suite.
- I did not specifically probe submodule-heavy repositories or state-file migration behavior.

## Round 3 report (verbatim)

## Verdict

| ID | CLOSED / STILL OPEN | evidence |
|---|---|---|
| GW-GUARD-01 | STILL OPEN | `git clean -*` classified `defer`. In a temporary repo with untracked files named `-f` and `victim`, shell expansion turned it into `git clean -f` and deleted both. |
| GW-GUARD-02 | STILL OPEN | `GIT_CONFIG_PARAMETERS="'core.fsmonitor=…'" git status` classified `defer`; Git executed the configured helper. `git config --edit` also classified `defer` while launching the configured editor. |
| GW-STATE-01 | CLOSED | In an independent two-process reproduction, a held append lock caused `LockTimeout`; the log bytes stayed unchanged. The focused suite also passed: 350 tests. |

## New findings

### [HIGH] GW-GUARD-01 — Shell expansion can synthesize a destructive flag

**Evidence:** `git clean -*` returned `defer` from `classify_command`. In a temporary repository, I created untracked `-f` and `victim` files and ran that command through a shell. The glob expanded to `-f`; Git deleted both files. Brace expansion such as `git clean {,-f}` also classified `defer`.

**Impact:** A command that looks like an unresolved path pattern can expand into an option that enables destructive behavior. This bypasses the guard’s uncertainty check and can permanently delete untracked files.

**Suggested fix:** On destructive-capable Git commands, treat unquoted glob and brace expansions in option-capable positions as uncertain and ask. Do not assume an expansion stays a path operand.

### [HIGH] GW-GUARD-02 — Git helper execution still has unguarded paths

**Evidence:** The classifier returned `defer` for `GIT_CONFIG_PARAMETERS="'core.fsmonitor=/tmp/helper'" git status`. In a temporary repository, Git ran the injected fsmonitor script. It also returned `defer` for `git config --edit` and `git config -e`; with `GIT_EDITOR` set to a marker script, `git config --edit` executed it. `git bisect run /tmp/helper` also classifies `defer`.

**Impact:** These commands can run programs with the user’s privileges without a Guardian prompt, despite the new execution-bearing checks.

**Suggested fix:** Detect `GIT_CONFIG_PARAMETERS` config injection, treat `git config --edit` as editor execution, and ask for runner subcommands such as `git bisect run` before inspecting any nested command.

No Critical finding was demonstrated.

## Regressions / false-positive risks

The broader variable rule intentionally prompts for ordinary commands such as `git push origin "$BRANCH"` when the value is unresolved. That is safer for deletion refspecs, but frequent prompts could encourage users to approve without reviewing. The focused tests confirm this behavior is intentional.

The targeted suite passed, including plain read-only command cases and the new lock-timeout cases. I found no additional false positives in those cases. Lock waits are bounded; the tested append path refused after timeout without changing existing data.

## What you could not verify

I did not run the live Claude Code hook or acceptance workflow, and this clone only documents macOS as tested. The test run covered the focused classifier and storage files, not the full suite. No tracked files were changed.

## Round 4 report (verbatim)

## Verdict

| class | CLOSED / STILL OPEN | evidence |
|---|---|---|
| Shell glob/brace expansion synthesising destructive flags | **STILL OPEN** | In a temporary repo with untracked files named `-f`, `-d`, and `victim`, zsh with `extendedglob` expands `git clean -(f|d)` to `git clean -f -d`; real Git deletes all three. The classifier returns `defer` for that same command. |
| Program-running paths | **STILL OPEN** | The classifier returns `defer` for `git bisect run cp /tmp/payload .git/config` and `git submodule foreach cp /tmp/payload .git/config`. Both paths execute `cp`; the runner exception treats it as harmless. |

## New findings

### [HIGH] GW-GLOB-02 — zsh extended glob can synthesize `git clean -f`

**Evidence:** With `extendedglob` enabled, zsh expands `-(f|d)` to `-f -d` when files named `-f` and `-d` exist. In a temporary Git repo, `git clean -(f|d)` then removed those files and an unrelated untracked `victim`. Yet `python3 scripts/warp.py guard check 'git clean -(f|d)' --branch feature/x` returned `defer` and recorded the command as `git clean -`. The current glob check only handles recognized glob words such as `-*` and `{,-f}`.

**Impact:** This bypasses the guard for a destructive `git clean -f` through shell expansion.

**Suggested fix:** Recognize zsh extended glob syntax that can expand into option words, or conservatively ask when an unquoted word in a destructive-capable command uses unsupported shell pattern syntax. Add a regression using zsh with `extendedglob`.

### [HIGH] GW-EXEC-02 — Runner exception defers file-writing programs

**Evidence:** `git bisect run cp /tmp/payload .git/config` and `git submodule foreach cp /tmp/payload .git/config` both classify as `defer`. `_runs_program` exempts every command in `_TEXT_TOOLS`; that set includes `cp`, `mv`, `touch`, and `tee`. Git executes the command supplied to `bisect run` or `submodule foreach`, so these exceptions include commands that write files.

**Impact:** A command classified as `defer` can overwrite repository configuration or other files while Git runs it. A copied hook or helper can also affect later Git operations.

**Suggested fix:** Use a dedicated allowlist of genuinely read-only commands for runner exceptions. Treat file-writing utilities as execution-bearing and return `ask`.

## Regressions / false-positive risks

The new default-ask rule also prompts for ordinary read-only commands with some benign Git environment variables. For example, `GIT_DIFF_OPTS=--unified=10 git diff` and `GIT_ATTR_NOSYSTEM=1 git diff` both return `ask` with `exec-option`. This follows the stated broad environment policy, but may prompt users with these workflows frequently.

## What you could not verify

The focused pytest files could not run because `pytest` is not installed (`No module named pytest`). The installed Git is 2.50.1, so I could not verify the documentation drift checks against Git 2.53. The review left the clone unchanged.

## Round 5 report (verbatim)

## Verdict

| class | CLOSED / STILL OPEN | evidence |
|---|---|---|
| zsh pattern words synthesizing destructive flags | CLOSED | With files named `-d`, `-f`, and `-z`, zsh `extendedglob` expands `git clean -^z` to `-d -f`. The guard returns `ask`; real Git removes the temporary files. |
| Strict read-only runner exemptions | STILL OPEN | `git submodule foreach ./test` and `git submodule foreach 'echo damage > 2'` both classify `defer`; real Git runs the tracked script and overwrites the file. See GW-501 and GW-502. |
| New pure-data environment exemptions | CLOSED, with a false-positive risk | The added allowlist cases passed. `GIT_DIFF_OPTS=-u10000` is harmless to real Git but still asks; see regressions below. |

## New findings

### [HIGH] GW-501 — Runner allowlist accepts executable paths by basename

**Evidence:** `RUNNER_SAFE` checks `posixpath.basename(head.text)` in [classifier.py](/tmp/gw-codex-review/scripts/gitwarp/safety/classifier.py:1339). In a real temporary submodule, `git submodule foreach ./test` classified `defer` and executed the tracked `./test` script, which created a marker file.

**Impact:** A repo-controlled executable with a name on the safe list can run with the user’s privileges, despite the runner exemption being intended for read-only commands.

**Suggested fix:** Reject command names containing a path component. Also ensure bare external utilities cannot resolve through a caller-controlled `PATH` to attacker-chosen executables.

### [MEDIUM] GW-502 — Numeric output redirections are mistaken for file descriptors

**Evidence:** `_FD_TARGET` accepts numeric strings and `-` without retaining the redirection operator context ([classifier.py](/tmp/gw-codex-review/scripts/gitwarp/safety/classifier.py:1329), [classifier.py](/tmp/gw-codex-review/scripts/gitwarp/safety/classifier.py:1337)). Real `git submodule foreach 'echo damage > 2'` and `git submodule foreach 'echo damage > -'` both classified `defer` and overwrote those files in the submodule.

**Impact:** A runner command can silently truncate a matching user file.

**Suggested fix:** Preserve the redirection operator and allow only actual descriptor duplication/closure forms; treat `>2` and `>-` as file writes.

## Regressions / false-positive risks

`GIT_DIFF_OPTS` accepts only values with up to four digits in [classifier.py](/tmp/gw-codex-review/scripts/gitwarp/safety/classifier.py:888). `GIT_DIFF_OPTS=-u10000 git diff` succeeds with real Git but the guard returns `ask`. This is a low-severity false positive; consider allowing a broader valid numeric range.

The changed unit tests passed: **163 passed**. I found no other confirmed regression in the new environment exemptions.

## What you could not verify

The full suite did not finish: I interrupted it after 307 tests passed over 205.78 seconds. I did not verify the plugin in a live Claude Code session.

## Round 6 report (verbatim)

## Verdict

| class | CLOSED / STILL OPEN | evidence |
|---|---|---|
| Path-qualified runner heads | CLOSED | The focused tests classify `./test`, absolute paths, and other path-qualified heads as ASK. A real Git `submodule foreach` run executed a tracked `./test` script; the guard now asks for that command. |
| PATH assignments in the command | CLOSED | Prefix, standalone, exported, and `env` PATH assignments are covered by passing focused tests and classify as ASK. |
| Runner redirection targets | CLOSED | The guard asks for file-writing targets, including `> 2`, `> -`, `>&file`, and `>|file`. It defers descriptor duplication and closure. Real Bash checks agree on which forms create files. |
| `GIT_DIFF_OPTS` digit runs | CLOSED | Focused tests confirm arbitrary-length numeric forms defer and malformed or risky values ask. |
| zsh/bash pattern handling | CLOSED | The earlier extglob regression suite passes; the round-6 changes did not reopen that behavior. |
| Other HIGH/CRITICAL issues in the diff | NONE FOUND | I found no demonstrated command-string bypass meeting the stated severity bar. |

## New findings

No new findings. **No Critical or High issue found.**

The remaining bare-name runner resolution depends on the user’s ambient `PATH`, which is state outside the command string and is already documented as a limitation.

## Regressions / false-positive risks

No material regression found in the reviewed changes. The runner allowlist is narrower, and PATH changes in the same command now prompt; those extra prompts are consistent with the guard’s safety policy.

The three focused suites—patterns, runner environment, and runner paths/redirections—passed: **285 passed**. The full unit suite had **2,770 passed and 2 failed**. Both failures were unrelated socket tests: this sandbox rejects Unix-domain socket binds with `PermissionError`.

The worktree remained clean.

## What you could not verify

Real-shell redirection checks used macOS Bash 3.2.57; the real Git runner reproduction used Apple Git 2.50.1. I did not verify behavior across Linux shell/Git versions or ambient PATH and Git configuration states.
