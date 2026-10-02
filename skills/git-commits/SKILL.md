---
name: git-commits
description: Propose groupings for current staged/unstaged diff hunks or existing commits using transparent overlap heuristics.
argument-hint: "[revision]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-commits

By default inspect staged and unstaged diff hunks and propose related groups using changed-line tokens and path overlap. When a revision is supplied, group existing commits using subject-token and changed-path overlap. Cite hunk ranges or full commit SHAs. Explain the heuristic and its uncertainty. Proposals are for human review; do not stage, unstage, rewrite, reorder, squash, amend, reset, or commit anything. Untracked file contents are not clustered unless represented in a tracked diff.
