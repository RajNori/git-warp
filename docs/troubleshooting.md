# Troubleshooting

Every `warp.py` failure is JSON with an `error` key (and often a `hint`) and a non-zero exit code. The messages
below were produced by real runs unless marked otherwise.

## Installation and loading

**The skills do not appear.** Load the plugin explicitly: `claude --plugin-dir /path/to/git-warp`. Check the manifest
with `claude plugin validate /path/to/git-warp`. Git Warp is not on any marketplace, so
`claude plugin install git-warp` cannot find it ([marketplace.md](marketplace.md)).

**What are the skills called?** In a headless session they were listed as `git-warp:git-xray`,
`git-warp:git-rescue`, etc. Whether a short form works is up to Claude Code.

**`python3: command not found` in a hook.** `hooks/hooks.json` runs `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/..."`.
`python3` must be on the `PATH` Claude Code uses. (Inferred from the hook definition; not reproduced.)

## CLI errors

| Message | Meaning and fix |
|---|---|
| `not a git repository: <path>` (`hint`: run inside a Git repository or pass `--repo PATH`) | The path is not inside a work tree. Use `--repo` |
| `invalid arguments: ...` with `usage` | A flag or value was wrong. Compare with [cli-reference.md](cli-reference.md). `--help` is only supported by the `rescue`, `archaeology` and `bisect` parsers |
| `base ref not found: 'nope'` (`hint`: check the name, or `git fetch`...) | `pr --base` ref does not exist locally |
| `'zzzz' does not resolve to a commit in this repository ...` | `rescue inspect` was given something that is not a commit (blob, tree, ambiguous, or already pruned) |
| `branch 'main' already exists at 8f059303; refusing to overwrite or move it (choose another --name)` | `rescue preserve --name` will never move an existing branch. Pick another name |
| `refusing to delete without --yes` | `memory forget` is a dry run unless you pass `--yes`; the JSON lists `would_delete` |
| `repository memory is disabled (memory_enabled: false in .claude/git-warp.local.md)` | Set `memory_enabled: true` or remove the key |
| `git executable not found` | `git` is not on `PATH` |
| `git timed out ...` with `"partial": true` (history commands) | A Git call exceeded its timeout; narrow the query (`--since`, `--limit`) or use `rescue scan --no-fsck` |
| `internal error: ...` (exit 1) | A bug. Re-run with the same arguments and keep the message |

## The guard

**A Git command was blocked or Claude asked me to confirm.** That is the guard hook. Ask what it matched:

```bash
python3 scripts/warp.py guard check "<the exact command>" --branch <your branch>
```

`reason` and `safer` explain the rule; `rule` names it ([safety-model.md](safety-model.md)). Blocked (`deny`)
commands are meant to be run by you, outside Claude, if you really intend them.

**Too strict or too lax for my project.** Change `protected_branches` or `safety_mode` in
`.claude/git-warp.local.md`. There is no config switch to turn the guard off.

**It did not stop something dangerous.** See [guard-limitations.md](guard-limitations.md). Scripts, variable
expansion, global-config aliases and non-Bash tools are invisible to it; single-file `git checkout -- file` and
`git restore file` are allowed.

**"Git Warp guard error — verify manually".** The guard failed internally on a command that mentions git and asked
instead of guessing. Run the command through `guard check` to see whether the error repeats.

## Configuration

**My config seems ignored.** Check `python3 scripts/warp.py memory status`: `enabled` shows the effective values
and `warnings` shows parse problems (`unknown key: ...`, `safety_mode: expected one of ('standard', 'strict')`,
`ignored .claude/git-warp.local.md: no frontmatter`). The file must live at
`<repo root>/.claude/git-warp.local.md` and start with `---` on its first line.

## Memory and the index

**Results look stale or wrong.** `python3 scripts/warp.py memory index --rebuild`.

**`index.complete` is false.** Only the newest commits were indexed (automatic indexing is capped at 5000 commits,
the session-start auto-index at 1000). Run `memory index` (optionally with `--max-commits N`) for more.

**`warp.db` problems.** An unreadable or incompatible `warp.db` is moved aside once to `warp.db.corrupt` and
rebuilt, with a warning. If `.git/git-warp/` is not writable the index is kept in memory for that run only
(warning: "state directory not writable ..."; `persisted: false`).

**Shallow clone.** History and blame are truncated; commands add a shallow warning. Fetch more history
(`git fetch --unshallow`) for complete results.

**Hotspots or co-change look odd.** Lockfiles, generated and vendored paths, and commits touching more than 40
files (co-change) are excluded by design; merges are excluded from co-change.

## Rescue

**Candidates are empty.** Evidence is limited to reflogs, stashes, `ORIG_HEAD` and (unless `--no-fsck`) dangling
commits. Git Warp cannot recover edits that were never committed or staged, nor objects removed by `git gc`/`prune`.
Check other clones, CI artifacts, editor history or the remote.

**A candidate says `low` confidence.** Labels come with `confidence_reasons`; in the example run a dangling commit
with no reflog entry was `low` because the only evidence was `git fsck` output. Inspect before preserving.

**`rescue inspect` shows `[REDACTED]` where an author name should be.** Known over-redaction in the `stat` field
([privacy.md](privacy.md)). The structured `commit` object has the real values.

## Hooks

**No SessionStart context / no Stop report.** Both are silent outside Git repositories. The Stop report appears
only when the working tree has changes and the report differs from the last one shown.

**Flight recorder is empty.** `recorder_enabled: false`, `recorder_retention_days: 0`, or not inside a work tree.
Check with `warp.py memory status` (`recorder.exists`, `enabled.recorder`).

## Tests

`python3 -m pytest tests -q` needs `pytest` installed. At the recovery checkpoint the suite reported 1070 passed,
1 warning. The warning is a `SyntaxWarning: invalid escape sequence '\;'` in a test string
(`tests/unit/test_guard_branches.py`), recorded in [../planning/POST_RESTORE_VALIDATION.md](../planning/POST_RESTORE_VALIDATION.md).

## Still stuck

Run the failing `warp.py` command directly and read the JSON: `error`, `hint`, `warnings`. Note the Git version
(`git --version`) and Python version. Only Python 3.13.2 / git 2.53.0 on macOS have been tested.
