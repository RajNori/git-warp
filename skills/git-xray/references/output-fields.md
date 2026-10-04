# X-Ray JSON fields

All path lists look like `{count, paths[<=50], truncated}`.

- `state`: `branch`, `detached`, `unborn`, `head`/`head_short`, `upstream`, `upstream_gone` (configured but ref missing), `ahead`/`behind` (null without upstream), `operation` (`{type: merge|rebase|cherry-pick|revert|bisect, branch?}`), `shallow`, `worktrees{count,items[],current}`, `stashes{count,items[]}`, `reflog{available,count,count_capped}`, `tracked_ahead_of_base{base,ahead}` (only when there is no upstream).
- `working_tree`: `clean`, `staged`, `unstaged`, `untracked` (+`files_total`, `collapsed_dirs`), `conflicted`, `numstat_totals` (tracked changes vs HEAD only), `largest_changes[5]`, `binary_files`.
- `recent_commits`: last 10 (`sha`, `subject`, `author`, `date`, `merge`).
- `high_churn`: files with >=2 commits in the last 200 non-merge commits; `commits_last_30d`; `touched_in_change_set`. `source` is `git-log` or `memory.index`.
- `sensitive_paths`: changed paths tagged sensitive (auth, payment, crypto, config globs) plus `secret_files` (.env, keys, certs; `.env.example` excluded).
- `signals`: `migrations`, `schema`, `config`, `infra`, `ci`, `dependency_manifests`, `lockfiles`, `dependency_drift` (manifest without lockfile / lockfile without manifest, heuristic), `tests` (`potential_missing_tests` with `reasoning`: source changed and no test file changed in the same change set), `secrets` (`secret_file_paths`, `content_findings` with path/line/kind and value always `[REDACTED]`; `in_test_file` marks likely fixtures), `generated_files`, `large_changes`, `binary_files`, `mixed_concerns{flag,reasons}`.
- `clusters`: `source` (`semantic.cluster` or `fallback`), `items[{id,label,kind,file_count,paths,added,deleted,reasons}]`, `atomic_commit_opportunities`. Labels are suggestions; rename or merge them after reading the files.
- `recovery`: `reflog_available`, `reflog_entries`, `stashes`, `worktrees`, `orig_head`, `dangling_commits` (never computed here).
- `risk`: `level`, `drivers[]` (strings with evidence counts), `driver_details[{id,level,count,reason}]`.
- `recommendation[]`: `{id, why, inspect}`; `inspect` is a read-only command.
- `warnings[]`: always surface.
