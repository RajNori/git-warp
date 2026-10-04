# Disposable neutral-corpus result for warp/convergence

The unchanged Disposable corpus, run against `warp/convergence`, compared with the two frozen candidates.

- Convergence candidate: `warp/convergence` @ `1b25abe05c731b399ce4cb323e964f05e12ceea0`
- Phoenix `warp/phoenix` @ `d909d6d4f739a7f33dcb543f4bea6b383f244a98`; Rebirth `warp/rebirth` @ `3d4a5aca98b14810dc785b75c6bfb09d4baaf663`
- Harness `RajNori/Disposable` branch `bakeoff-harness`, HEAD `69d22f845145188b4c59adf03a364960f1618b7f` (local branch; the Disposable repository itself is not part of this release)
- Darwin 25.6.0 (26.6.2); Python 3.13.2; git version 2.53.0; Claude Code 2.1.285 (Claude Code)

## Totals

| result | phoenix | rebirth | convergence |
|---|---|---|---|
| PASS | 277 | 269 | 291 |
| FAIL | 13 | 10 | 0 |
| BLOCK | 62 | 34 | 67 |
| ASK | 28 | 46 | 47 |
| ALLOW | 72 | 82 | 48 |
| ERROR | 1 | 1 | 0 |
| TIMEOUT | 0 | 0 | 0 |
| UNSUPPORTED | 1 | 3 | 1 |
| UNREACHABLE | 0 | 9 | 0 |
| UNVERIFIED | 3 | 3 | 3 |
| **cases** | 457 | 457 | 457 |

Contract verdicts:

| verdict | phoenix | rebirth | convergence |
|---|---|---|---|
| PASS | 335 | 321 | 361 |
| FAIL | 25 | 38 | 0 |
| no contract | 97 | 98 | 96 |

## Convergence cases with a contract FAIL / ERROR / TIMEOUT: **0**

_none_

## Baseline failures closed by convergence: **65**; still open: **0**

| baseline | case | baseline result/contract | convergence result/contract | title |
|---|---|---|---|---|
| phoenix | GW-CONFIG-001 | FAIL/FAIL | PASS/PASS | hostile config: fsmonitor |
| phoenix | GW-CONFIG-003 | FAIL/FAIL | PASS/PASS | hostile config: textconv |
| phoenix | GW-CONFIG-011 | ALLOW/FAIL | ASK/PASS | aliases: `git x` where alias.x = reset --hard (repo config) |
| phoenix | GW-ENV-001 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_DIR |
| phoenix | GW-ENV-002 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_WORK_TREE |
| phoenix | GW-ENV-004 | ERROR/- | PASS/PASS | hostile environment: GIT_OBJECT_DIRECTORY |
| phoenix | GW-ENV-010 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_CONFIG_GLOBAL |
| phoenix | GW-ENV-011 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_CONFIG_SYSTEM |
| phoenix | GW-ENV-012 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_CONFIG_COUNT |
| phoenix | GW-GUARD-003 | ALLOW/FAIL | ASK/PASS | guard: 'git reset $(echo --hard)' |
| phoenix | GW-GUARD-007 | ALLOW/FAIL | BLOCK/PASS | guard: 'cmd="git reset --hard"; $cmd' |
| phoenix | GW-GUARD-049 | ALLOW/FAIL | ASK/PASS | guard: "bash <(echo 'git reset --hard')" |
| phoenix | GW-GUARD-050 | ALLOW/FAIL | BLOCK/PASS | guard: "source /dev/stdin <<< 'git reset --hard'" |
| phoenix | GW-GUARD-076 | ALLOW/FAIL | BLOCK/PASS | guard: 'git fetch --force origin main:main' |
| phoenix | GW-GUARD-077 | ALLOW/FAIL | BLOCK/PASS | guard: 'git fetch origin +refs/heads/*:refs/heads/*' |
| phoenix | GW-HOOK-018 | ALLOW/FAIL | ASK/PASS | hook guard: empty-stdin |
| phoenix | GW-HOOK-020 | ALLOW/FAIL | ASK/PASS | hook guard: malformed-json |
| phoenix | GW-HOOK-021 | ALLOW/FAIL | ASK/PASS | hook guard: json-array |
| phoenix | GW-HOOK-024 | ALLOW/FAIL | ASK/PASS | hook guard: command-not-string |
| phoenix | GW-HOOK-025 | ALLOW/FAIL | ASK/PASS | hook guard: bash-no-tool-input |
| phoenix | GW-PRIVACY-001 | FAIL/FAIL | PASS/PASS | synthetic secrets are not persisted in state files / SQLite |
| phoenix | GW-PRIVACY-002 | FAIL/FAIL | PASS/PASS | synthetic secrets are not echoed by hooks or CLI output |
| phoenix | GW-PRIVACY-004 | FAIL/FAIL | PASS/PASS | state directory / SQLite / recorder permissions under umask  |
| phoenix | GW-PRIVACY-005 | FAIL/FAIL | PASS/PASS | symlinked state directory is not followed |
| phoenix | GW-PRIVACY-006 | FAIL/FAIL | PASS/PASS | symlinked state file is not written through |
| phoenix | GW-PRIVACY-007 | FAIL/FAIL | PASS/PASS | FIFO in place of a state file does not hang the hook |
| rebirth | GW-CONFIG-001 | FAIL/FAIL | PASS/PASS | hostile config: fsmonitor |
| rebirth | GW-CONFIG-009 | FAIL/FAIL | PASS/PASS | hostile config: hookspath |
| rebirth | GW-ENV-001 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_DIR |
| rebirth | GW-ENV-002 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_WORK_TREE |
| rebirth | GW-ENV-004 | ERROR/- | PASS/PASS | hostile environment: GIT_OBJECT_DIRECTORY |
| rebirth | GW-ENV-010 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_CONFIG_GLOBAL |
| rebirth | GW-ENV-011 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_CONFIG_SYSTEM |
| rebirth | GW-ENV-012 | FAIL/FAIL | PASS/PASS | hostile environment: GIT_CONFIG_COUNT |
| rebirth | GW-GUARD-002 | ALLOW/FAIL | BLOCK/PASS | guard: 'git reset --har' |
| rebirth | GW-GUARD-011 | ALLOW/FAIL | BLOCK/PASS | guard: 'git checkout .' |
| rebirth | GW-GUARD-013 | ALLOW/FAIL | ASK/PASS | guard: 'git checkout -B main' |
| rebirth | GW-GUARD-014 | ALLOW/FAIL | ASK/PASS | guard: 'git switch -C main' |
| rebirth | GW-GUARD-018 | ALLOW/FAIL | ASK/PASS | guard: 'git branch -f main HEAD' |
| rebirth | GW-GUARD-022 | ALLOW/FAIL | BLOCK/PASS | guard: 'git push -fu origin main' |
| rebirth | GW-GUARD-024 | ALLOW/FAIL | BLOCK/PASS | guard: 'git push origin :main' |
| rebirth | GW-GUARD-026 | ALLOW/FAIL | BLOCK/PASS | guard: 'git push --mirror' |
| rebirth | GW-GUARD-031 | ALLOW/FAIL | BLOCK/PASS | guard: "git submodule foreach 'git reset --hard'" |
| rebirth | GW-GUARD-043 | ALLOW/FAIL | BLOCK/PASS | guard: "echo 'git reset --hard' \| sh" |
| rebirth | GW-GUARD-044 | ALLOW/FAIL | BLOCK/PASS | guard: "printf '%s' 'git reset --hard' \| bash" |
| rebirth | GW-GUARD-046 | ALLOW/FAIL | BLOCK/PASS | guard: "eval 'git reset --hard'" |
| rebirth | GW-GUARD-048 | ALLOW/FAIL | BLOCK/PASS | guard: "bash <<< 'git reset --hard'" |
| rebirth | GW-GUARD-050 | ALLOW/FAIL | BLOCK/PASS | guard: "source /dev/stdin <<< 'git reset --hard'" |
| rebirth | GW-GUARD-060 | ALLOW/FAIL | BLOCK/PASS | guard: 'git stash clear' |
| rebirth | GW-GUARD-063 | ALLOW/FAIL | BLOCK/PASS | guard: 'git prune' |
| rebirth | GW-GUARD-064 | ALLOW/FAIL | BLOCK/PASS | guard: 'git reflog delete HEAD@{0}' |
| rebirth | GW-GUARD-066 | ALLOW/FAIL | BLOCK/PASS | guard: 'git rm -rf .' |
| rebirth | GW-GUARD-067 | ALLOW/FAIL | ASK/PASS | guard: 'git worktree remove --force x' |
| rebirth | GW-GUARD-068 | ALLOW/FAIL | BLOCK/PASS | guard: 'git push -uf origin main' |
| rebirth | GW-GUARD-070 | ALLOW/FAIL | BLOCK/PASS | guard: 'git checkout -f' |
| rebirth | GW-GUARD-071 | ALLOW/FAIL | BLOCK/PASS | guard: 'git switch --discard-changes main' |
| rebirth | GW-GUARD-073 | ALLOW/FAIL | ASK/PASS | guard: 'git tag -d v1' |
| rebirth | GW-GUARD-074 | ALLOW/FAIL | ASK/PASS | guard: 'git branch -M main' |
| rebirth | GW-GUARD-076 | ALLOW/FAIL | BLOCK/PASS | guard: 'git fetch --force origin main:main' |
| rebirth | GW-GUARD-077 | ALLOW/FAIL | BLOCK/PASS | guard: 'git fetch origin +refs/heads/*:refs/heads/*' |
| rebirth | GW-PERF-006 | ALLOW/FAIL | BLOCK/PASS | guard stress: nested sh -c depth 7 |
| rebirth | GW-PERF-007 | ALLOW/FAIL | BLOCK/PASS | guard stress: nested sh -c depth 8 |
| rebirth | GW-PRIVACY-001 | FAIL/FAIL | PASS/PASS | synthetic secrets are not persisted in state files / SQLite |
| rebirth | GW-PRIVACY-002 | FAIL/FAIL | PASS/PASS | synthetic secrets are not echoed by hooks or CLI output |
| rebirth | GW-STATIC-002 | FAIL/FAIL | PASS/PASS | skills must not pre-approve unscoped Bash |

## UNVERIFIED / UNSUPPORTED in convergence (unchanged categories)

- `GW-CONFIG-004` UNVERIFIED: control: plain git did not fire the pager_core marker (pty=False); vector ineffective here
- `GW-PRIVACY-009` UNVERIFIED: atomic replacement / crash-consistency not behaviourally tested; static hints only
- `GW-RECOVERY-008` UNSUPPORTED: superseded by GW-RECOVERY-009. CORRECTION: this case originally claimed neither candidate has a preserve command; that was inaccurate for Phoenix (`wa
- `GW-STATIC-001` UNVERIFIED: verbs appearing in git argv (manual check needed; many are read-only sub-forms like `branch --list`): ['branch', 'config', 'reflog', 'stash', 'worktre

## Repository mutation

Cases with `mutated_repo`: 0.
