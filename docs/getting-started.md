# Getting started

This page assumes you have read the [README](../README.md) overview. Everything below was checked against the code
or run for real; items that could not be checked are labelled.

## 1. Check requirements

```bash
python3 --version      # tested on 3.13.2
git --version          # tested on 2.53.0
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"   # needs 3.24+ (upserts); tested on 3.51.0
```

No `pip install` step exists: Git Warp uses only the Python standard library.

## 2. Load the plugin

Git Warp is not on any marketplace yet (see [marketplace.md](marketplace.md)). Load it from a local checkout:

```bash
git clone https://github.com/RajNori/git-warp.git
cd your-project
claude --plugin-dir /path/to/git-warp/plugin
```

Optional manifest check: `claude plugin validate /path/to/git-warp/plugin`.

In a headless session the skills were listed as `git-warp:git-xray`, `git-warp:git-rescue`, and so on
(namespaced `plugin:skill`). Whether `/git-xray` also works depends on Claude Code.

## 3. What happens automatically

Once the plugin is loaded in a Git repository, the hooks in `hooks/hooks.json` run without you doing anything:

| Hook | When | Effect |
|---|---|---|
| `SessionStart` | session starts, resumes, clears or compacts | Adds a short context block (branch, upstream, working-tree counts, stashes, worktrees, last 5 commits, safety policy reminder) and does a bounded incremental history index (about 5 s budget, first run capped at 1000 commits) |
| `PreToolUse` on `Bash` | before every Bash command | The Git guard returns deny, ask or defer (no objection). See [safety-model.md](safety-model.md) |
| `PostToolUse` on `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `Bash` | after each such tool call | Appends a redacted line to the flight recorder. See [privacy.md](privacy.md) |
| `Stop` | end of each assistant turn | If the working tree changed, prints a short report (branch, counts, risk level, diffstat, next step); stays silent when nothing changed or the report is identical to the last one |

Outside a Git repository the `SessionStart` and `Stop` hooks do nothing. All hooks exit 0 and are designed not to
crash Claude Code; the guard errs toward `ask` when it fails on a command that mentions `git`.

## 4. Try the CLI directly

You can run everything the skills run, yourself. Use a scratch repository first if you like:

```bash
python3 /path/to/git-warp/plugin/scripts/warp.py xray --repo /path/to/repo
python3 /path/to/git-warp/plugin/scripts/warp.py guard check "git reset --hard HEAD~1"
python3 /path/to/git-warp/plugin/scripts/warp.py memory hotspots --repo /path/to/repo
```

Output is JSON on stdout. Errors are JSON too (`{"error": ...}`) with a non-zero exit code. Every command takes
`--repo PATH` (default: the current directory). Real captured outputs are in [../examples/](../examples/).

## 5. Use the skills

Ask in plain language or invoke a skill by name. Typical prompts, with the skill each is meant to trigger (from the
skill descriptions):

| You say | Skill |
|---|---|
| "what's risky in this branch?", "x-ray the repo" | `git-warp:git-xray` |
| "I ran git reset --hard and lost commits" | `git-warp:git-rescue` |
| "who introduced this function and why?" | `git-warp:git-archaeology` |
| "find which commit broke the tests" | `git-warp:git-bisect-ai` |
| "write a PR description for this branch" | `git-warp:git-pr` |
| "split my changes into commits" | `git-warp:git-commits` |
| "what does this change affect?" | `git-warp:git-blast-radius` |
| "help me resolve this merge conflict" | `git-warp:git-conflict` |
| "has this code been removed before?" | `git-warp:git-temporal-review` |
| "which files change together?", "show hotspots" | `git-warp:git-memory` |

The skills are read-only by design: they tell Claude not to stage, commit, reset, or edit unless you explicitly ask.
That is a prompt-level instruction. The hard guarantee is the guard hook, which only covers Git commands that pass
through the Bash tool.

## 6. Configure (optional)

Create `.claude/git-warp.local.md` in your repo to change protected branches, switch to `strict` mode, add
sensitive/test/infra/ignored globs, or disable the recorder and the history index. See
[configuration.md](configuration.md).

## 7. Where things are written

Only `<repo>/.git/git-warp/` (`warp.db`, `flight-recorder.jsonl`, `state.json`). See [privacy.md](privacy.md) for
exact contents and how to delete them.

## Next

- [commands.md](commands.md): every command, read-only or not, and the JSON keys it returns
- [cli-reference.md](cli-reference.md): every flag
- [troubleshooting.md](troubleshooting.md)
