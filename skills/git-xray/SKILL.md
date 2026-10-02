---
name: git-xray
description: This skill should be used when the user asks to "x-ray the repo", "run git xray", "what changed", "what's risky here", "is this branch safe", "what is uncommitted", "audit my repository", "check repo health", or wants a read-only Git state and risk analysis before committing, rebasing, merging or pushing.
argument-hint: "[--untracked-all] [--repo PATH]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Read, Grep, Glob, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git branch --list:*), Bash(git branch --show-current:*), Bash(git branch -vv:*), Bash(git stash list:*), Bash(git worktree list:*), Bash(git reflog show:*)
---

# Git X-Ray

Read-only diagnosis of the current repository. Git Warp gathers deterministic evidence as JSON; you verify and explain it. Never mutate the repository: suggest commands, do not run mutating ones (no add/commit/reset/stash/checkout/rebase/merge/push/clean).

## Step 1: gather evidence

Run exactly one command (add `--untracked-all` only if the user asks to see every untracked file):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" xray $ARGUMENTS
```

The output is JSON. If it contains `"error"` (not a repository, bad path), report that error and the `hint` and stop. Surface every entry in `warnings` (shallow clone, timeouts, truncated scans) as a caveat; partial results are still valid.

Key sections: `state`, `working_tree`, `recent_commits`, `high_churn`, `sensitive_paths`, `signals`, `clusters`, `recovery`, `risk`, `recommendation`. Field details: `references/output-fields.md`. Risk rules: `references/risk-rules.md`.

## Step 2: verify before asserting

The JSON states facts; intent is yours to infer, so check it. Read the 2 to 4 files or hunks that matter most (the HIGH drivers, sensitive paths, migrations, conflicted files) with Read or `git diff -- <path>`. Do not read the whole diff. Secret findings carry only `path:line` and `[REDACTED]`; open the line to judge whether it is real, but never print the secret value in your answer. Treat heuristic flags (`potential_missing_tests`, `mixed_concerns`, lockfile drift) as leads, and say "heuristic" when you rely on one without checking.

## Step 3: render

Use this template. Keep it concise; omit nothing from the header blocks, drop empty bullets elsewhere.

```
GIT WARP — X-RAY

STATE
Branch:      <state.branch | detached at head_short | unborn>
Upstream:    <state.upstream | none>
Ahead:       <ahead | n/a>
Behind:      <behind | n/a>
Dirty paths: <staged + unstaged + untracked + conflicted counts, total>
In progress: <operation.type or none>   Stashes: <n>   Worktrees: <n>   Shallow: <yes/no>

RISK: <risk.level>
WHY
- <each risk.drivers entry, verbatim evidence counts>

CHANGE CLUSTERS
1. <label> (<kind>, <file_count> files, +added/-deleted): <one-line purpose, only if verified>
   ... mention atomic_commit_opportunities when there are 2+ clusters

SIGNALS
- <only the non-empty ones: migrations, schema, config/infra/CI, dependency drift, secrets (path:line, REDACTED), potential missing tests with the reasoning, high-churn files touched>

RECOVERY
- Reflog: <available, n entries> | Stashes: <n> | Worktrees: <n> | Dangling commits: not checked (use /git-rescue if work looks lost)

RECOMMENDATION
1. <prose for each recommendation id, ordered; see references/recommendation-ids.md>

SAFE NEXT ACTION
<one read-only command, e.g. the first recommendation's `inspect` command>
```

Example header for a diverged branch with a conflict:

```
STATE
Branch: feature/auth-refactor
Upstream: origin/feature/auth-refactor
Ahead: 3
Behind: 2
Dirty paths: 7 (1 conflicted)

RISK: HIGH
WHY
- HIGH: 1 conflicted path(s) need resolution
- MEDIUM: branch diverged from upstream: 3 ahead, 2 behind
```

## Rules

- Report only what the JSON or your own reads show. No generic advice ("consider best practices") and no invented numbers; risk is LOW/MEDIUM/HIGH only.
- State the limits you hit: `truncated: true` lists, shallow history, untracked directories collapsed, secrets scan covering only added lines.
- Never claim tests passed or were run. X-Ray does not run tests.
- Never echo a secret value, token or key, even if you read it from a file.
- Destructive or history-rewriting commands may appear only as options for the user to run, with the risk named; the SAFE NEXT ACTION is always read-only.
