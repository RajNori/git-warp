---
name: git-blast-radius
description: This skill should be used when the user asks "what does this change affect", "blast radius", "what could break", "who depends on this file", "impact analysis", "is this change risky", "which tests should I run", or wants to understand the downstream impact of a diff, branch or specific files before committing or opening a PR.
argument-hint: "[paths...] [--base REF]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*), Read, Grep, Glob, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git blame:*), Bash(git ls-files:*), Bash(git rev-parse:*), Bash(git merge-base:*)
---

# git-blast-radius: explainable impact analysis

Read-only. Builds an import/reference graph from the changed files outward and reports an explainable risk level.

## 1. Run

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" blast [paths...] [--base REF] [--depth N] [--max-nodes N]
```

No paths: uses the working tree (staged, unstaged, untracked). `--base REF`: the branch diff `REF...HEAD`. Explicit paths: those files. If `error` is present report it and stop. Output reference: `references/graph-and-levels.md`.

## 2. Interpret

- `level` (LOW/MEDIUM/HIGH) is the outcome of fixed rules over `risk_drivers`, not a probability. Never convert it to a percentage. Always show the drivers.
- `nodes[].confidence`: `import-graph` edges are static facts; `naming` (tests) and `text-match` edges are heuristics. Say so, and open the file with Read/Grep when an edge decides your conclusion.
- Check `truncated` (depth, nodes, scan) and `warnings`; state plainly what was not analysed.
- Static analysis misses dynamic imports with computed names, reflection, runtime config and cross-repo consumers. Mention this when the change touches a public API.

## 3. Output template

```
BLAST RADIUS: <LEVEL>
STATE: <N changed files, mode, branch>
TREE:
  <changed file>  (role, exports)
    depth 1  <dependant>   <reason, e.g. imports ./payment-service>
      depth 2  <dependant>  ...
    tests: <paths>  |  interfaces: <routes/handlers>  |  schema/config/infra: <paths>
RISK DRIVERS:
  - <each driver verbatim from risk_drivers, with your one-line why it matters>
WHY: <how the drivers combine under the level rules>
RECOMMENDATION: <tests to run first, files to review, whether to split the change>
SAFE NEXT ACTION: <a read-only or test command the user can run>
```

Recommend concrete test files from `nodes` with `kind: test`; if none were found for changed source, say so and propose where one belongs. Do not run the project's tests unless the user asks.
