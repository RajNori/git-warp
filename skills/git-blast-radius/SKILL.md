---
name: git-blast-radius
description: Estimate source-level dependants of changed JavaScript, TypeScript, and Python files.
argument-hint: "[base]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-blast-radius

Identify changed source files and scan repository source for statically recognizable imports that resolve to them. Report each importer and the changed target as evidence. State the scan limit and that regex-based static parsing can miss aliases, generated modules, dynamic imports, package resolution, runtime wiring, and languages outside JS/TS/Python. No match is not proof of no dependants. Keep this analysis read-only.
