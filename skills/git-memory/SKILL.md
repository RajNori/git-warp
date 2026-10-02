---
name: git-memory
description: Query Git Warp's local, incremental history index for hotspots, co-changing files, and commit history.
argument-hint: "[hotspots|cochanges <path>|history <path>]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-memory

Use the local Git history index to answer repository-memory questions. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/git_memory.py" hotspots` for churn hotspots, `cochanges <path>` for files that often change together, and `history <path>` for indexed commit history. The CLI incrementally indexes commit metadata and paths under the Git common directory's `.git/git-warp/warp.db`; it does not store source contents or prompts. State the number of commits indexed when the result says work remains. Treat co-change as historical association, not present ownership or proof of dependency. Cite commit SHAs and paths, and say when the index has no evidence.
