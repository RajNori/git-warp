# Repository memory reference

Location: `<common git dir>/git-warp/` (`git rev-parse --git-common-dir`). Plain files, safe to delete.

## warp.db (SQLite, `PRAGMA user_version` = schema version, currently 1)

| table | columns / meaning |
|---|---|
| `commits` | sha PK, parents, author_name, author_email, author_date, commit_date, commit_ts (epoch), subject, is_merge, is_revert, reverts_sha |
| `files` | id, path UNIQUE (paths as Git reports them; unicode/spaces fine) |
| `commit_files` | sha, file_id, added, deleted (0 for binary), status A/M/D/R, old_path (renames/copies); an `RD` row marks a rename source and carries no churn |
| `cochanges` | file_a < file_b (file ids), count; built from non-merge commits touching 2..40 files |
| `authors` | email, name, commits, first, last (global contribution history) |
| `sessions`, `events` | session starts recorded by the SessionStart hook |
| `refs` | branch/tag/remote ref snapshot at last index |
| `index_state` | key/value: head, complete (0/1), max_commits, indexed_at, shallow, last_mode |
| `file_stats` (view) | per-file commits/added/deleted/last_ts |

Incremental: only `<last indexed head>..HEAD` is walked. If the old head is no longer an ancestor (rebase, amend, reset, switching to a diverged branch) the index is rebuilt. `--max-commits N` indexes only the newest N (`complete: false`).

Corrupt or newer-schema DB: moved to `warp.db.corrupt` and rebuilt. Unwritable directory: index kept in memory for that run, with a warning.

## flight-recorder.jsonl

One JSON object per line: `ts`, `session_id`, `hook_event`, `tool {name, category: edit|shell|git|other}`, `files` (repo-relative; `<outside-repo>` otherwise), `command` (Bash only: redacted, newlines folded, 300 chars), `branch`, `head`, optional `dirty_count`, `test_outcome` (only if explicitly supplied). Never contents, edit strings, prompts, tool responses or environment. Entries older than the retention window are dropped (checked at most daily); the file rotates to `.1` at 5 MB.

## CLI output (all JSON; errors are `{"error": ...}` with non-zero exit)

Every response has `command`, `repo`, `warnings`; query responses add `index` (freshness: mode, indexed, total_commits, complete, head, shallow).

- `cochange`: `cochange: [{path, count, ratio, commits_touching_path}]`, `note`
- `hotspots`: `hotspots: [{path, score, commits, recent_commits_90d, lines_changed, authors, last_changed}]`, `formula`
- `churn`: `churn: [{path, commits, lines_changed, authors, last_changed}]`
- `introduced`: `introduced {found, sha, date, author, subject, evidence, renames_followed, complete_history}`, `history` (latest commits)
- `reverts`: `reverts`, `revert_commits_total`, `repeatedly_reverted_files`
- `authors`: `authors: [{name, email, commits, lines_changed, first, last}]`, `scope` (file|directory), `note`: "historical contribution evidence, not current ownership"
- `sessions`: `sessions: [{session_id, first, last, events, edits, shell, git_commands, files_touched, top_files, branches, heads}]`
- `forget --yes`: `deleted: [...]`; without `--yes` it refuses and lists `would_delete`.
