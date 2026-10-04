# Recommendation ids to prose

Write one or two sentences each, citing evidence from the JSON. Suggest, never execute.

- `resolve-conflicts-first`: list the conflicted paths; resolve them (or abort the operation) before any other action. Suggest `/git-conflict` if available.
- `finish-or-abort-operation`: name the operation; say how to continue or abort it, noting that abort discards in-progress merge state.
- `review-secrets`: list `path:line` findings and secret-file paths; do not commit or push; if already committed, rotate the credential and clean history deliberately.
- `rebase-or-merge-upstream`: behind N upstream commits; compare rebase (linear, rewrites local commits) vs merge (keeps history); recommend based on whether local commits are pushed.
- `set-upstream-before-push`: no upstream; suggest `git push -u origin <branch>` only as a future step.
- `split-commits`: use `atomic_commit_opportunities`; propose commit boundaries by cluster after reading the files.
- `add-tests`: state which source files changed without test changes, and that this is a heuristic.
- `sync-lockfile`: manifest and lockfile changed out of sync; regenerate with the project's package manager.
- `review-migration`: read the migration; state reversibility and data impact only if verified.
- `review-sensitive-changes`: name the sensitive paths and why they matter.
- `create-branch-for-detached-work`: HEAD is detached; commits made now are reachable only through the reflog.
- `no-action-needed`: say the tree is clean and no rule fired.
