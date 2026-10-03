---
name: git-bisect-ai
description: Guide an evidence-driven git bisect to locate a regression, optionally constructing a deterministic test command.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-bisect-ai

Run the read-only bisect preflight before recommending a bisect. Confirm both refs resolve to commits, good is an ancestor of bad, the endpoints differ, and the index/worktree are clean. Check for existing bisect markers and interrupted rebase state as well; resolve an existing operation deliberately before beginning another investigation. The preflight never starts or modifies a bisect session.

Translate the symptom into one exact predicate command with a stable environment and explicit success/failure output. Avoid network dependencies, timestamps, randomness, mutable external services, and commands whose result varies by machine. Run it manually on the known-good and known-bad commits first. For `git bisect run`, exit 0 marks good, 1–124 marks bad, 125 skips an untestable commit, and 126/127 or 128+ aborts. If the predicate is flaky or setup varies between revisions, test commits manually and report uncertainty instead of automating it.

Only after the human approves should a bisect session be started. When a culprit is found, inspect the introducing diff and nearby dependencies, distinguish observed facts from causal inference, and propose fix/revert options. Never start, reset, or conclude a bisect automatically, and never revert automatically.
