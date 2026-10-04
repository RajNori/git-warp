# Configuration

Git Warp works with no configuration. To change behaviour, create `.claude/git-warp.local.md` in the repository
root (the directory `git rev-parse --show-toplevel` reports) and/or `~/.claude/git-warp.local.md` for all
repositories. This follows the Claude Code plugin-settings convention of a `*.local.md` file with frontmatter. This repository's `.gitignore` already contains
`.claude/*.local.md`; add the same line to your own project if you do not want to commit it.

Behaviour verified against `scripts/gitwarp/core/config.py` and by running `warp.py` against a repository with a
config file.

## Policy sources and precedence

There is one policy model, built from three sources. Trust **decreases** from left to right, and a less-trusted source
can only **tighten**:

```
built-in safety floor   →   user policy (~/.claude/git-warp.local.md)   →   repository policy (<repo>/.claude/git-warp.local.md)
   most trusted                                                              least trusted (can only tighten)
```

A repository is untrusted input (anyone can commit a `.claude/git-warp.local.md`), so **no source can loosen what a
more-trusted source established**:

- `protected_branches` is the **union** of the built-in defaults, the user's additions and the repository's additions.
  Nothing can remove a default. Relaxing protection is not supported in v0.1.0.
- `safety_mode` is the **strictest** value across sources (`strict` beats `standard`); a repository `standard` cannot
  lower a user `strict`.
- `memory_enabled` and `recorder_enabled` are disabled if **any** source says `false` (turning collection off is a
  privacy tightening and is allowed from either source). `recorder_retention_days` can only be lowered by the repository.
- The path lists (`sensitive_paths`, `ignored_paths`, `test_paths`, `infra_paths`) are unions: they add project context.
- `guard_enabled`, `safety_mode: off`, an empty `protected_branches` and any other attempt to disable the guard are
  **ignored with a warning** (prefixed with the source). The unconditional rules (`reset --hard`, forced `clean`,
  forced push or forced fetch into a protected ref, ...) cannot be configured away.
- Policy files are read defensively: only regular files of at most 64 KiB, opened without following symlinks; a
  symlink, FIFO, socket, directory, binary, oversized or malformed file is ignored with a warning. Lists are capped
  at 200 items of 256 characters. (A symlinked *parent directory* such as `.claude` is not rejected: only the final file is checked.)

## File format

A leading frontmatter block between two `---` lines. The parser is intentionally small (no PyYAML):

- `key: value` scalars: `true/yes/on` and `false/no/off` (case-insensitive) are booleans, integers are integers,
  quoted strings lose their quotes, everything else is a string
- inline lists: `key: [a, b, "c d"]`
- block lists: `key:` followed by `- item` lines
- blank lines and `#` comment lines are skipped
- anything after the closing `---` is ignored (use it for notes)

If the file does not start with `---`, or the frontmatter is not terminated, the whole file is ignored and a
warning `ignored .claude/git-warp.local.md: ...` is reported. Unknown keys, wrong types and out-of-range values
are ignored individually with a warning (for example `unknown key: bogus_key`); the default for that key stays.
Warnings appear in the `warnings` of commands that load the config (for example `memory status`).

## Keys

| Key | Type | Default | Validation | Effect |
|---|---|---|---|---|
| `protected_branches` | list of strings | `main`, `master`, `develop`, `development`, `production`, `prod`, `release` | list of strings; an empty list is ignored (warning). Entries are **added** to the defaults | Branch globs (`fnmatch`) the guard treats as protected |
| `safety_mode` | string | `standard` | `standard` or `strict`; the strictest source wins | `strict` turns history-rewriting `ask` verdicts into `deny` |
| `sensitive_paths` | list of strings | `[]` | list of strings | Extra globs that receive the `sensitive` tag |
| `ignored_paths` | list of strings | `[]` | list of strings | Extra globs tagged `generated` (excluded from analyses that skip generated files) |
| `test_paths` | list of strings | `[]` | list of strings | Extra globs tagged `test` |
| `infra_paths` | list of strings | `[]` | list of strings | Extra globs tagged `infra` |
| `memory_enabled` | bool | `true` | `true`/`false` | `false`: no automatic indexing at session start; `memory` query subcommands return an error |
| `recorder_enabled` | bool | `true` | `true`/`false` | `false`: the flight recorder writes nothing |
| `recorder_retention_days` | integer | `30` | 0 to 3650 | Records older than this are compacted away; `0` stops recording |

Glob matching for path keys (`core/paths.py`): a pattern matches if `fnmatch` matches the full repo-relative path,
the path as a directory prefix (`pattern/*`), or the file name alone. Remember `fnmatch` `*` also matches `/`.
Built-in path tagging also looks at names and directories (lockfiles, manifests, `migrations/`, CI directories,
words such as `auth`, `payment`, `billing`, `session` for `sensitive`); your globs add to it, they do not replace it.

## Example

```markdown
---
protected_branches:
  - main
  - release/*
safety_mode: strict
sensitive_paths: ["src/payments/**", "config/prod.yml"]
ignored_paths:
  - vendor/**
test_paths: [spec/**]
infra_paths: [deploy/**]
memory_enabled: true
recorder_enabled: false
recorder_retention_days: 7
---

Free-form notes for humans go here and are ignored.
```

With that file, in a real run:

- `warp.py memory status` reported `enabled: {memory: true, recorder: false, retention_days: 7}` and, with an extra
  `bogus_key: 1` in the frontmatter, `warnings: ["unknown key: bogus_key"]`
- `warp.py guard check "git rebase main" --branch x` returned `deny` with `safety_mode: strict`
- `warp.py guard check "git push --force origin release/2" --branch x` returned `deny` (matched `release/*`)

## What each key influences

| Key | Used by |
|---|---|
| `protected_branches`, `safety_mode` | Guard hook, `guard check` |
| `sensitive_paths`, `ignored_paths`, `test_paths`, `infra_paths` | Path classification used by X-Ray, PR, Blast Radius, Commit Composer, Stop-hook risk report |
| `memory_enabled` | `memory` queries, SessionStart auto-index |
| `recorder_enabled`, `recorder_retention_days` | PostToolUse recorder, SessionStart record, `memory sessions` |

## Overriding on the command line

Only the guard has overrides: `warp.py guard check "<cmd>" --mode strict --protected "main,release/*"`. They follow
the same rule: `--protected` **adds** branches and `--mode` can only raise strictness.
The only environment input is `HOME`, which locates the user policy file.

## Disabling things

- Stop recording: `recorder_enabled: false` (or `recorder_retention_days: 0`). Existing records stay until they age
  out or you run `memory forget --yes`.
- Stop indexing: `memory_enabled: false`. An existing `warp.db` stays until you delete it.
- The guard has no off switch in config (any attempt is ignored with a warning). Remove or disable the plugin to stop it.
