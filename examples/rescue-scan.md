# Example: Git Rescue

Situation: the branch `experiment/search` (one unmerged commit) was deleted. Captured 2026-10-02.

## 1. `rescue scan` (read-only)

```bash
python3 scripts/warp.py rescue scan --repo <repo>
```

Captured output (trimmed; `"...": "..."` marks removed keys; the full output was about 2.8 KB):

```json
{
  "command": "rescue scan",
  "principle": "Preserve first. Investigate second. Mutate last.",
  "state": { "branch": "feature/billing", "detached": false, "unborn": false, "shallow": false, "operation": null, "orig_head": null, "...": "head" },
  "candidates": [
    {
      "sha": "55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
      "short": "55158fbb",
      "timestamp": "2026-08-12T10:00:00+10:00",
      "last_seen_in_reflog": null,
      "author": "Ada Dev",
      "subject": "feat: experimental search over slugs",
      "files": ["src/search.py"],
      "files_total": 1,
      "kinds": ["dangling"],
      "why_candidate": ["git fsck: dangling commit (unreachable from refs and in no reflog)"],
      "reachable_from": [],
      "confidence": "low",
      "confidence_reasons": [
        "commit object 55158fbb exists and was read successfully",
        "not reachable from any branch, tag, remote-tracking ref or stash",
        "only evidence is that git fsck reports it dangling; no reflog entry explains it (may be old garbage from an amend, a failed rebase or a deleted branch)"
      ],
      "safe_action": "git branch rescue/2026-10-02-55158fbb 55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
      "safe_action_via_warp": "python3 scripts/warp.py rescue preserve 55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
      "...": "parents, unreachable_commit_count, also_unreachable"
    }
  ],
  "deleted_branch_candidates": [],
  "reflog_signals": [],
  "stashes": [],
  "fsck": { "ran": true, "timed_out": false },
  "dangling_objects": { "commits": 1, "trees": 0, "blobs": 0, "...": "blob_samples, note" },
  "refs_examined": 2,
  "truncated": false,
  "unknown": [
    "Edits that were never committed or staged are not stored by git and cannot be recovered from it.",
    "Reflog entries and unreferenced objects can be removed by gc/prune; absence of a candidate does not prove the work never existed.",
    "Evidence shows where commits went, not whether the user meant to discard them."
  ],
  "warnings": []
}
```

Note the honesty in the output: the commit was found only by `git fsck`, so confidence is `low` with the reasons
spelled out, and `deleted_branch_candidates` is empty (the branch was removed with `git update-ref -d`, so there was
no reflog entry naming it).

## 2. `rescue inspect <sha>` (read-only)

```bash
python3 scripts/warp.py rescue inspect 55158fbb --repo <repo>
```

Captured output (trimmed):

```json
{
  "command": "rescue inspect",
  "commit": { "sha": "55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c", "short": "55158fbb", "date": "2026-08-12T10:00:00+10:00", "author": "Ada Dev", "subject": "feat: experimental search over slugs", "...": "parents, body, author_date" },
  "files": ["src/search.py"],
  "files_total": 1,
  "stat": "commit 55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c\nAuthor:     Ada Dev <ada@example.com>\nAuthorDate: Wed Aug 12 10:00:00 2026 +1000\nCommit:     Ada Dev <ada@example.com>\nCommitDate: Wed Aug 12 10:00:00 2026 +1000\n\n    feat: experimental search over slugs\n\n src/search.py | 5 +++++\n 1 file changed, 5 insertions(+)\n",
  "reachable_from": [],
  "unreachable": true,
  "ancestry_vs_head": {
    "is_ancestor_of_head": false,
    "head_is_ancestor_of_commit": false,
    "merge_base": "8f059303069bc623b65a702f9d5ad33082d25526",
    "commits_only_in_head": 3,
    "commits_only_in_candidate": 1,
    "candidate_only_commits": [ { "short": "55158fbb", "subject": "feat: experimental search over slugs" } ],
    "...": "head"
  },
  "head_reflog_mentions": [],
  "safe_action": "git branch rescue/2026-10-02-55158fbb 55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
  "warnings": []
}
```

Re-captured after the redaction hardening (commit `64e23a0`): the `Author:` and `AuthorDate:` lines in `stat` are
readable. (An earlier capture showed `[REDACTED]` there; that bug is fixed.)

## 3. `rescue preserve --dry-run` (changes nothing)

```bash
python3 scripts/warp.py rescue preserve 55158fbb --dry-run --repo <repo>
```

```json
{
  "command": "rescue preserve",
  "dry_run": true,
  "principle": "Preserve first. Investigate second. Mutate last.",
  "status": "dry_run",
  "would_run": "git branch rescue/2026-10-02-55158fbb 55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
  "branch": "rescue/2026-10-02-55158fbb",
  "sha": "55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
  "changed": false,
  "would_not_touch": ["HEAD", "index", "working tree", "any existing ref"],
  "verify": [
    "git rev-parse refs/heads/rescue/2026-10-02-55158fbb",
    "git log --oneline -5 rescue/2026-10-02-55158fbb",
    "git status --short   # unchanged: preserve never touches the working tree"
  ]
}
```

## 4. `rescue preserve` (the only command that creates a ref)

```bash
python3 scripts/warp.py rescue preserve 55158fbb --repo <repo>
```

```json
{
  "command": "rescue preserve",
  "dry_run": false,
  "principle": "Preserve first. Investigate second. Mutate last.",
  "status": "created",
  "ran": "git branch rescue/2026-10-02-55158fbb 55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
  "branch": "rescue/2026-10-02-55158fbb",
  "sha": "55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c",
  "changed": true,
  "verified": true,
  "head_unchanged": true,
  "verify": ["git rev-parse refs/heads/rescue/2026-10-02-55158fbb", "...", "..."],
  "next": [
    "git show --stat rescue/2026-10-02-55158fbb",
    "git switch rescue/2026-10-02-55158fbb   # only when you are ready; this changes your working tree",
    "git cherry-pick 55158fbb   # or copy individual commits onto your current branch"
  ]
}
```

Afterwards `git branch -a` listed `feature/billing` (current), `main` and `rescue/2026-10-02-55158fbb`, and
`git status -sb` still showed the same modified and untracked file as before. Running the same `preserve` again
returned `"status": "already_preserved"`, `"changed": false`. Passing `--name main` returned
`branch 'main' already exists at 8f059303; refusing to overwrite or move it (choose another --name)`.

## ILLUSTRATIVE rendering (not captured model output)

Hand-written example of how `skills/git-rescue/SKILL.md`'s response template could present the scan.

```
STATE
  On feature/billing, no operation in progress, not shallow. Scan examined 2 refs and ran git fsck.

EVIDENCE
  - No reflog signals and no stashes.
  - fsck found 1 dangling commit: 55158fbb "feat: experimental search over slugs" (2026-08-12, 1 file: src/search.py)

LIKELY RECOVERY
  Candidate: 55158fbb "feat: experimental search over slugs" (2026-08-12, 1 file)
  Evidence: git fsck: dangling commit (unreachable from refs and in no reflog)
  Confidence: low - only evidence is that git fsck reports it dangling
  SAFE ACTION: python3 scripts/warp.py rescue preserve 55158fbb8dee8d9ed21ddfc93e1ffc1ddcce448c

RISK
  Dangling objects can be removed by git gc/prune; uncommitted edits are not recoverable.

RECOMMENDATION
  Preserve first, then inspect, then restore. If this is the deleted experiment branch, preserve it.

SAFE NEXT ACTION
  python3 scripts/warp.py rescue preserve 55158fbb --dry-run
```
