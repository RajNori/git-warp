# Commands

Each Git Warp feature is one skill that runs one or more `scripts/warp.py` commands. This page lists, per command:
the purpose, the underlying CLI call with flags (verified against each `cli.py`), whether it changes anything, and
the **top-level JSON keys observed in real runs** against a throwaway repository (see [../examples/](../examples/)).
Key lists are observations, not a schema promise; keys that only appear in some situations are noted.

Common to all commands: output is JSON on stdout; failures are `{"error": ...}` with a non-zero exit code;
`--repo PATH` selects the repository (default: current directory). Every flag is in
[cli-reference.md](cli-reference.md).

"Read-only" below means: does not change your repository, index, work tree or refs. Some read-only analyses still
create or refresh Git Warp's own index file (`.git/git-warp/warp.db`); this is stated where it applies. In a run
against a fresh repository, only `temporal` and the `memory` queries created `warp.db`; `xray`, `pr`, `rescue scan`,
`commits`, `blast`, `archaeology`, `bisect status` and `conflict` did not create `.git/git-warp` at all.

In a headless session the skills were listed as `git-warp:<name>`; that namespaced form is used here.

| Skill | CLI | Mutates |
|---|---|---|
| `git-warp:git-xray` | `xray` | no |
| `git-warp:git-pr` | `pr` | no |
| `git-warp:git-rescue` | `rescue scan`, `rescue inspect`, `rescue preserve` | `preserve` only: creates one branch ref |
| `git-warp:git-archaeology` | `archaeology` | no |
| `git-warp:git-bisect-ai` | `bisect plan`, `bisect status` | no (never starts a bisect, never runs the test command) |
| `git-warp:git-commits` | `commits` | no (never stages or commits) |
| `git-warp:git-blast-radius` | `blast` | no |
| `git-warp:git-conflict` | `conflict` | no |
| `git-warp:git-temporal-review` | `temporal` | no (creates/refreshes `warp.db`) |
| `git-warp:git-memory` | `memory ...` | writes `warp.db`; `forget --yes` deletes local state |
| Guardian (hook) | `guard check` | no (never runs the command) |

---

## xray (Git X-Ray)

Purpose: snapshot of repository state and risk. Read-only.

```
warp.py xray [--untracked-all] [--repo PATH]
```

Top-level keys: `command`, `repo`, `state`, `working_tree`, `recent_commits`, `high_churn`, `sensitive_paths`,
`signals`, `clusters`, `recovery`, `risk`, `recommendation`, `warnings`.
`risk` has `level` (LOW/MEDIUM/HIGH), `drivers`, `driver_details`, `rules_version`. `recovery.dangling_commits` is
"not checked": X-Ray never runs `git fsck` (use `rescue scan`). `--untracked-all` lists every untracked file instead
of collapsing directories. Example: [../examples/xray.md](../examples/xray.md).

## pr (PR Engineer)

Purpose: facts for a pull-request description from `merge-base(base, HEAD)..HEAD`. Read-only. Does not run tests.

```
warp.py pr [BASE | --base REF] [--repo PATH]
```

Without a base it auto-detects one (the observed run chose `main` with `auto_detected: true`). Giving both a
positional BASE and `--base` with different values is an error.

Top-level keys: `command`, `repo`, `status`, `base`, `merge_base`, `head`, `behind_base`, `is_behind_base`,
`ahead_of_base`, `upstream`, `not_included`, `commits`, `files`, `totals`, `largest_changes`, `clusters`,
`unrelated_changes`, `tags`, `dependencies`, `api_surface`, `debug_leftovers`, `secrets`, `artifacts`, `tests`,
`rollback`, `testing_evidence`, `patch_scan`, `hygiene`, `facts_for_prose`, `risk`, `warnings`.
The `pr` SKILL.md also mentions a `status: no-commits` case with a `message`; that case was not exercised.
Example: [../examples/pr.md](../examples/pr.md).

## rescue (Git Rescue)

Purpose: find and preserve commits that look lost. `rescue` with no subcommand means `rescue scan`.

```
warp.py rescue scan [--since TEXT] [--grep TEXT] [--path PATH] [--limit N=25] [--no-fsck]
                    [--fsck-timeout S=45] [--include-reachable] [--repo PATH]
warp.py rescue inspect <sha> [--repo PATH]
warp.py rescue preserve <sha> [--name BRANCH] [--dry-run] [--repo PATH]
```

- `scan` (read-only; `--no-fsck` skips the slow dangling-object scan and then dropped stashes and unreferenced
  commits are missed). Keys: `command`, `principle`, `state`, `filters`, `candidates`, `candidate_summary`,
  `deleted_branch_candidates`, `reflog_signals`, `stashes`, `worktrees`, `fsck`, `dangling_objects`,
  `refs_examined`, `truncated`, `unknown`, `warnings`. Each candidate has `confidence` (`high|medium|low`),
  `confidence_reasons`, `why_candidate`, `kinds`, `safe_action`, `safe_action_via_warp`.
- `inspect` (read-only). Keys: `command`, `commit`, `files`, `files_total`, `stat`, `reachable_from`,
  `unreachable`, `ancestry_vs_head`, `head_reflog_mentions`, `safe_action`, `state`, `warnings`, `principle`.
- `preserve` (**the only command that creates a Git ref**). Runs `git branch <name> <sha>` where the default name
  is `rescue/<YYYY-MM-DD>-<sha8>`. It rejects non-commit ids and invalid branch names, **refuses to overwrite or
  move an existing branch** (a repeat with the same name and sha reports `already_preserved`), and checks HEAD is
  unchanged afterwards. Keys: `command`, `dry_run`, `principle`, `status` (`dry_run`, `created`,
  `created_unverified`, `already_preserved`), plus `would_run`/`ran`, `branch`, `sha`, `changed`, `verified`,
  `head_unchanged`, `verify`, `next` (varies by status). Exit code 2 on error.

Examples: [../examples/rescue-scan.md](../examples/rescue-scan.md).

## archaeology (Git Archaeology)

Purpose: fact/inference/unknown timeline for a path, symbol or pattern. Read-only. Works for deleted paths.

```
warp.py archaeology [TARGET] [--symbol NAME] [--regex RE] [--question TEXT] [--limit N=200] [--since DATE] [--repo PATH]
```

`--symbol` uses `git log -S`; `--regex` uses `-G`; `--question` matches keywords against commit messages only.
Top-level keys: `command`, `state`, `limit`, `since`, `mode`, `target`, `found`, `truncated`, `examined_commits`,
`query`, `currently_in_head`, `introduction`, `path_history`, `renames`, `deleted_then_restored`,
`currently_deleted`, `reverts`, `fix_like_commits`, `large_rewrites`, `merge_points`, `first_parent_history`,
`blame_summary`, `symbol_presence`, `authorship_timeline`, `timeline`, `statements`, `commits_worth_reading`,
`unknown`, `warnings`. Items carry `tag: fact|inference`. Example: [../examples/archaeology.md](../examples/archaeology.md).

## bisect (AI Bisect)

Purpose: plan a safe bisect; report progress. Read-only; `executed` is always `false`.

```
warp.py bisect plan --good REF [--good REF ...] [--bad REF=HEAD] [--test CMD] [--repo PATH]
warp.py bisect status [--repo PATH]
```

The `--test` string is validated syntactically and echoed; it is never executed.
`plan` keys: `command`, `executed`, `note`, `state`, `ready`, `refs`, `range`, `blockers`, `warnings`,
`reproducibility_risks`, `isolation`, `test`, `predicate_guidance`, `commands`. In a run on a dirty working tree
`ready` was `false`, `blockers` held a `dirty_working_tree` entry with `suggest` options and `commands` was `null`;
on a clean tree `ready` was `true` and `commands` listed seven steps (create a detached worktree, `git bisect start`,
`git bisect run sh -c '<test>'`, `git bisect log`, `git bisect reset`, remove the worktree). Those commands are text
for you (or Claude, with your approval) to run.
`status` keys: `command`, `state`, `executed`, `in_progress`, `message`, `hint`, `warnings` (fields for an active
bisect were not exercised).

## commits (Semantic Commit Composer)

Purpose: cluster the change set and propose ordered Conventional Commit skeletons. Read-only: nothing is staged or
committed; `notes` says so. Messages contain `<placeholders>`.

```
warp.py commits [--staged] [--repo PATH]
```

Top-level keys: `repo`, `mode`, `state`, `summary`, `files`, `clusters`, `proposals`, `commands`, `mixed_concerns`,
`hunks`, `flags`, `notes`, `warnings`. (No `command` key.) `flags` holds `secrets`, `generated`, `conflicted`,
`binary`. Example: [../examples/commits.md](../examples/commits.md).

## blast (Blast Radius)

Purpose: static dependants/tests/interfaces for changed files with a rule-based level. Read-only.

```
warp.py blast [PATH ...] [--base REF] [--depth N=3] [--max-nodes N=200] [--timeout S=15] [--repo PATH]
```

No paths: the working tree. `--base REF`: the branch diff `REF...HEAD`. Explicit paths: those files. `mode` was
observed as `working-tree`, `paths` and `base:main`.
Top-level keys: `repo`, `mode`, `base`, `state`, `level`, `level_note`, `level_rules`, `risk_drivers`,
`risk_driver_details`, `changed`, `nodes`, `tree`, `counts`, `truncated`, `stats`, `caveats`, `warnings`.
Node `confidence` values observed: `import-graph`, `changed`; the SKILL.md also names `naming` and `text-match`
(heuristics). Example: [../examples/blast.md](../examples/blast.md).

## conflict (Conflict Surgeon)

Purpose: describe an active merge/rebase/cherry-pick/revert conflict. Read-only.

```
warp.py conflict [--repo PATH]
```

With no conflict: `repo`, `state`, `conflicts` (empty), `message` ("no unmerged paths: not currently in a
conflicted state"), `warnings`. During a real merge conflict the keys were: `repo`, `state`, `operation`,
`swap_note`, `ours`, `theirs`, `merge_base`, `pick_base`, `rebase`, `conflicts`, `summary`, `read_only`, `notes`,
`warnings`. Each conflict had `path`, `conflict_type`, `stages`, `stage_meaning`, `line_endings`, `regions`,
`region_count`, `relationship_hint`, `history`. A region had line ranges, ours/theirs/base text, `blame` and a
`relationship_hint` (observed value `overlapping`). Rebase/cherry-pick/revert states were not exercised.

## temporal (Temporal Code Review)

Purpose: historical evidence for the current change. Creates or refreshes `warp.db` (it uses the memory index).

```
warp.py temporal [--base REF] [--limit N=20] [--budget N=25] [--timeout S=30] [--repo PATH]
```

Top-level keys: `repo`, `state`, `mode`, `base`, `changed_files`, `findings`, `findings_total`, `probes`
(`used`, `budget`, `timed_out`, `seconds`), `sources` (`git_log_pickaxe`, `memory_index`), `instructions`,
`warnings`, `message`. In the observed run `findings` was empty and `message` said "no historical evidence found
within the probe budget (17/25 probes); this is not proof that the change is safe". A second run that re-added a previously
removed file produced one finding with keys `kind` (`touched-file-has-reverts`), `file`, `match_quality`
(`heuristic`), `evidence` (sha, short, subject, date), `signals`, `caveat`, `id`, `verify_with`. See
[../examples/temporal.md](../examples/temporal.md).

## memory (Repository Memory)

Purpose: local history index and flight-recorder queries.

```
warp.py memory index [--max-commits N] [--rebuild] [--repo PATH]
warp.py memory status [--repo PATH]
warp.py memory cochange <path> [--limit N=20]
warp.py memory hotspots [--limit N=20]
warp.py memory churn [PREFIX] [--limit N=20]
warp.py memory introduced <path>
warp.py memory reverts [--limit N=20]
warp.py memory authors <path>
warp.py memory sessions [--limit N=10]
warp.py memory forget [--yes]
```

All subcommands take `--repo`. Query subcommands (`cochange`, `hotspots`, `churn`, `introduced`, `reverts`,
`authors`) first refresh the index incrementally (capped at 5000 commits and a 60 s budget) and report
freshness under `index`. They fail with an error when `memory_enabled: false`.

| Subcommand | Mutates | Observed top-level keys |
|---|---|---|
| `index` | writes/updates `warp.db` | `command`, `repo`, `index`, `warnings` |
| `status` | no | `command`, `repo`, `enabled`, `state_dir`, `index`, `recorder`, `sessions_recorded`, `warnings` |
| `cochange` | refreshes `warp.db` | `command`, `repo`, `index`, `path`, `cochange`, `note`, `warnings` |
| `hotspots` | refreshes `warp.db` | `command`, `repo`, `index`, `hotspots`, `formula`, `note`, `warnings` |
| `churn` | refreshes `warp.db` | `command`, `repo`, `index`, `prefix`, `churn`, `warnings` |
| `introduced` | refreshes `warp.db` | `command`, `repo`, `index`, `introduced`, `history`, `warnings` |
| `reverts` | refreshes `warp.db` | `command`, `repo`, `index`, `revert_commits_total`, `reverts`, `repeatedly_reverted_files`, `note`, `warnings` |
| `authors` | refreshes `warp.db` | `command`, `repo`, `index`, `path`, `scope`, `note`, `authors`, `total_commits`, `found`, `warnings` |
| `sessions` | no | `command`, `repo`, `sessions`, `known_sessions`, `note`, `warnings` |
| `forget` | **deletes local state** | without `--yes`: `error`, `would_delete`, `state_dir` (exit 2); with `--yes`: `command`, `repo`, `deleted`, `kept`, `warnings` (observed in a throwaway repo) |

`forget --yes` deletes `warp.db` (and its `-wal`, `-shm`, `-journal`, `.corrupt` siblings) and
`flight-recorder.jsonl` (and `.1`); it keeps `state.json`. Without `--yes` it deletes nothing. Authors output
includes author email addresses (they are in your Git history). Example:
[../examples/memory-hotspots.md](../examples/memory-hotspots.md).

## guard (Git Guardian debug aid)

Purpose: show what the guard would decide. Never executes the command. Read-only.

```
warp.py guard check "<command string>" [--repo PATH] [--branch NAME] [--mode standard|strict] [--protected a,b]
```

`--branch` assumes a current branch instead of looking it up; `--mode` overrides `safety_mode`; `--protected`
overrides `protected_branches` with a comma-separated list (globs allowed).
Output keys: `decision`, `rule`, `operation`, `reason`, `safer`, `commands`, `branch`, `safety_mode`.
Example: [../examples/guard-check.md](../examples/guard-check.md). Rule table: [safety-model.md](safety-model.md).

---

## Hooks (no skill)

| Hook script | Event | Output |
|---|---|---|
| `hook_session_start.py` | `SessionStart` | `additionalContext` text; records a `SessionStart` line; registers the session and runs a bounded auto-index when `memory_enabled` |
| `hook_git_guard.py` | `PreToolUse` (`Bash`) | `{}` or a `deny`/`ask` decision |
| `hook_post_tool.py` | `PostToolUse` (`Write\|Edit\|MultiEdit\|NotebookEdit\|Bash`) | `{}`; appends a redacted record |
| `hook_stop.py` | `Stop` | optional `systemMessage` report; never blocks; ignores `stop_hook_active` |

## Agents

`agents/git-forensic-analyst.md` (rescue, archaeology, bisect planning), `agents/git-history-analyst.md`
(archaeology), `agents/git-risk-analyst.md` (commits, blast, temporal, conflict). All are described as read-only
and use `Read`, `Grep`, `Glob`, `Bash`. That restriction is in their prompts; unlike the guard it is not enforced
by code. The forensic analyst's prompt allows one write, `rescue preserve`, only when explicitly asked.
