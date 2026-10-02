---
name: git-commits
description: This skill should be used when the user asks to "split my changes into commits", "group my changes", "what should I commit", "compose commits", "make atomic commits", "organize my working tree", "write commit messages for my changes", or has a large mixed set of staged/unstaged/untracked changes. Analyses the change set into semantic groups and proposes an ordered, Conventional Commits plan without staging or committing anything.
argument-hint: "[--staged]"
allowed-tools: Bash(python3:*), Read, Grep, Glob, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git blame:*), Bash(git ls-files:*), Bash(git rev-parse:*), Bash(git merge-base:*)
---

# git-commits: semantic commit composer

Default flow is **ANALYZE, PROPOSE, EXPLAIN**. This skill never stages, commits, restores or edits files on its own.

## 1. Gather evidence

Run (read-only):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" commits [--staged]
```

Use `--staged` only if the user wants to review what is already in the index. The JSON contains `files`, `clusters`, `proposals` (ordered, each with `message` skeleton and `commands`), `mixed_concerns`, `hunks`, `flags` (secrets, generated, conflicted, binary), `warnings`. If `error` is present, report it plainly and stop. Field details: `references/output-format.md`.

## 2. Interpret (this is your job, the script only groups heuristically)

- Read the diffs (`git diff -- <path>`, `git diff --cached`) of non-trivial clusters. Rename or merge clusters when the intent is one logical change; split when one cluster hides two intents.
- Decide `feat` vs `fix` vs `refactor` etc. wherever `needs_type_decision` is true; replace every `<placeholder>` with a real, specific summary (imperative mood, under about 72 characters).
- For `hunks` entries with `split_candidate`, read the hunk headers and decide whether the hunks are independent; if so propose `git add -p -- <path>` with which hunks go where.
- Check ordering: dependencies and schema/migrations first, then code with its tests, then UI/infra, docs last. Adjust if the real dependency direction differs.

## 3. Output template

```
STATE: <branch, N files, +A/-D, staged S / unstaged U / untracked T, any in-progress operation>
GROUPS:
  1. <label> (<kind>) <files> : <one-line reason>
PROPOSED COMMITS (in order):
  1. <type(scope): subject>
     files: ...
     why this order: ...
     commands (NOT RUN):  git add -- ...   |   git commit -m "..."
WARNINGS: <secrets excluded, generated files, mixed-concern files, partially staged files, conflicts>
RECOMMENDATION: <what you would do and why>
SAFE NEXT ACTION: <e.g. "Say 'commit group 1' and I will stage and commit only that group.">
```

## 4. Hard rules

- **Never run `git add`, `git commit`, `git restore`, `git reset` or `git stash` unless the user explicitly asks** to stage/commit.
- When asked: do **one cluster at a time**. Run the cluster's `git add -- <paths>` (never `git add -A` or `.`), then `git diff --cached --stat` and check it lists exactly the intended files; show the user, then commit with the agreed message. Re-run `warp.py commits` before the next cluster.
- Never propose or stage files listed in `flags.secrets`; tell the user and suggest `.gitignore`. Do not echo their contents.
- Do not stage generated/vendored files (`flags.generated`) without asking.
- Never use `--no-verify`, never amend or force-push; do not push.
- Files with unresolved conflicts are excluded; point the user to the git-conflict skill.
