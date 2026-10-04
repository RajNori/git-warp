# Example: AI Bisect plan and Conflict Surgeon

Captured 2026-10-02.

## `bisect plan` on a dirty working tree (blocked)

```bash
python3 scripts/warp.py bisect plan --good main --bad HEAD --test "python3 -m pytest -q" --repo <repo>
```

Trimmed:

```json
{
  "command": "bisect plan",
  "executed": false,
  "note": "Planning only: Git Warp never starts a bisect or runs your test command.",
  "ready": false,
  "refs": {
    "bad": { "input": "HEAD", "sha": "afef50ed22947c6ae56a6cf33adbd7600f18e625" },
    "good": [ { "input": "main", "sha": "8f059303069bc623b65a702f9d5ad33082d25526" } ]
  },
  "range": {
    "commits_in_range": 3,
    "expected_steps": 2,
    "steps_note": "about ceil(log2(3)) = 2 test runs; skips and merges can add a few",
    "merge_commits": 0,
    "files_changed_in_range": 5,
    "...": "merge_commit_samples"
  },
  "blockers": [
    {
      "code": "dirty_working_tree",
      "message": "1 tracked file(s) have uncommitted changes; bisect checks out other commits and would clobber or be confused by them",
      "files": ["src/app.py"],
      "suggest": [
        "git stash push -u -m 'before bisect'   # then `git stash pop` after `git bisect reset`",
        "git worktree add --detach <repo>-bisect-afef50ed afef50ed22947c6ae56a6cf33adbd7600f18e625   # or bisect in an isolated worktree, leaving this tree untouched"
      ]
    }
  ],
  "reproducibility_risks": [
    { "category": "lockfile", "files": ["package-lock.json"], "why": "dependency lockfiles changed across the range: the installed dependency set must be rebuilt per step or old commits will run against new libraries" },
    { "category": "manifest", "files": ["package.json"], "why": "dependency manifests changed across the range: reinstall dependencies inside the predicate" },
    { "category": "migration", "files": ["migrations/0002_add_invoices.sql"], "why": "database migrations changed across the range: persistent DB state will not match each commit; recreate the DB per step" }
  ],
  "test": {
    "provided": true,
    "valid": true,
    "issues": [],
    "echo": "python3 -m pytest -q",
    "executed": false,
    "note": "Git Warp only validated this string syntactically; it was NOT executed."
  },
  "commands": null,
  "...": "state, warnings, isolation, predicate_guidance"
}
```

## `bisect plan` on a clean working tree (ready)

In a second copy of the demo repository with the uncommitted work committed, the same command returned
`"ready": true`, `"blockers": []` and these `commands` (paths shortened; the order and `purpose` text are verbatim):

```json
[
  { "step": 1, "run": "git worktree add --detach <repo>-bisect-7197a329 7197a3294bc729df2e07c7f6f9f689090455aca9", "purpose": "create an isolated worktree at the bad commit" },
  { "step": 2, "run": "cd <repo>-bisect-7197a329", "purpose": "work inside the worktree" },
  { "step": 3, "run": "git bisect start 7197a3294bc729df2e07c7f6f9f689090455aca9 8f059303069bc623b65a702f9d5ad33082d25526", "purpose": "start bisect (optionally add --first-parent when merges dominate)" },
  { "step": 4, "run": "git bisect run sh -c 'python3 -m pytest -q'", "purpose": "let git drive the search with your deterministic predicate (YOU run this; Git Warp does not)" },
  { "step": 5, "run": "git bisect log", "purpose": "review the decisions and the first bad commit" },
  { "step": 6, "run": "git bisect reset", "purpose": "leave bisect mode" },
  { "step": 7, "run": "cd - && git worktree remove <repo>-bisect-7197a329", "purpose": "remove the temporary worktree (the culprit commit stays in history)" }
]
```

The plan also carried `predicate_guidance` with the exit-code meanings (`0` good, `1-124, 126, 127` bad, `125`
skip, `128-255` abort). None of these commands were run.

## `bisect status` with no bisect in progress

```json
{
  "command": "bisect status",
  "executed": false,
  "in_progress": false,
  "message": "no bisect in progress in this worktree",
  "hint": "python3 scripts/warp.py bisect plan --good REF --bad REF",
  "...": "state, warnings"
}
```

## `conflict` during a real merge conflict

A tiny separate repository: `main` and a branch `side` both changed line 2 of `f.txt`, then `git merge side`
stopped with a conflict.

```bash
python3 scripts/warp.py conflict --repo <repo>
```

Trimmed:

```json
{
  "state": { "branch": "main", "operation": "merge", "...": "head, detached, unborn, shallow" },
  "operation": "merge",
  "swap_note": null,
  "ours": { "ref": "HEAD", "meaning": "HEAD (the branch you are on)", "...": "sha" },
  "theirs": { "ref": "MERGE_HEAD", "meaning": "MERGE_HEAD (the branch being merged in)", "...": "sha" },
  "conflicts": [
    {
      "path": "f.txt",
      "conflict_type": "both-modified",
      "stage_meaning": { "1": "base (merge-base version)", "2": "ours", "3": "theirs" },
      "line_endings": "lf",
      "regions": [
        {
          "index": 1,
          "start_line": 2,
          "end_line": 6,
          "ours_label": "HEAD",
          "theirs_label": "side",
          "ours": { "text": "MAIN", "line_count": 1 },
          "theirs": { "text": "SIDE", "line_count": 1 },
          "has_diff3_base": false,
          "relationship_hint": {
            "hint": "overlapping",
            "basis": "both sides changed the same lines differently",
            "note": "deterministic hint only; Claude must judge compatible / contradictory / independent / unclear by reading both sides"
          },
          "blame": {
            "ours": [ { "summary": "main change", "author": "t", "lines": 1, "...": "sha" } ],
            "theirs": [ { "summary": "side change", "author": "t", "lines": 1, "...": "sha" } ]
          },
          "...": "ours_range, theirs_range, base_range, base"
        }
      ],
      "region_count": 1,
      "history": {
        "ours": [ { "short": "1b1c5935", "subject": "main change", "...": "sha, author, date" } ],
        "theirs": [ { "short": "c383a3dc", "subject": "side change", "...": "sha, author, date" } ]
      },
      "...": "stages, ours_label, theirs_label, parse_warnings, relationship_hint"
    }
  ],
  "summary": { "files": 1, "regions": 1 },
  "read_only": true,
  "notes": ["Analysis only: nothing was edited, staged or checked out. Resolving means editing the file and `git add`, which only happens if the user asks."],
  "warnings": [],
  "...": "repo, merge_base, pick_base, rebase"
}
```
