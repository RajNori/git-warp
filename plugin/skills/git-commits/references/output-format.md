# `warp.py commits` output

| field | meaning |
|---|---|
| `mode` | `working-tree` (staged + unstaged + untracked) or `staged` |
| `state` | branch, head, detached, unborn, shallow, operation (merge/rebase/...) |
| `files[]` | `path, status (M/A/D/R/?/U), added, deleted, binary, staged, unstaged, tags, cluster, orig_path?` |
| `clusters[]` | deterministic groups: `id, label, kind, paths, reasons, added, deleted`. Kinds: source, test, docs, migration, infra, ui, deps, config, generated |
| `proposals[]` | ordered commit plan: `order, cluster, type, message (skeleton), needs_type_decision, files, commands, order_reason` |
| `commands[]` | the same commands per step; plain text only, the plugin never runs them |
| `mixed_concerns[]` | files matching several concerns, tests pairing ambiguously, partially staged files (`severity` info/warn) |
| `hunks{path}` | files with 2+ hunks: `count, span_lines, split_candidate, hunks[{header,old_start,new_start,...}]` (from `git diff -U0`) |
| `flags` | `secrets` (never proposed), `generated`, `conflicted`, `binary` |

## Clustering heuristics (so you can second-guess them)
1. generated and secret-looking files are isolated first.
2. deps: manifest + lockfile per directory. migrations + schema together. CI and infra separate. docs together.
3. source/UI by module (directory prefix, skipping generic dirs like `src`, `lib`, `app`); big modules split one level deeper.
4. tests join their source by naming convention (`test_x.py`, `x_test.go`, `x.test.ts`, `__tests__/x.ts`).
5. small source clusters whose changed files import each other are merged.

Ordering rank: deps, migration, config, source, ui, test (unpaired), infra/ci, docs.

## Conventional Commit types suggested
test, docs, ci, `build(deps)`, `feat(db)` for migrations, `chore(config)`, `chore(infra)`; source/UI is `feat|fix?` on purpose: decide from the diff.
