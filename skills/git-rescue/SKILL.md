---
name: git-rescue
description: Recover lost commits, branches, stashes, or overwritten work using reflog and Git object history.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-rescue

Treat recovery as forensics. Never run gc, prune, reflog expire, reset --hard, clean -f, or destructive restore. Preserve evidence, inspect reflog --all, validate candidate commits with git show, prefer creating rescue/<name> branches, and verify recovered content before cleanup.
