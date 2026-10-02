# Example: PR Engineer

Command (captured 2026-10-02; the base was auto-detected):

```bash
python3 scripts/warp.py pr --repo <repo>
```

## Captured output (trimmed)

The full output was about 10.7 KB. `"...": "..."` marks removed keys or items; everything else is verbatim.

```json
{
  "command": "pr",
  "repo": "<repo>",
  "status": "ok",
  "base": { "ref": "main", "sha": "8f059303069bc623b65a702f9d5ad33082d25526", "auto_detected": true },
  "merge_base": "8f059303069bc623b65a702f9d5ad33082d25526",
  "head": { "sha": "afef50ed22947c6ae56a6cf33adbd7600f18e625", "branch": "feature/billing", "detached": false },
  "behind_base": 0,
  "is_behind_base": false,
  "ahead_of_base": 3,
  "upstream": { "ref": null, "ahead": null, "behind": null, "state": "none" },
  "not_included": {
    "note": "This analysis covers committed changes in merge-base..HEAD only; the items below are not part of it.",
    "unstaged": { "count": 1, "paths": ["src/app.py"], "truncated": false },
    "untracked": { "count": 1, "paths": ["notes.txt"], "truncated": false },
    "has_uncommitted_changes": true,
    "...": "staged, conflicted, operation_in_progress"
  },
  "commits": {
    "count": 3,
    "conventional_commits": { "conforming": 3, "nonconforming": [], "nonconforming_total": 0 },
    "...": "items, authors, merge_commits"
  },
  "files": {
    "count": 5,
    "items": [
      { "path": "migrations/0002_add_invoices.sql", "added": 5, "deleted": 0, "binary": false, "status": "added" },
      { "path": "package-lock.json", "added": 1, "deleted": 1, "binary": false, "status": "modified" },
      "... 3 more"
    ],
    "truncated": false
  },
  "totals": { "files": 5, "added": 18, "deleted": 3, "binary_files": 0 },
  "clusters": {
    "items": [
      { "id": 1, "label": "dependencies", "kind": "deps", "file_count": 2, "paths": ["package-lock.json", "package.json"], "added": 3, "deleted": 3 },
      {
        "id": 2, "label": "root source", "kind": "source", "file_count": 2,
        "paths": ["src/billing.py", "tests/test_billing.py"],
        "reasons": ["files at the repository root", "test 'tests/test_billing.py' paired with source 'src/billing.py' by naming convention"],
        "added": 10, "deleted": 0
      },
      { "id": 3, "label": "db schema/migrations", "kind": "migration", "file_count": 1, "paths": ["migrations/0002_add_invoices.sql"], "added": 5, "deleted": 0 }
    ],
    "...": "atomic_commit_opportunities"
  },
  "tags": {
    "migration": { "count": 1, "paths": ["migrations/0002_add_invoices.sql"], "truncated": false },
    "dependency": { "count": 2, "paths": ["package-lock.json", "package.json"], "truncated": false },
    "sensitive": { "count": 2, "paths": ["src/billing.py", "tests/test_billing.py"], "truncated": false },
    "...": "schema, lockfile, test"
  },
  "api_surface": {
    "heuristic": true,
    "added": [ { "path": "src/billing.py", "symbol": "invoice_slug", "line": 4 } ],
    "counts": { "removed": 0, "signature_changed": 0, "added": 1 },
    "...": "note, possibly_breaking_removed, signature_changed, schema_files_changed"
  },
  "secrets": { "count": 0, "findings": [], "truncated": false, "note": "Added lines of the net range diff only; values are never printed.", "...": "secret_file_paths" },
  "tests": {
    "potential_missing_tests": false,
    "source_files_changed": 1,
    "test_files_changed": 1,
    "reasoning": "1 source file(s) and 1 test file(s) changed together.",
    "...": "source_files, test_files"
  },
  "rollback": {
    "migrations_present": true,
    "migrations": [ { "path": "migrations/0002_add_invoices.sql", "status": "added", "reversible_signal": false } ],
    "destructive_statements_added": 1,
    "dependencies_changed": true,
    "...": "note, dependency_files, config_files_changed, infra_or_ci_changed"
  },
  "testing_evidence": {
    "found_in_repo": { "ci_config_files": [], "test_directories": ["tests"], "test_runner_configs": [] },
    "test_files_changed_in_range": 1,
    "ran_by_git_warp": false,
    "note": "Git Warp never runs tests. State 'not run' unless you actually ran them and saw the output."
  },
  "hygiene": [],
  "facts_for_prose": {
    "type_hints": ["conventional types in commits: test=1, chore=1, feat=1"],
    "scope_hints": ["deps: 1 cluster(s)", "source: 1 cluster(s)", "migration: 1 cluster(s)"],
    "ticket_refs": [],
    "commit_subjects": [
      "test: cover invoice_slug",
      "chore(deps): add decimal.js",
      "feat(billing): add invoices table and slug helper"
    ],
    "...": "size, cluster_labels, breaking_change_markers, branch"
  },
  "risk": {
    "level": "HIGH",
    "drivers": [
      "HIGH: 1 destructive statement(s) (DROP/TRUNCATE/DELETE) added in migration files",
      "MEDIUM: 1 migration file(s) changed",
      "MEDIUM: 2 sensitive path(s) touched"
    ],
    "rules_version": 1,
    "...": "driver_details"
  },
  "warnings": []
}
```

Things worth noticing: the uncommitted `src/app.py` and `notes.txt` are reported under `not_included`, the
`DROP TABLE legacy_invoices;` line in the migration produced the HIGH driver, and `src/billing.py` was tagged
`sensitive` only because its path contains the word `billing` (a path-name heuristic, see
[../docs/configuration.md](../docs/configuration.md)).

## ILLUSTRATIVE rendering (not captured model output)

Hand-written to show how the `skills/git-pr/SKILL.md` template could use the data above. It is abbreviated and the
prose is an example of what a verified answer might say, not something Git Warp produced.

```
PR TITLE
feat(billing): add invoices table, invoice slug helper and decimal.js

RATIONALE
unknown, ask author (no ticket refs or commit bodies in the range)

IMPLEMENTATION SUMMARY
- db schema/migrations: migrations/0002_add_invoices.sql creates `invoices` and drops `legacy_invoices`
- root source: src/billing.py adds invoice_slug (heuristic: new public symbol), covered by tests/test_billing.py
- dependencies: package.json and package-lock.json add decimal.js

MIGRATIONS
- migrations/0002_add_invoices.sql: 1 destructive statement (DROP TABLE legacy_invoices); no reversible signal found

RISK AREAS
- HIGH: destructive statement in a migration; MEDIUM: migration changed; MEDIUM: 2 sensitive-path matches

TESTING EVIDENCE
- Run by Git Warp: no. Tests: not run
- Present in repo: tests/ directory; test files changed in this range: 1

NOT INCLUDED
- uncommitted: src/app.py (modified), notes.txt (untracked)
```
