# Example: Git Archaeology

Captured 2026-10-02.

## 1. A live file: `src/util.py`

```bash
python3 scripts/warp.py archaeology src/util.py --repo <repo>
```

Trimmed (`"...": "..."` marks removed keys or items; the full output was about 6.9 KB):

```json
{
  "command": "archaeology",
  "state": { "branch": "feature/billing", "detached": false, "unborn": false, "shallow": false, "operation": null, "...": "head" },
  "limit": 200,
  "since": null,
  "mode": "file",
  "target": "src/util.py",
  "found": true,
  "truncated": false,
  "examined_commits": 3,
  "currently_in_head": "blob",
  "introduction": {
    "tag": "fact",
    "commit": { "short": "9ae8c9b0", "date": "2026-08-01T10:00:00+10:00", "author": "Ada Dev", "subject": "feat: initial slug helper and page builder", "...": "sha" },
    "note": "oldest commit in followed history; it adds the path"
  },
  "renames": [],
  "reverts": [],
  "fix_like_commits": [
    { "tag": "inference", "basis": "commit message matches fix-like keywords", "short": "8f059303", "date": "2026-08-09T10:00:00+10:00", "subject": "fix: collapse repeated spaces in slugify", "churn": 2, "...": "sha, author" },
    { "tag": "inference", "basis": "commit message matches fix-like keywords", "short": "377715ef", "date": "2026-08-03T10:00:00+10:00", "subject": "fix: strip whitespace in slugify", "churn": 2, "...": "sha, author" }
  ],
  "blame_summary": {
    "total_lines": 2,
    "distinct_commits": 2,
    "top_commits": [
      { "tag": "fact", "short": "9ae8c9b0", "surviving_lines": 1, "author": "Ada Dev", "summary": "feat: initial slug helper and page builder", "in_followed_history": true, "...": "sha" },
      { "tag": "fact", "short": "8f059303", "surviving_lines": 1, "author": "Ada Dev", "summary": "fix: collapse repeated spaces in slugify", "in_followed_history": true, "...": "sha" }
    ]
  },
  "authorship_timeline": [
    { "tag": "fact", "author": "Ada Dev", "commits": 3, "first": "2026-08-01T10:00:00+10:00", "last": "2026-08-09T10:00:00+10:00" }
  ],
  "timeline": [
    { "short": "8f059303", "date": "2026-08-09T10:00:00+10:00", "subject": "fix: collapse repeated spaces in slugify", "added": 1, "deleted": 1, "status": "M", "...": "sha, author, merge, churn, path" },
    "... 2 more (newest first)"
  ],
  "statements": [
    { "tag": "fact", "text": "Oldest matching commit 9ae8c9b0 (2026-08-01, Ada Dev): feat: initial slug helper and page builder - oldest commit in followed history; it adds the path", "commits": ["9ae8c9b0"] },
    { "tag": "inference", "text": "2 commit(s) have fix-like messages (message heuristic; says nothing about whether they actually fixed anything)", "commits": ["8f059303", "377715ef"] },
    { "tag": "fact", "text": "Contribution history only (not ownership): Ada Dev (3)", "commits": [] }
  ],
  "commits_worth_reading": [
    { "short": "9ae8c9b0", "subject": "feat: initial slug helper and page builder", "reasons": [ { "tag": "fact", "reason": "introduction" } ], "...": "sha, date, author" },
    "... 2 more"
  ],
  "unknown": [
    "Why each change was made (motive/intent): git stores messages, not reasons; any motive is an inference from the message text.",
    "Whether commit messages are accurate or complete.",
    "History that was squashed, rebased or force-pushed away is not visible unless it is still in the reflog.",
    "Work on branches not merged into HEAD (only HEAD's history was searched).",
    "Renames below git's similarity threshold (-M default 50%) would appear as a delete plus an add, not as a rename."
  ],
  "warnings": [],
  "...": "query, path_history, deleted_then_restored, currently_deleted, large_rewrites, merge_points, first_parent_history, symbol_presence"
}
```

## 2. A deleted file: `src/cache.py`

```bash
python3 scripts/warp.py archaeology src/cache.py --repo <repo>
```

Selected keys from the real output:

```json
{
  "found": true,
  "mode": "file",
  "currently_in_head": null,
  "currently_deleted": { "tag": "fact", "deleted_in": "3d50bae3", "subject": "revert: remove cache helper, stale data in tests" },
  "introduction": { "tag": "fact", "commit": { "short": "598f5d5d", "subject": "feat: add in-memory cache helper", "...": "..." }, "note": "oldest commit in followed history; it adds the path" },
  "reverts": [],
  "...": "everything else omitted"
}
```

The commit subject starts with `revert:` but archaeology reported `"reverts": []`. In the same repository
`memory reverts` reported `revert_commits_total: 0` with the note that detection works from `Revert "..."` subjects
and `This reverts commit <sha>` bodies only. (That note comes from the memory command; the archaeology code was not
separately checked for its detection rules.)

## ILLUSTRATIVE rendering (not captured model output)

Hand-written example following `skills/git-archaeology/SKILL.md` for run 1.

```
STATE
  src/util.py, file mode, 3 commits examined, not truncated, not shallow

TIMELINE
  2026-08-01 9ae8c9b0 feat: initial slug helper and page builder (adds the file)
  2026-08-03 377715ef fix: strip whitespace in slugify
  2026-08-09 8f059303 fix: collapse repeated spaces in slugify

FACT
  - 9ae8c9b0 introduced the file (oldest commit in followed history)
  - 2 commits account for the 2 current lines (1 each from 9ae8c9b0 and 8f059303)

INFERENCE
  - 377715ef and 8f059303 look like fixes (message heuristic only)

UNKNOWN
  - why each change was made; work on unmerged branches

COMMITS WORTH READING
  1. 9ae8c9b0 feat: initial slug helper and page builder - introduction [fact]
  2. 8f059303 fix: collapse repeated spaces in slugify - latest change, 1 line survives [fact+inference]
```
