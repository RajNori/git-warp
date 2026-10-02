---
name: git-pr
description: Turn the current branch into a high-quality PR package: review the diff, identify risk, propose atomic commits, and generate reviewer guidance.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-pr

Analyze base...HEAD. Produce PR title, rationale, implementation summary, architecture implications, risk areas, migrations/config changes, testing evidence, rollback plan, reviewer focus areas, unresolved questions, and commit-quality assessment. Detect unrelated changes, secrets, debug logging, missing tests, lockfile drift, migrations, and API/schema breaks. Never fabricate test results.
