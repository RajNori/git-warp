---
name: git-xray
description: Perform a deep Git repository health and risk analysis. Use when the user asks what changed, whether a branch is safe, what is risky, what is uncommitted, or wants a repository audit.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-xray

Act as a senior Git diagnostician. Analyze without mutating the repository. Inspect branch/upstream state, staged/unstaged/untracked files, commit graph, reflog, worktrees, stashes, high-churn files, sensitive paths, likely missing tests, and mixed-concern commits. Return an executive summary, risk map, suggested atomic commit boundaries, available recovery points, and exact non-destructive next commands.
