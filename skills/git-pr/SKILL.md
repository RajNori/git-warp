---
name: git-pr
description: This skill should be used when the user asks to "write a PR", "create a pull request description", "prepare this branch for review", "review my branch", "PR summary", "what should reviewers look at", "is this branch ready to merge", or wants a hygiene check of the commits between a base branch and HEAD.
argument-hint: "[base-branch] [--base REF] [--repo PATH]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Read, Grep, Glob, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git branch --list:*), Bash(git branch --show-current:*), Bash(git branch -vv:*), Bash(git merge-base:*), Bash(git rev-list:*)
---

# Git PR package

Turn `merge-base(base, HEAD)..HEAD` into a reviewer-ready PR package. Git Warp supplies deterministic facts as JSON; you read the key diffs, verify, and write the prose. This skill is read-only: suggest commands, never run mutating ones (no commit, rebase, push, reset, checkout, merge). Do not push or open the PR yourself.

## Step 1: gather evidence

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" pr $ARGUMENTS
```

With no argument the base is auto-detected (origin/HEAD, main, master). On `"error"` report it with its `hint` (no base found, invalid or unknown ref, unrelated histories, shallow clone without merge base) and stop; ask the user for `--base` when needed. If `status` is `no-commits`, say there is nothing to put in a PR and why (`message`). Surface all `warnings`. Field reference: `references/output-fields.md`.

## Step 2: read selectively and verify

The JSON gives file lists, clusters, heuristics and counts, not intent. Before asserting what the change does:

1. Read the diff of the 3 to 6 highest-signal files: the largest changes, migrations, API/schema files, sensitive paths, each cluster's main file (`git diff <merge_base>..HEAD -- <path>`). Do not dump the full diff.
2. Check every heuristic you plan to cite (`api_surface`, `debug_leftovers`, `missing_tests`, `reversible_signal`) against the actual lines. Label unverified ones "heuristic".
3. `secrets.findings` have `path:line` only. Look at the line to judge it, but never print a secret value; write `[REDACTED]`.

## Step 3: render

```
PR TITLE
<one line, imperative, <=72 chars; derive from verified scope; use facts_for_prose.type_hints and ticket_refs; offer one alternative if scope is ambiguous>

RATIONALE
<why: from commit bodies, ticket refs, code; mark "unknown, ask author" when not evidenced>

IMPLEMENTATION SUMMARY
- <per cluster or area: what changed, in specific terms with file names>

ARCHITECTURE IMPLICATIONS
- <new modules, changed boundaries, API/schema surface changes (say heuristic if unverified); "none observed" if none>

MIGRATIONS
- <files, destructive statements, reversible_signal, deployment ordering; "none">

DEPENDENCY CHANGES
- <manifest/lockfile changes; drift (manifest without lockfile or the reverse); "none">

RISK AREAS
- <risk.drivers with evidence counts, plus config/infra/CI changes and sensitive paths>

TESTING EVIDENCE
- Run by Git Warp: no. Tests: <"not run" unless you ran them and saw output; then the exact command and result>
- Present in repo: <testing_evidence.found_in_repo: CI files, test dirs>; test files changed in this range: <n>
- Missing-test concern: <tests.reasoning, verified or heuristic>

ROLLBACK CONSIDERATIONS
- <revert is clean? migrations applied need data/schema rollback; dependency changes; config/flag changes; from rollback facts>

REVIEWER FOCUS
- <3 to 5 specific files/areas and what to check there>

UNRESOLVED QUESTIONS
- <things the diff cannot answer; stale base (behind_base), upstream diverged, uncommitted work not included>

HYGIENE FINDINGS
- <commit-hygiene (fixup!/WIP/nonconforming), debug leftovers (path:line, added lines only), secrets (path:line REDACTED), generated/large/binary files, unrelated changes with the cluster labels, merge commits; suggested fix as a command the user can run>

NOT INCLUDED
- <not_included: uncommitted/untracked paths that this PR analysis ignored>
```

## Rules

- Never fabricate test results, CI status, benchmarks or reviewer names. Write "not run" for tests unless you ran them.
- No generic AI commentary ("this improves maintainability"). Every claim must trace to a file, a commit, or a count from the JSON.
- State when the branch is behind base (`behind_base`) or upstream is ahead/diverged (`upstream.state`), and what that means for the merge result.
- If `unrelated_changes.flag` is true, propose how to split the PR (clusters as boundaries) before writing a single title.
- Recommended fixes (squash fixups, remove debug lines, rotate secrets) are suggestions for the user; give the exact command but do not execute it.
- Mention scan limits: `patch_scan.truncated`, per-file caps, net-diff scanning (a secret added then removed within the range is not seen; scan commit history separately if that matters).
