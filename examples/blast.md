# Example: Blast Radius

Three real runs against the demo repository (captured 2026-10-02).

## 1. Working tree (`src/app.py` modified, `notes.txt` untracked)

```bash
python3 scripts/warp.py blast --repo <repo>
```

Trimmed (`"...": "..."` marks removed keys):

```json
{
  "repo": "<repo>",
  "mode": "working-tree",
  "base": null,
  "level": "LOW",
  "level_note": "deterministic rule outcome, not a probability; see level_rules",
  "risk_drivers": [],
  "changed": [
    { "path": "src/app.py", "status": "M", "tags": ["source"], "role": "source", "exports": ["make_page"], "direct_dependants": 0, "tests": ["tests/test_app.py"], "...": "dependant_modules, interface_signals" },
    { "path": "notes.txt", "status": "?", "tags": ["docs"], "role": "docs", "...": "..." }
  ],
  "nodes": [
    { "path": "tests/test_app.py", "kind": "test", "reason": "imports src.app", "depth": 1, "via": "src/app.py", "confidence": "import-graph", "...": "for, tags, interface_signals" }
  ],
  "counts": { "test": 1 },
  "truncated": { "nodes": false, "omitted_nodes": 0, "depth": false, "scan": false, "max_depth": 3, "max_nodes": 200 },
  "stats": { "files_in_universe": 10, "files_parsed": 5, "import_edges": 4, "seconds": 0.03 },
  "caveats": ["Edges from imports are static and best effort (no dynamic dispatch, no runtime config); text-match edges are heuristic."],
  "warnings": [],
  "...": "level_rules, risk_driver_details, tree"
}
```

## 2. Explicit path (`blast src/util.py`)

```bash
python3 scripts/warp.py blast src/util.py --repo <repo>
```

```json
{
  "mode": "paths",
  "level": "LOW",
  "risk_drivers": [],
  "changed": [
    { "path": "src/util.py", "status": "M", "role": "source", "exports": ["slugify"], "direct_dependants": 2,
      "dependant_modules": ["(root)"], "tests": ["tests/test_app.py", "tests/test_billing.py"], "...": "tags, interface_signals" }
  ],
  "tree": [
    {
      "path": "src/util.py", "kind": "changed", "depth": 0,
      "children": [
        { "path": "src/app.py", "kind": "dependant", "reason": "imports src.util", "depth": 1, "confidence": "import-graph",
          "children": [ { "path": "tests/test_app.py", "kind": "test", "reason": "imports src.app", "depth": 2, "confidence": "import-graph", "children": [] } ] },
        { "path": "src/billing.py", "kind": "dependant", "reason": "imports src.util", "depth": 1, "confidence": "import-graph",
          "children": [ { "path": "tests/test_billing.py", "kind": "test", "reason": "imports src.billing", "depth": 2, "confidence": "import-graph", "children": [] } ] }
      ],
      "...": "reason, confidence"
    }
  ],
  "counts": { "dependant": 2, "test": 2 },
  "...": "repo, base, state, level_note, level_rules, risk_driver_details, nodes, truncated, stats, caveats, warnings"
}
```

Two direct dependants are below the "public module" threshold in `level_rules`, so the level stays LOW.

## 3. Branch diff against `main` (`blast --base main`)

```bash
python3 scripts/warp.py blast --base main --repo <repo>
```

```json
{
  "mode": "base:main",
  "base": "main",
  "level": "HIGH",
  "risk_drivers": [
    "schema/migration touched: migrations/0002_add_invoices.sql",
    "sensitive path changed with no tests found: src/billing.py",
    "lockfile changed: package-lock.json"
  ],
  "risk_driver_details": [
    { "id": "schema", "weight": "major", "text": "schema/migration touched: migrations/0002_add_invoices.sql" },
    { "id": "sensitive-no-tests", "weight": "major", "text": "sensitive path changed with no tests found: src/billing.py" },
    { "id": "lockfile", "weight": "minor", "text": "lockfile changed: package-lock.json" }
  ],
  "counts": {},
  "...": "repo, state, level_note, level_rules, changed, nodes, tree, truncated, stats, caveats, warnings"
}
```

The rule text that explains the level (from `level_rules` in the same output): "HIGH if >=2 major drivers, or >=1
major and >=2 drivers total, or >=4 drivers total; MEDIUM if >=1 driver; LOW otherwise".

Be aware of a visible limitation: `tests/test_billing.py` exists and imports `src.billing`, yet the driver says
"no tests found". In this run `changed[].tests` for `src/billing.py` was `[]` and `nodes` was empty, which
appears to be because the test file is itself part of the changed set (the graph is built outward from changed
files; this explanation is the documentation author's inference, not verified in the code). Treat that driver as a
lead to verify, as the skill instructs.

## ILLUSTRATIVE rendering (not captured model output)

Hand-written example for run 3, following the template in `skills/git-blast-radius/SKILL.md`.

```
BLAST RADIUS: HIGH
STATE: 5 changed files, mode base:main, branch feature/billing
TREE:
  migrations/0002_add_invoices.sql  (migration)
  src/billing.py  (source, exports invoice_slug)
  tests/test_billing.py  (test)
  package.json, package-lock.json  (dependency, lockfile)
RISK DRIVERS:
  - schema/migration touched: migrations/0002_add_invoices.sql  (a schema change must be deployed in order)
  - sensitive path changed with no tests found: src/billing.py  (verify: tests/test_billing.py covers it)
  - lockfile changed: package-lock.json
WHY: two major drivers (schema, sensitive-no-tests) satisfy the HIGH rule
RECOMMENDATION: review the migration first; run tests/test_billing.py
SAFE NEXT ACTION: python3 -m pytest tests/test_billing.py -q   (not run by Git Warp)
```
