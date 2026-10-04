# Independent convergence review (Codex)

- **Reviewer:** Codex CLI 0.160.0 (`codex exec`, workspace-write sandbox confined to a disposable clone; it modified no tracked file).
- **Scope:** `origin/warp/phoenix..warp/convergence` at `5edaeef` (84 files). Prompt: security, Git safety, state hardening, Guardian bypasses, Git environment/config, revision normalization, privacy, architecture, plugin correctness, tests and documentation.
- **Result:** 0 Critical, 3 High, 1 Medium, 0 Low, 0 Uncertain.
- **Not verified by the reviewer:** five Unix-socket fixture tests (its sandbox denied socket binding), the acceptance/crash/scale suites, behaviour inside a live Claude Code host. Those are covered by the project's own runs (`planning/LIVE_ACCEPTANCE.md`, the native suite).

## Resolution of the round-1 findings

| ID | Severity | Resolution | Commit | Regression tests |
|---|---|---|---|---|
| GW-SEC-01 | HIGH | **Resolved.** Repository-filter enumeration now fails closed (`GitRefused`, reasons `filter_enumeration_incomplete` / `too_many_filters`) for truncated, failed, timed-out, unparseable or over-limit enumerations; capture cap raised to 8 MiB; names are de-duplicated case-sensitively. No other truncation-dependent security decision exists in `core/git.py`. | `ea367a1` | `tests/security/test_git_env_config_filters.py` (30,000-filter repo with the evil filter last, lowered cap, timeout, OS error, odd names) |
| GW-GUARD-01 | HIGH | **Resolved.** Unresolved variables in a destructive-capable subcommand ASK (`dynamic-argument`): any unquoted `$V`/`${V}`, and a quoted `"$V"` before the first operand. A quoted variable *after* an operand still defers (documented tradeoff, `docs/guard-limitations.md`). One baseline expectation tightened (`git reset --soft $X` is now ASK because the last mode flag wins in real Git). | `dea510c` | `tests/unit/test_guard_unresolved_vars_output.py` (184 cases) |
| GW-PLUGIN-01 | HIGH | **Resolved.** File-writing options (`--output` and its abbreviations on diff/log/show/blame/rev-list/format-patch/…, `format-patch -o`, `archive -o`, `bundle create`, `fast-export --export-marks`, `grep -O`) ASK under rule `output-file`; program-running `-c` keys ASK under `config-exec`. Skills keep scoped read-only prefixes; the live TUI run shows Guardian's ASK surfaces even when a skill pre-approves the Bash command. | `dea510c` | same file; `planning/LIVE_ACCEPTANCE.md` LIVE-006 |
| GW-STATE-01 | MEDIUM | **Resolved.** Every mutator of the recorder (append, rotation, compaction) takes a stable `<name>.lock` file held across read, transform, atomic replace; appenders can no longer land between the snapshot and the replace. | `e0218cf` | `tests/unit/test_storage_compaction_race.py` (deterministic race, 4-append race, 8-appender + 2-compactor stress, rotation + compaction stress) |

Two further items surfaced by the project's own audit while fixing the above: `index_state['head']` read back from `warp.db` reached Git without validation (fixed, `5d29e5c`, `tests/security/test_storage_tampered_index.py`), and `commits`/`temporal` output was unbounded at scale (fixed, `0e7527e`).

## Reviewer's report (verbatim, round 1)

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
