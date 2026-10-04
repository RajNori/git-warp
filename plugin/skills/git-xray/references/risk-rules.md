# Risk rules (deterministic, no scores)

Level is the highest triggered rule. Source of truth: `scripts/gitwarp/analysis/risk.py`.

HIGH: conflicted paths >= 1; rebase in progress with dirty tree; secret-file path or secret-looking added line; more than 25 files changed including sensitive paths and migrations.

MEDIUM: operation in progress (merge/cherry-pick/revert/bisect/rebase on a clean tree); diverged from upstream (ahead>0 and behind>0); sensitive paths touched; migration files changed; lockfile/manifest out of sync; no upstream while commits ahead of the default base; more than 40 files or 1000 changed lines; detached HEAD with uncommitted changes.

LOW: nothing above triggered. LOW is not "safe", it means no rule fired; still list `warnings`.

Known false positives: secret findings in test fixtures (`in_test_file: true`), manifest edits that do not touch dependencies, sensitive-path matches on names like `auth_helpers.md`. Check before alarming the user.
