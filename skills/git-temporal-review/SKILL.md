---
name: git-temporal-review
description: This skill should be used when the user asks for a "temporal review", "has this been tried before", "was this code removed before", "is this a regression", "why was this removed", "review my change against history", or before merging a change that re-adds deleted code, removes old fixes or toggles dependencies. Collects historical evidence (earlier removals, fix commits, reverts, dependency flip-flops) and warns only with cited commits.
argument-hint: "[--base REF] [--limit N]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Read, Grep, Glob, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git blame:*), Bash(git ls-files:*), Bash(git rev-parse:*), Bash(git merge-base:*)
---

# git-temporal-review: does history warn against this change?

## 1. Run

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" temporal [--base REF] [--limit N]
```

No `--base`: working tree vs HEAD (including untracked). `--base REF`: branch diff `REF...HEAD` (commits already on the branch are excluded from "history"). Output reference: `references/evidence-kinds.md`.

## 2. Verify before you warn

Each finding has `kind`, `evidence` (commit shas, subjects, dates), `match_quality` (exact-line, identifier, heuristic), `signals` and a `caveat`. **String similarity is not semantic equivalence.** For every finding you intend to report:
1. Run `git show <sha> -- <file>` (read-only) and read the full removal/addition diff and the commit message.
2. Read the current change (`git diff`) for the same area.
3. Decide: same intent and same risk, or different context (renamed, refactored, guard now elsewhere)? Grep the current tree for the replacement before claiming a guard is missing.
4. Drop findings you cannot support.

**No warning without cited commits.** If `findings` is empty, say no historical evidence was found within the probe budget (give `probes.used/budget`), that this is not proof of safety, and do not invent a risk. Shallow clones and the probe budget limit what can be found; report `warnings`.

## 3. Output template

```
TEMPORAL WARNING: <one sentence, only if verified>   (or: NO TEMPORAL EVIDENCE FOUND)
Related historical removal:
  <sha8> <date> "<subject>"  removed  <file>: <what was removed>
Current change:
  <file>:<line> <what is being added/removed now>
Historical evidence:
  - <why the commit mattered: message, signals such as race/security/revert, linked issue>
  - match quality: <exact-line | identifier | heuristic>; verified by reading <sha> diff
Relevant commits:
  <sha8> <subject>  (cited above)
RECOMMENDATION: <re-check X / keep the guard / add a test for the old failure>
SAFE NEXT ACTION: <read-only command or test to run>
```

Give one block per verified finding, strongest first. Never claim a regression you did not verify.
