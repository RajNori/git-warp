# Example: Git X-Ray

Command (captured 2026-10-02, repository state described in [README.md](README.md)):

```bash
python3 scripts/warp.py xray --repo <repo>
```

## Captured output (trimmed)

The full output was about 8.5 KB. `"...": "..."` marks removed keys or items; everything else is verbatim.

```json
{
  "command": "xray",
  "repo": "<repo>",
  "state": {
    "branch": "feature/billing",
    "detached": false,
    "unborn": false,
    "head_short": "afef50ed",
    "upstream": null,
    "ahead": null,
    "behind": null,
    "operation": null,
    "shallow": false,
    "worktrees": { "count": 1, "...": "..." },
    "stashes": { "count": 0, "items": [], "truncated": false },
    "reflog": { "available": true, "count": 9, "count_capped": false },
    "tracked_ahead_of_base": { "base": "main", "ahead": 3 },
    "...": "..."
  },
  "working_tree": {
    "clean": false,
    "staged": { "count": 0, "paths": [], "truncated": false },
    "unstaged": { "count": 1, "paths": ["src/app.py"], "truncated": false },
    "untracked": { "count": 1, "paths": ["notes.txt"], "truncated": false, "files_total": 1, "collapsed_dirs": 0, "untracked_all": false },
    "conflicted": { "count": 0, "paths": [], "truncated": false },
    "numstat_totals": {
      "files": 1, "added": 1, "deleted": 1,
      "scope": "tracked changes vs HEAD (staged+unstaged); untracked files excluded"
    },
    "...": "..."
  },
  "recent_commits": [
    { "sha": "afef50ed", "subject": "test: cover invoice_slug", "author": "Ada Dev", "date": "2026-09-03T10:00:00+10:00", "merge": false },
    "... 7 more"
  ],
  "high_churn": {
    "window": { "commits": 8, "days": 30, "note": "last 200 non-merge commits; commits_last_30d counts those within 30 days" },
    "files": [
      { "path": "src/util.py", "commits": 3, "commits_last_30d": 0, "touched_in_change_set": false },
      "... 3 more"
    ],
    "source": "git-log"
  },
  "signals": {
    "tests": {
      "potential_missing_tests": true,
      "source_files_changed": 1,
      "test_files_changed": 0,
      "source_files": ["src/app.py"],
      "test_files": [],
      "reasoning": "1 source file(s) changed (e.g. src/app.py) and 0 test files changed in the same change set. Heuristic: existing tests may already cover this."
    },
    "mixed_concerns": { "flag": false, "reasons": [], "heuristic": true },
    "...": "migrations, schema, config, infra, ci, dependency_manifests, lockfiles, dependency_drift, secrets, generated_files, large_changes, binary_files (all empty here)"
  },
  "clusters": {
    "source": "semantic.cluster",
    "items": [
      { "id": 1, "label": "docs", "kind": "docs", "file_count": 1, "paths": ["notes.txt"], "reasons": ["documentation files"], "added": 0, "deleted": 0 },
      { "id": 2, "label": "root source", "kind": "source", "file_count": 1, "paths": ["src/app.py"], "reasons": ["files at the repository root"], "added": 1, "deleted": 1 }
    ],
    "...": "atomic_commit_opportunities"
  },
  "recovery": {
    "reflog_available": true,
    "reflog_entries": 9,
    "stashes": 0,
    "worktrees": 1,
    "dangling_commits": "not checked (xray never runs fsck); use `warp.py rescue scan` if work looks lost",
    "orig_head": false
  },
  "risk": {
    "level": "MEDIUM",
    "drivers": ["MEDIUM: no upstream configured and 3 commit(s) ahead of main"],
    "driver_details": [
      { "id": "no-upstream-ahead", "level": "MEDIUM", "count": 3, "reason": "no upstream configured and 3 commit(s) ahead of main" }
    ],
    "rules_version": 1
  },
  "recommendation": [
    { "id": "set-upstream-before-push", "why": "branch has local commits and no upstream", "inspect": "git branch -vv" },
    { "id": "add-tests", "why": "source changed with no test change", "inspect": "git diff --stat" }
  ],
  "warnings": []
}
```

Things worth noticing in the real output: the risk level is a rule outcome with a named driver, `dangling_commits`
is explicitly "not checked", and the heuristic fields (`potential_missing_tests`, `mixed_concerns`) are labelled
as heuristics.

## ILLUSTRATIVE rendering (not captured model output)

The block below was written by hand to show how the template in `skills/git-xray/SKILL.md` would lay out the JSON
above. A real Claude answer would differ in wording and would first read the changed files to verify intent.

```
GIT WARP — X-RAY

STATE
Branch:      feature/billing
Upstream:    none
Ahead:       n/a
Behind:      n/a
Dirty paths: 2 (0 staged, 1 unstaged, 1 untracked, 0 conflicted)
In progress: none   Stashes: 0   Worktrees: 1   Shallow: no

RISK: MEDIUM
WHY
- MEDIUM: no upstream configured and 3 commit(s) ahead of main

CHANGE CLUSTERS
1. docs (docs, 1 file, +0/-0)
2. root source (source, 1 file, +1/-1)

SIGNALS
- potential missing tests (heuristic): 1 source file changed, 0 test files changed

RECOVERY
- Reflog: available, 9 entries | Stashes: 0 | Worktrees: 1 | Dangling commits: not checked (use /git-rescue if work looks lost)

RECOMMENDATION
1. Set an upstream before pushing (branch has local commits and no upstream).
2. Add or update tests for src/app.py.

SAFE NEXT ACTION
git branch -vv
```
