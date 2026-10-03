---
name: git-conflict
description: Inspect unresolved Git index stages, merge bases, branch commits, and blame evidence without resolving conflicts.
argument-hint: "[base]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-conflict

Read unmerged index entries and report available base/ours/theirs stage object IDs and bounded excerpts. When given a branch or revision, report the merge base, commits unique to each side, and bounded blame SHAs for affected paths. State explicitly that history evidence does not determine which side is semantically correct; mark resolution semantics unknown and provide no automatic resolution proposal. Explain missing stages for add/delete conflicts. Never write the index, worktree, refs, or repository configuration.
