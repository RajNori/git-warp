# Example: Repository Memory

Captured 2026-10-02 in the demo repository (8 commits, one author).

## `memory hotspots`

The first memory query built the index (`mode: rebuild`, `reason: first index`) and created
`.git/git-warp/warp.db`.

```bash
python3 scripts/warp.py memory hotspots --repo <repo>
```

Trimmed (3 of 8 rows shown):

```json
{
  "command": "memory hotspots",
  "repo": "<repo>",
  "index": {
    "mode": "rebuild",
    "indexed": 8,
    "total_commits": 8,
    "complete": true,
    "shallow": false,
    "persisted": true,
    "elapsed_ms": 29,
    "reason": "first index",
    "...": "head"
  },
  "hotspots": [
    { "path": "src/util.py", "score": 2.81, "commits": 3, "recent_commits_90d": 3, "lines_changed": 6, "authors": 1, "last_changed": "2026-08-09" },
    { "path": "package.json", "score": 2.45, "commits": 2, "recent_commits_90d": 2, "lines_changed": 9, "authors": 1, "last_changed": "2026-09-02" },
    { "path": "tests/test_billing.py", "score": 1.42, "commits": 1, "recent_commits_90d": 1, "lines_changed": 5, "authors": 1, "last_changed": "2026-09-03" },
    "... 5 more"
  ],
  "formula": "score = sum over non-merge commits touching the file of 0.5^(age_days/90) * (1 + log10(1 + lines_changed_in_commit)); a relative ranking signal (recent, large, repeated change), not a defect probability",
  "note": "generated/vendored/lock files and deleted files are excluded; ranking is relative evidence, not a defect prediction",
  "warnings": []
}
```

`package-lock.json` (changed in 2 commits) is absent from the list, and so is the deleted `src/cache.py`: lockfiles
and deleted files are excluded by design.

## `memory status`

```bash
python3 scripts/warp.py memory status --repo <repo>
```

```json
{
  "command": "memory status",
  "repo": "<repo>",
  "enabled": { "memory": true, "recorder": true, "retention_days": 30 },
  "state_dir": "<repo>/.git/git-warp",
  "index": {
    "exists": true,
    "db": "<repo>/.git/git-warp/warp.db",
    "size_bytes": 94208,
    "schema_version": 1,
    "complete": true,
    "max_commits": "5000",
    "indexed_at": "2026-10-02T08:05:13Z",
    "shallow": false,
    "commits": 8,
    "files": 10,
    "cochange_pairs": 16,
    "up_to_date": true,
    "...": "head, current_head"
  },
  "recorder": { "exists": false, "path": "<repo>/.git/git-warp/flight-recorder.jsonl" },
  "sessions_recorded": 0,
  "warnings": []
}
```

## `memory cochange src/util.py`

```json
{
  "command": "memory cochange",
  "path": "src/util.py",
  "cochange": [
    { "path": "README.md", "count": 1, "ratio": 0.33, "commits_touching_path": 3 },
    { "path": "src/app.py", "count": 1, "ratio": 0.33, "commits_touching_path": 3 },
    "... 3 more"
  ],
  "note": "count = commits in which both files changed (commits touching >40 files and merges excluded); ratio = count / commits touching the path. Correlation, not causation.",
  "...": "repo, index, warnings"
}
```

With 8 commits these numbers are tiny and mostly reflect the initial commit; they illustrate the output shape,
not a meaningful result.

## ILLUSTRATIVE rendering (not captured model output)

```
Hotspots (relative evidence, not defect prediction; score formula in the tool output)
1. src/util.py  - 3 commits, 6 lines changed, last changed 2026-08-09
2. package.json - 2 commits, 9 lines changed, last changed 2026-09-02
Caveat: index complete (8 of 8 commits); lock files and deleted files are excluded.
```
