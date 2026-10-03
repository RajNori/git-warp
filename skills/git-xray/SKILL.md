---
name: git-xray
description: Perform a read-only Git repository health and change analysis with checkout, path, and commit evidence. Use when the user asks what changed, what is risky, what is uncommitted, or wants a repository audit.
argument-hint: "[base]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-xray

Resolve nested input directories to the owning repository root. Report branch/detached HEAD, upstream and ahead/behind counts when configured, staged/unstaged/untracked paths, stashes, worktrees, reflog availability, recent commits, and historical path hotspots. Classify changed paths for sensitive, migration, infrastructure, lockfile, tests, dependency, generated, and API/schema signals. Inspect added diff lines for common secret-shaped values and debug output without reproducing matched values. Propose possible atomic boundaries with the paths that support them. Label all path/content heuristics and state history/status limits. A clean worktree is not proof that a repository is healthy. Never change refs, index, worktree, config, or repository files.
