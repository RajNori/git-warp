---
name: git-archaeology
description: Explain how and why a file, symbol, architecture decision, or bug evolved through Git history.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-archaeology

Use the history analysis service to collect a bounded timeline for the requested path and/or commit-message query. For a focused symbol or behavior, supplement it with read-only `git log --follow`, `git log -S'<text>'`, `git log -G'<regex>'`, `git blame`, `git show`, and `git diff` queries as appropriate. Keep commands argv-safe and put path arguments after `--`.

Separate conclusions by certainty: `FACT` for observed Git output (commit IDs, dates, changed paths, lines, and parent relationships), `INFERENCE` for a plausible interpretation of a sequence, and `UNKNOWN` where the repository evidence cannot establish the answer. Commit messages and blame identify recorded history; they do not prove author intent or causation. Say when shallow history, renames, generated files, or missing refs limit the account.

Build a concise timeline of introduction, rewrites, fixes/reverts, and architectural changes, citing the supporting commit IDs and commands. Attribute motives only when directly supported by repository evidence or an explicitly cited source. End with the smallest set of commits a developer should read.
