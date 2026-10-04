---
name: git-archaeology
description: This skill should be used when the user asks how or why code evolved, for example "who introduced this", "when was this function added", "why does this file look like this", "history of src/foo.py", "trace this symbol through history", "was this reverted", "what's the story behind this bug", or wants the smallest set of commits to read to understand a file, directory, symbol, or decision. Builds a fact-versus-inference timeline from git data.
argument-hint: "<path> | --symbol NAME | --regex RE | --question \"text\" [--since DATE] [--limit N]"
allowed-tools: Read, Grep, Glob, Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Bash(git log:*), Bash(git show:*), Bash(git blame:*), Bash(git diff:*), Bash(git rev-parse:*)
---

# git-archaeology

> Run `warp.py` from the repository directory: `--repo` defaults to the current directory. Do **not** pass `--repo` a shell variable or any other shell expansion: Claude Code cannot analyse it statically and would prompt even though `warp.py` is pre-approved. If you must name another repository, use a literal absolute path.

Reconstruct how code came to be from Git evidence. Separate what git records (FACT) from what is guessed from it (INFERENCE), and admit what git cannot tell (UNKNOWN). Read-only.

## Workflow

1. Pick the form of the question:
   - a file or directory: `... archaeology path/to/file`
   - a function/class/string: `... archaeology --symbol NAME` (git `-S`: commits that changed how often it occurs)
   - a pattern: `... archaeology --regex 'RE'` (git `-G`, POSIX ERE, no `\w`)
   - a "why" question with no code anchor: `... archaeology --question "why was caching removed"` (keywords matched against commit messages only)
   Full command: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" archaeology <target-or-flags>`
   Add `--since` or `--limit` for large histories. A symbol plus a path (`<path> --symbol NAME`) restricts the search.
2. Read `warnings` and `truncated` first. If `truncated` is true, or `--since` was used, the introduction is NOT established; say so.
3. Use the structured fields:
   - facts: `timeline`, `renames`, `path_history`, `deleted_then_restored`, `currently_deleted`, `blame_summary`, `authorship_timeline`, `merge_points`, `reverts` with `linked_via: body`, `symbol_presence`
   - inferences: `fix_like_commits`, `large_rewrites`, `reverts` with `linked_via: subject-match`, `introduction` when tagged inference, anything in `statements` tagged inference
   - `unknown`: always carry these through.
4. If the target is missing at HEAD the CLI searches history (deleted or renamed away). If `found` is false, report `similar_paths_in_history` and ask.
5. Look at 1-3 of `commits_worth_reading` for real: `git show --stat <sha>` or `git show <sha> -- <path>` (read-only) before explaining intent. Quote commit messages as evidence, never as proven motive.
6. Treat authorship as contribution history, not ownership.

See `references/archaeology-playbook.md` for symbol/regex tips, rename and rewrite pitfalls, and how to phrase inferences.

## Response template

```
STATE
  <target, mode, commits examined, truncated/since/shallow caveats>

TIMELINE
  <oldest to newest: introduction, renames, rewrites, reverts, fixes, merges - each with sha8 and date>

FACT
  - <statement directly from git data, with sha8>

INFERENCE
  - <heuristic reading, labelled with its basis, e.g. "fix-like message", "high churn">

UNKNOWN
  - <what git cannot say: motive, rewritten history, other branches>

RISK
  <what a reader might wrongly conclude; e.g. squashed history, partial window>

RECOMMENDATION
  <how to proceed: where to look next, who/what to ask>

COMMITS WORTH READING
  1. <sha8> <subject> - <reason> [fact|inference]
  (smallest useful set, at most 7, in reading order)
```

Always end with COMMITS WORTH READING.
