---
name: git-pr
description: Build a reviewer-ready pull request analysis from a base-to-HEAD diff, with evidence and explicit limits.
argument-hint: "<base>"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-pr

Analyze the merge base to HEAD file and line diff using read-only Git commands. Return a concise title and rationale, implementation summary, architecture implications, migration/schema/API/dependency/generated-file/lockfile signals, possible secret/debug-output matches, test-path evidence, rollback consideration, reviewer questions, and unresolved questions. Cite changed paths and base/HEAD SHAs. Treat path and pattern matching as a heuristic; do not reproduce suspected secret values, fabricate test results, or claim coverage from filenames. Static review does not prove behavior or mergeability. Do not mutate Git state.
