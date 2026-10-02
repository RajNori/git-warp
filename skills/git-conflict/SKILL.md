---
name: git-conflict
description: This skill should be used when the user hits a merge, rebase, cherry-pick or revert conflict, says "resolve this conflict", "what does this conflict mean", "which side should I keep", "explain these conflict markers", "ours vs theirs", or git reports "CONFLICT" or "Unmerged paths". Analyses both sides with ancestry and proposes a resolution without touching files.
argument-hint: ""
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Read, Grep, Glob, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git blame:*), Bash(git ls-files:*), Bash(git rev-parse:*), Bash(git merge-base:*)
---

# git-conflict: conflict ancestry and proposed resolution

Default mode is **analysis and a proposed resolution only**.

## 1. Run

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" conflict
```

If `conflicts` is empty, tell the user there is no active conflict (`message`) and stop. Otherwise read: `operation`, `swap_note`, `ours`/`theirs`, `merge_base`, and per file `conflict_type`, `stages`, `regions` (line ranges, ours/theirs/base text, blame), `history` (commits per side), `relationship_hint`. Field reference: `references/conflict-fields.md`.

## 2. Interpret

1. State which side is which. **During a rebase ours and theirs are swapped**: ours is the upstream you are rebasing onto, theirs is your own commit being replayed. Say this explicitly whenever `operation` is `rebase`.
2. For each file and region, read the commits listed in `history` (use `git show <sha> -- <path>` read-only) so you know the *intent* of each side, not just the text.
3. `relationship_hint` is a deterministic hint, not a verdict. Classify yourself: **compatible** (both can be kept), **contradictory** (mutually exclusive intents), **independent** (separate concerns that happened to touch the same lines) or **unclear**.
4. Delete/modify, add/add, rename and binary conflicts need a decision about intent: say what each side did and what the user must choose.

## 3. Output template

```
CONFLICT: <operation>, <N files, M regions>
<path> (<conflict_type>), region k at lines a-b
  OURS (<what ours is>): <summary of the change + commit subject/sha>
  THEIRS (<what theirs is>): <summary + commit subject/sha>
  BASE: <what both started from, if known>
  SEMANTIC RELATIONSHIP: <compatible | contradictory | independent | unclear>. <evidence from commits/blame>. (script hint: <relationship_hint>)
  PROPOSED RESOLUTION: <the exact resulting text, or which side to keep and why>
  CONFIDENCE / WHAT TO VERIFY: <tests to run, callers to check>
SAFE NEXT ACTION: <e.g. "Tell me to apply this resolution to <path>.">
```

## 4. Hard rules

- **Do not edit conflict files** unless the user explicitly asks you to apply a resolution. Then edit only the named files, remove every marker, re-read the result, and show `git diff`.
- **Never run `git checkout --ours/--theirs`, `git add`, `git merge --abort`, `git rebase --continue/--abort`, `git restore` or `git reset` silently.** Staging resolved files and continuing is the user's call; propose the commands as text.
- Never silently resolve an uncertain conflict. If the relationship is `unclear` or `contradictory`, present options and ask.
- Do not trust the markers alone: line endings (CRLF) and diff3 base sections are handled by the script; binary files cannot be merged textually.
