# Scale and crash measurements (QA, Phase 12 / 13)

Measured on one laptop (macOS 25.6 / Darwin, Python 3.13.2, git 2.x, local SSD), against `warp/convergence` @ 4f824b7
(pre-fix code: other agents' fixes had not landed). These are measurements of one **synthetic** repository, not a
claim about any particular real repository or about "every monorepo". Re-run with
`python3 -m pytest tests/scale -q -s` (the table is printed and written to `scale-results.md` in the pytest tmp dir).

## Phase 13 - scale (`tests/scale/test_scale.py`)

Repository: 6000 linear commits (built by `git fast-import` in 0.7 s), 400 distinct tracked files touched across history
(320 `src/modNN.py` + 80 `docs/dNN.md`), working tree with **400 modified** tracked files and **8000 untracked** files
(80 directories x 100). Disposable's bake-off ceiling was ~1500 commits / ~5300 files.

Bounds: hooks use their `hooks/hooks.json` timeout (SessionStart 15 s, PreToolUse 10 s, PostToolUse 10 s, Stop 20 s);
`warp.py` commands have no declared timeout, so the test uses a 120 s ceiling. Each run is sequential (no contention) under
a supervisor reporting wall time and the peak RSS of the largest process in the tree (`resource.getrusage(RUSAGE_CHILDREN)`).

| operation | bound s | time s | peak RSS MB | stdout bytes | degradation signals |
|---|---:|---:|---:|---:|---|
| hook SessionStart | 15 | 0.27 | 28 | 997 | - |
| hook PreToolUse (guard) | 10 | 0.07 | 24 | 2 | - |
| hook PostToolUse (Bash) | 10 | 0.10 | 21 | 2 | - |
| hook PostToolUse (Write) | 10 | 0.10 | 21 | 2 | - |
| hook Stop | 20 | 0.22 | 28 | 569 | - |
| `guard check` | 120 | 0.09 | 24 | 182 | - |
| `xray` | 120 | 0.32 | 31 | 12087 | `working_tree.unstaged.truncated=true` (list capped, flagged); 3 warnings |
| `pr --base=HEAD~500` | 120 | 0.22 | 25 | 49736 | `files.truncated=true`, `not_included.unstaged.truncated=true`, `tags.docs.truncated=true` |
| `pr --base=HEAD` | 120 | 0.14 | 23 | 2496 | `not_included.unstaged.truncated=true` |
| `blast` | 120 | 0.26 | 28 | 137138 | `mode=working-tree`; 1 warning |
| `commits` | 120 | 0.70 | 42 | **2926108** | `mode=working-tree` (no truncation flag; output exceeds the 2 MiB ceiling asserted by the test) |
| `conflict` | 120 | 0.10 | 22 | 409 | - |
| `temporal` | 120 | 4.86 | 104 | 241011 | `mode=working-tree`, `probes.timed_out=false` |
| `rescue scan` | 120 | 0.28 | 106 | 1492 | `fsck.timed_out=false`, `truncated=false` |
| `rescue scan --no-fsck` | 120 | 0.15 | 25 | 1591 | 1 warning |
| `rescue inspect HEAD` | 120 | 0.16 | 25 | 1740 | - |
| `rescue preserve --dry-run` | 120 | 0.10 | 25 | 665 | - |
| `archaeology src/mod7.py` | 120 | 0.32 | 87 | 11938 | `truncated=false` |
| `archaeology --question` | 120 | 0.15 | 25 | 594 | 1 warning |
| `bisect plan` (4000 commits span) | 120 | 0.20 | 25 | 3861 | 1 warning |
| `bisect status` | 120 | 0.10 | 25 | 405 | - |
| `memory index` (6000 commits, cold) | 120 | 0.42 | 33 | 577 | `index.complete=true` |
| `memory status / hotspots / churn / cochange / introduced / authors / reverts / sessions` | 120 | 0.09-0.15 | 25-28 | 610-4720 | `index.complete=true` |

Summary of what was observed:

* No timeout, no traceback, no crash; every hook exits 0 well inside its hooks.json budget (slowest: SessionStart 0.27 s).
  The slowest command was `temporal` at 4.9 s (peak RSS 104 MB); the largest peak RSS was `rescue scan` (106 MB, runs `git fsck`).
* Truncation is flagged (`truncated: true`) where lists are capped (`xray`, `pr`); the index reported `complete: true`
  for all 6000 commits.
* SessionStart context stays ~1 KB regardless of the 8400 dirty paths.
* **Finding:** `warp.py commits` in working-tree mode emitted 2.9 MB of JSON for 400 modified files, with no truncation
  flag; its output scales with the size of the change and exceeds the 2 MiB output ceiling asserted in
  `test_output_and_memory_are_bounded[cli:commits]` (that test fails until the output is bounded and flagged).
* The whole scale file runs in ~12 s; the guard/payload stress file (`tests/scale/test_guard_and_payload_stress.py`) in ~3 s.

## Phase 12 - crash and concurrency (`tests/crash/`)

| scenario | observation |
|---|---|
| 16 parallel PostToolUse (x48 runs) | wall 0.50 s, slowest hook 0.27 s, all exit 0, 73 recorder lines present, 0 torn lines |
| 8 parallel PostToolUse (x24 runs) | wall 0.28 s, slowest hook 0.11 s, all exit 0 |
| 16 mixed hooks (8 PostToolUse + 4 SessionStart + 4 Stop) | wall 0.42 s; slowest SessionStart 0.41 s; `state.json` never observed torn by a polling reader |
| 16 writers across recorder rotation (file at 5 MiB) | one rotation; every old + new record present across `.jsonl` + `.jsonl.1` |
| 8 simultaneous `memory index` on a fresh state dir (10 rounds) | all runs exit 0 and the final index is complete (400/400) and passes `PRAGMA integrity_check`, **but a healthy database was moved to `warp.db.corrupt` in 3 of 10 rounds** (first-creation race treated as corruption) |
| SIGKILL storm (24 hooks killed at random moments, 3 seeds) | `state.json` valid, <= 1 torn recorder line, db passes integrity check, full recovery on the next run |
| crash before `os.replace` of `state.json` / recorder rotation / compaction | previous complete file intact, no torn data, next run normal |
| DB: garbage / truncated / header-only / future schema / foreign DB | exit 0, ~0.1 s, original bytes preserved as `warp.db.corrupt`, usable index afterwards |
| DB: zero-length | recovered (nothing to preserve) |
| DB: flipped interior pages | file left in place (SQLite did not detect the damage on this access path); CLI still succeeds |
| two consecutive corruptions | **the first quarantined copy is deleted by the second recovery** (single backup slot) |
| record appended after a crash-torn recorder line | **the new record is glued to the torn fragment and lost** |

Bounded and local: every subprocess has a hard timeout; the kill-storm test reaps every process it starts.
