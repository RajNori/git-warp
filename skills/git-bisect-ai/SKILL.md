---
name: git-bisect-ai
description: Guide an evidence-driven git bisect to locate a regression, optionally constructing a deterministic test command.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-bisect-ai

Ensure the working tree is safe; establish good and bad refs; translate the symptom into a deterministic pass/fail test; use git bisect run only when reliable. Handle flaky failures manually. When the culprit is found, inspect the introducing diff, explain causality carefully, identify dependent commits, and propose fix/revert options without automatically reverting.
