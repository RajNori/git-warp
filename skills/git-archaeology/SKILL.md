---
name: git-archaeology
description: Explain how and why a file, symbol, architecture decision, or bug evolved through Git history.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-archaeology

Reconstruct history using git log --follow, -S/-G, blame, show, diff, merge-base, and first-parent history as appropriate. Build a timeline of introduction, rewrites, fixes/reverts, and architectural changes. Attribute motives only when supported by evidence. End with the smallest set of commits a developer should read.
