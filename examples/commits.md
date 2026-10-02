# Example: Semantic Commit Composer

Command (captured 2026-10-02, on the dirty working tree: one modified file and one untracked file):

```bash
python3 scripts/warp.py commits --repo <repo>
```

## Captured output (trimmed)

`"...": "..."` marks removed keys or items; the full output was about 3.5 KB.

```json
{
  "repo": "<repo>",
  "mode": "working-tree",
  "state": { "branch": "feature/billing", "detached": false, "unborn": false, "shallow": false, "operation": null, "...": "head" },
  "summary": { "files": 2, "clusters": 2, "added": 2, "deleted": 1, "staged": 0, "untracked": 1 },
  "files": [
    { "path": "notes.txt", "status": "?", "added": 1, "deleted": 0, "binary": false, "staged": false, "unstaged": true, "tags": ["docs"], "cluster": 1 },
    { "path": "src/app.py", "status": "M", "added": 1, "deleted": 1, "binary": false, "staged": false, "unstaged": true, "tags": ["source"], "cluster": 2 }
  ],
  "proposals": [
    {
      "cluster": 2,
      "label": "root source",
      "kind": "source",
      "type": "feat|fix?",
      "message": "feat|fix?: <describe the change>",
      "needs_type_decision": true,
      "files": ["src/app.py"],
      "order": 1,
      "commands": [
        "git add -- src/app.py",
        "git diff --cached --stat",
        "git commit -m 'feat|fix?: <describe the change>'"
      ],
      "order_reason": "implementation (with its tests when paired)",
      "...": "added, deleted"
    },
    {
      "cluster": 1,
      "label": "docs",
      "kind": "docs",
      "type": "docs",
      "message": "docs: <describe the change>",
      "needs_type_decision": false,
      "files": ["notes.txt"],
      "order": 2,
      "commands": ["git add -- notes.txt", "git diff --cached --stat", "git commit -m 'docs: <describe the change>'"],
      "order_reason": "docs last, describing the final state",
      "...": "added, deleted"
    }
  ],
  "mixed_concerns": [],
  "hunks": {},
  "flags": { "secrets": [], "generated": [], "conflicted": [], "binary": [] },
  "notes": [
    "NOTHING WAS STAGED OR COMMITTED. These commands are proposals for the user; run one cluster at a time and verify `git diff --cached --stat` after each `git add`.",
    "Cluster labels, commit types and message summaries are suggestions: review the diffs and decide feat vs fix, merge or split clusters.",
    "Commit messages contain <placeholders>; fill them in before running."
  ],
  "warnings": [],
  "...": "clusters, commands (the same command lists grouped by step)"
}
```

The commands are text only. `git status -sb` after the run was unchanged (`M src/app.py`, `?? notes.txt`). The
message skeletons are deliberately unfinished (`feat|fix?`, `<describe the change>`): choosing the type and writing
the summary is the model's job in the skill, and `needs_type_decision` marks where.

## ILLUSTRATIVE rendering (not captured model output)

```
STATE: feature/billing, 2 files, +2/-1, staged 0 / unstaged 1 / untracked 1, no operation in progress
GROUPS:
  1. root source (source) src/app.py : files at the repository root
  2. docs (docs) notes.txt : documentation files
PROPOSED COMMITS (in order):
  1. feat(pages): prefix generated page paths with /pages/   <- example wording, chosen after reading the diff
     files: src/app.py
     why this order: implementation first
     commands (NOT RUN):  git add -- src/app.py  |  git commit -m "..."
  2. docs: add working notes
     files: notes.txt
     why this order: docs last
WARNINGS: none (no secrets, generated or conflicted files)
SAFE NEXT ACTION: Say "commit group 1" and I will stage and commit only that group.
```
