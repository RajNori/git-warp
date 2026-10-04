# PR JSON fields

Top level: `status` (`ok` | `no-commits`), `base{ref,sha,auto_detected}`, `merge_base`, `head{sha,branch,detached}`, `behind_base`, `is_behind_base`, `ahead_of_base`, `upstream{ref,ahead,behind,state}` (state: none, in-sync, ahead-of-upstream, behind-upstream, diverged), `not_included`, `warnings`. Errors return `{"error", "hint"}` with exit code 2.

- `commits`: `count`, `analysed` (max 500), `merge_commits`, `authors[]`, `items[<=50]`, `conventional_commits{conforming, nonconforming[], nonconforming_total}`.
- `files`: `items[<=200]` with `status` (added/modified/deleted/renamed), `renamed_from`, `binary`; `totals`; `largest_changes`.
- `clusters`: same shape as X-Ray (`semantic.cluster` or `fallback`); `unrelated_changes{flag, reasons[]}` (heuristic).
- `tags`: per tag (migration, schema, dependency, lockfile, config, infra, ci, docs, test, sensitive, generated, secret-file) `{count, paths}`.
- `dependencies`: `manifest_without_lockfile[]`, `lockfile_without_manifest[]` (heuristic).
- `api_surface`: heuristic. `possibly_breaking_removed`, `signature_changed`, `added` (JS/TS `export`, top-level Python def/class), `schema_files_changed`.
- `debug_leftovers`: added lines only; `counts` by kind (console-log, debugger-statement, python-print, pdb-breakpoint, todo-marker, test-only, test-skip, ...), `items` with redacted snippet.
- `secrets`: `count`, `findings[{path,line,kind,value:"[REDACTED]",in_test_file}]`, `secret_file_paths`.
- `artifacts`: `binary_files`, `generated_files`, `large_files` (>1 MiB), `very_large_diffs` (>1000 changed lines).
- `tests`: `potential_missing_tests`, counts, `reasoning`.
- `rollback`: `migrations[{path,status,reversible_signal}]` (keyword heuristic), `destructive_statements_added`, `dependencies_changed`, `config_files_changed`, `infra_or_ci_changed`.
- `testing_evidence`: `found_in_repo{ci_config_files,test_directories,test_runner_configs}`, `ran_by_git_warp: false`.
- `patch_scan`: `truncated`, `bytes_cap`, scope (net diff of added lines).
- `hygiene[]`: `{id, count, ...}`.
- `facts_for_prose`: `size`, `type_hints`, `scope_hints`, `cluster_labels`, `ticket_refs`, `breaking_change_markers`, `commit_subjects`.
- `risk`: `level` + `drivers` (same format as X-Ray, PR rules in `analysis/risk.py::evaluate_pr`).
