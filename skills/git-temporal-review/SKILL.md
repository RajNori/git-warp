---
name: git-temporal-review
description: Review selected or changed path history with full commit SHA evidence and conservative revert/reintroduction signals.
argument-hint: "[paths...]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-temporal-review

Inspect path-specific commit history and report subjects with full commit SHAs. Flag explicit revert references and reintroduction/restore wording as possible signals, citing both the signal SHA and any referenced target SHA. Commit wording is not proof that behavior was fully reverted or restored. Use recency only as a descriptive history measure, not a defect-probability claim. State limits from the history cap, shallow clones, and rewritten history. This is read-only; do not alter refs, index, or worktree.
