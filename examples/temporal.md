# Example: Temporal Code Review

Captured 2026-10-02. Two real runs.

## 1. No evidence (branch diff against `main` in the demo repository)

```bash
python3 scripts/warp.py temporal --base main --repo <repo>
```

Trimmed:

```json
{
  "mode": "base:main",
  "base": "main",
  "changed_files": ["migrations/0002_add_invoices.sql", "package-lock.json", "package.json", "src/billing.py", "tests/test_billing.py"],
  "findings": [],
  "findings_total": 0,
  "probes": { "used": 17, "budget": 25, "timed_out": false, "seconds": 0.17 },
  "sources": { "git_log_pickaxe": "used", "memory_index": "used" },
  "instructions": "Each finding is a lead, not a conclusion. Read the cited commits' diffs; only warn the user for findings you verified, and cite the commits.",
  "warnings": [],
  "message": "no historical evidence found within the probe budget (17/25 probes); this is not proof that the change is safe",
  "...": "repo, state"
}
```

## 2. One finding

A second clean copy of the demo repository, with `src/cache.py` (removed earlier by commit `3d50bae3`) re-created
as an untracked file, then:

```bash
python3 scripts/warp.py temporal --repo <repo>
```

```json
{
  "mode": "working-tree",
  "base": null,
  "changed_files": ["src/cache.py"],
  "findings": [
    {
      "kind": "touched-file-has-reverts",
      "file": "src/cache.py",
      "match_quality": "heuristic",
      "evidence": [
        {
          "sha": "3d50bae3f12655e427ebbd5f2b183ff349b6fc0a",
          "short": "3d50bae3",
          "subject": "revert: remove cache helper, stale data in tests",
          "date": "2026-08-07T10:00:00+10:00"
        }
      ],
      "signals": ["revert"],
      "caveat": "String/identifier similarity is not semantic equivalence. Read the cited commit's diff and the current change before deciding whether this is a real regression; do not warn the user without citing these commits. Matched on commit-message wording only; the revert may be unrelated to the lines being changed now.",
      "id": 1,
      "verify_with": ["git show 3d50bae3f12655e427ebbd5f2b183ff349b6fc0a -- src/cache.py"]
    }
  ],
  "findings_total": 1,
  "probes": { "used": 0, "budget": 25, "timed_out": false, "seconds": 0.04 },
  "sources": { "git_log_pickaxe": "used", "memory_index": "used" },
  "warnings": [],
  "...": "repo, state, instructions"
}
```

The match is only `heuristic`, based on the commit subject, and the tool says so in `caveat`. It did not find the
exact re-added lines (`probes.used` was 0).

## ILLUSTRATIVE rendering (not captured model output)

Following `skills/git-temporal-review/SKILL.md`, after a model has read `git show 3d50bae3 -- src/cache.py`.

```
TEMPORAL WARNING: src/cache.py was removed before (3d50bae3, "revert: remove cache helper, stale data in tests")
Related historical removal:
  3d50bae3 2026-08-07 "revert: remove cache helper, stale data in tests"  removed  src/cache.py
Current change:
  src/cache.py: file re-created with the same cache helper (untracked)
Historical evidence:
  - the removal message cites stale data in tests
  - match quality: heuristic; verified by reading the 3d50bae3 diff
RECOMMENDATION: check whether the stale-data problem is handled before re-adding the cache
SAFE NEXT ACTION: git show 3d50bae3 -- src/cache.py
```
