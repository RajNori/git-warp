---
name: git-rescue
description: This skill should be used when the user says they lost work in Git, for example "I lost my commits", "I ran git reset --hard", "I deleted a branch", "recover my stash", "my rebase went wrong", "I force-pushed over something", "commits disappeared after checkout", "work on detached HEAD is gone", or asks to find or restore dangling/unreachable commits. Gathers deterministic reflog and fsck evidence, ranks recovery candidates, and preserves one safely on a new branch.
argument-hint: "[what was lost | --since \"2 hours ago\" | --grep TEXT | --path FILE]"
allowed-tools: Read, Grep, Glob, Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Bash(git log:*), Bash(git show:*), Bash(git reflog show:*), Bash(git status:*), Bash(git diff:*), Bash(git rev-parse:*), Bash(git cat-file:*), Bash(git stash list:*)
---

# git-rescue

Recovery principle: **Preserve first. Investigate second. Mutate last.**

Git rarely deletes work immediately. Commits that look gone are usually still in the reflog or dangling as unreachable objects. Pin them with a new branch before doing anything else.

## Hard rules

- Never recommend or run `git reset --hard`, `git clean`, `git gc`, `git prune`, `git reflog expire`, `git checkout -- .`, `git restore` over changes, or `git push --force`. Do not run `git fsck --lost-found` (it writes into `.git`).
- The only mutation allowed is `warp.py rescue preserve`, which creates a NEW branch and refuses to overwrite one. It never touches HEAD, the index or the working tree.
- Never state uncertain evidence as certainty. Use the `confidence` label and its `confidence_reasons` verbatim in spirit: "high", "medium", "low" are labels with reasons, not probabilities.
- Never claim uncommitted, never-staged edits can be recovered. Git does not store them.

## Workflow

1. Gather evidence (read-only):
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" rescue scan --repo "$PWD"`
   Narrow with `--since "2 hours ago"`, `--grep TEXT`, `--path PATH`; add `--no-fsck` if it is slow (and say stored dropped stashes will then be missed). If the user describes a time window or a file, use those filters.
2. Read `state` (branch, detached, operation in progress, shallow) and `warnings` first. If an operation is in progress, tell the user before judging what is lost.
3. Rank `candidates`: highest `confidence` first, then most recent. Match the user's description (subject, files, time) to candidates; ask one question if several fit.
4. Inspect the best one (read-only):
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" rescue inspect <sha> --repo "$PWD"`
   Check `files`, `stat`, `ancestry_vs_head`, and `candidate_only_commits`.
5. Offer preservation. Show the dry run first when the user wants to see it:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" rescue preserve <sha> --dry-run --repo "$PWD"`
   Run the real `preserve` only after the user agrees (or already asked you to save it). Then show the `verify` commands.
6. Restoration into the working tree (switch to the branch, cherry-pick, or copy files) is a separate step: explain options, let the user choose, and prefer `git cherry-pick`/`git switch` on the preserved branch over anything destructive.

Detailed scenario playbooks (accidental reset, deleted branch, bad rebase, dropped stash, detached-HEAD work, overwritten force-push) are in `references/rescue-scenarios.md`. Read it when the scenario needs more than the scan output provides.

## Response template

```
STATE
  <branch/detached, operation in progress, shallow, what the scan examined>

EVIDENCE
  <facts from the scan: reflog entries, fsck result, ORIG_HEAD, stashes>

LIKELY RECOVERY
  Candidate: <sha8> "<subject>" (<timestamp>, <N> files)
  Evidence: <why_candidate items>
  Confidence: <high|medium|low> - <confidence_reasons>
  SAFE ACTION: git branch rescue/<date>-<sha8> <sha>
               (or: python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" rescue preserve <sha>)
  (repeat for further candidates, at most 3-4)

RISK
  <what could still be lost: gc/prune timing, uncommitted work, shallow clone>

WHY
  <how the work became unreachable, in one or two sentences>

RECOMMENDATION
  <preserve first, then inspect, then restore; name the candidate>

SAFE NEXT ACTION
  <exactly one command>
```

If `candidates` is empty, say so plainly, list what was searched (HEAD and branch reflogs, dangling commits, ORIG_HEAD, stashes), name the limits from `unknown`, and suggest checking other clones, CI artifacts, editor local history, or the remote.
