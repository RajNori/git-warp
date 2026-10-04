---
name: git-bisect-ai
description: This skill should be used when the user wants to find which commit introduced a bug or regression, for example "find the commit that broke this", "bisect this", "when did this test start failing", "which commit caused the regression", "help me write a git bisect test", or "check bisect progress". Plans a safe bisect (never starts one or runs tests itself), helps write a deterministic pass/fail predicate, and reads bisect status.
argument-hint: "--good REF --bad REF [--test CMD] | status"
allowed-tools: Read, Grep, Glob, Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(git status:*), Bash(git rev-parse:*), Bash(git bisect log:*), Bash(git worktree list:*)
---

# git-bisect-ai

> Run `warp.py` from the repository directory: `--repo` defaults to the current directory. Do **not** pass `--repo` a shell variable or any other shell expansion: Claude Code cannot analyse it statically and would prompt even though `warp.py` is pre-approved. If you must name another repository, use a literal absolute path.

Locate a regression with evidence. Git Warp plans and validates; the user (or you, with their approval) runs git's own bisect. Git Warp NEVER starts a bisect and NEVER executes the test command.

## Workflow

1. Establish refs. Ask for a known-good ref (tag, release, commit date you can look up with `git log --before=...`) and a known-bad ref (default HEAD). Confirm the symptom in one sentence and what "good" looks like.
2. Plan (read-only):
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" bisect plan --good <ref> --bad <ref> [--test "<cmd>"]`
3. Read the result:
   - `ready: false`: explain each `blockers[]` entry (dirty tree, operation in progress, good not an ancestor of bad, unknown ref, invalid test) and give its `suggest` options (`git stash push -u`, or an isolated worktree). Do not work around a blocker.
   - `range`: commit count and expected steps; merge commits mean non-linear history (suggest `--first-parent` first, then bisect inside the merge).
   - `reproducibility_risks`: lockfile/manifest/migration/schema changes across the range. The predicate must reinstall dependencies or reset the database per step, and exit 125 for unbuildable commits.
   - `warnings`: shallow clone, untracked files, submodules.
4. Help write the predicate (`references/bisect-predicates.md`). Save it OUTSIDE the work tree (for example the session scratchpad or a temporary directory; never a directory on PATH), make it deterministic, and dry-run it on the good and bad refs in the isolated worktree before bisecting. Exit codes: 0 good, 1-124/126/127 bad, 125 skip, 128-255 abort.
5. Present `commands[]` exactly as returned (worktree, `git bisect start`, `git bisect run`, `git bisect log`, `git bisect reset`, cleanup). Run them only with the user's approval, inside the worktree, never in a dirty tree. If no reliable predicate exists (flaky, needs a human or a UI), use manual `git bisect good|bad|skip` and say which commits you could not test.
6. Check progress or finish with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" bisect status --repo <worktree>`.
7. When `phase` is `finished`: read the culprit's diff (`git show <sha>`), explain causality carefully (what changed vs the symptom), list `follow_up_commits_touching_same_files`, and mention `culprit_caveat` if skips happened. **Do not revert, fix, or reset automatically.** Offer options (fix forward, revert, report) and let the user choose. Remind them to `git bisect reset`.

## Response template

```
STATE
  <refs, range size, tree state, bisect in progress or not>

EVIDENCE
  <plan facts: expected steps, merges, changed risk files, predicate validation>

RISK
  <reproducibility risks, merge ambiguity, flakiness, shallow history>

WHY
  <why the plan is shaped this way (worktree isolation, predicate exit codes)>

RECOMMENDATION
  <ready to run / fix blockers first / bisect manually>

SAFE NEXT ACTION
  <one command, or the single decision the user must make>
```

After a finished bisect add: `CULPRIT: <sha8> <subject> (fact)`, `CAUSALITY: <explanation> (inference unless proven by test)`, `OPTIONS: fix forward | revert | investigate` (no automatic action).

Never recommend `git reset --hard`, `git clean`, or `git stash drop` as part of bisect cleanup.
