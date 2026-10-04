# CLI reference: `scripts/warp.py`

```
python3 /path/to/git-warp/scripts/warp.py <command> [args]
```

Inside Claude Code the skills call it as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" <command> ...`.

Commands (the dispatch table in `scripts/warp.py`): `xray`, `pr`, `rescue`, `archaeology`, `bisect`, `commits`,
`blast`, `conflict`, `temporal`, `memory`, `guard`. Running `warp.py` with no arguments, `--help`/`-h` or an unknown
command prints `{"usage": ..., "commands": [...]}` (exit 0 for `-h`/`--help`, 2 otherwise).

## Conventions

- **Output:** indented JSON on stdout, including errors (`{"error": "..."}`). Never a traceback.
- **Exit codes:** `0` success; `2` a reported error (bad arguments, not a repository, Git failure, a result that
  contains `error`); `1` an unexpected internal error (the message is still JSON). `guard check` returns 0 for
  every verdict, including `deny`.
- **`--repo PATH`:** accepted by every command; default is the current directory. Used to find the repository root.
- **Argument errors:** returned as JSON with a `usage` string rather than printed by argparse. `xray`, `pr`,
  `commits`, `blast`, `conflict` and `temporal` do not define `--help`; the `rescue`, `archaeology` and `bisect`
  parsers do accept `-h`.
- **Mutation:** only `rescue preserve` creates a ref and `memory forget --yes` deletes local state. Some analyses
  create or refresh `.git/git-warp/warp.db` (`temporal`, all `memory` queries). See [commands.md](commands.md).

## xray

| Argument | Default | Meaning |
|---|---|---|
| `--untracked-all` | off | List every untracked file instead of collapsing directories |
| `--repo PATH` | cwd | Repository |

## pr

| Argument | Default | Meaning |
|---|---|---|
| `BASE` (positional, optional) | auto-detect | Base ref |
| `--base REF` | auto-detect | Base ref (if both given they must be equal) |
| `--repo PATH` | cwd | Repository |

## rescue

`rescue` alone is `rescue scan`.

### rescue scan

| Flag | Default | Meaning |
|---|---|---|
| `--since TEXT` | none | Only candidates seen or created since, e.g. `"2 hours ago"` |
| `--grep TEXT` | none | Only candidates whose message contains TEXT |
| `--path PATH` | none | Only candidates touching PATH |
| `--limit N` | 25 | Maximum candidates (minimum 1) |
| `--no-fsck` | off | Skip the dangling-object scan |
| `--fsck-timeout S` | 45.0 | Timeout for fsck (minimum 1) |
| `--include-reachable` | off | Also list reflog commits still reachable from a branch |
| `--repo PATH` | cwd | Repository |

### rescue inspect

`rescue inspect <sha> [--repo PATH]`. Read-only. Exit 2 if the commit cannot be inspected.

### rescue preserve

| Argument | Default | Meaning |
|---|---|---|
| `<sha>` | required | Commit to preserve |
| `--name BRANCH` | `rescue/<YYYY-MM-DD>-<sha8>` | Branch name to create (validated; never overwrites an existing branch) |
| `--dry-run` | off | Print what would run; change nothing |
| `--repo PATH` | cwd | Repository |

## archaeology

| Argument | Default | Meaning |
|---|---|---|
| `TARGET` (positional, optional) | none | File or directory path; history is searched even if deleted |
| `--symbol NAME` | none | `git log -S`: commits that changed the number of occurrences |
| `--regex RE` | none | `git log -G`: commits whose diff matches the regex |
| `--question TEXT` | none | Keywords matched against commit messages |
| `--limit N` | 200 | Maximum commits (minimum 1) |
| `--since DATE` | none | Restrict the window (a `--since` run cannot establish the introduction) |
| `--repo PATH` | cwd | Repository |

## bisect

### bisect plan

| Flag | Default | Meaning |
|---|---|---|
| `--good REF` | none | Known-good ref; repeatable |
| `--bad REF` | `HEAD` | Known-bad ref |
| `--test CMD` | none | Test command; validated and echoed, never executed |
| `--repo PATH` | cwd | Repository |

### bisect status

`bisect status [--repo PATH]`. Anything other than `plan` or `status` is a usage error.

## commits

| Flag | Default | Meaning |
|---|---|---|
| `--staged` | off | Analyse only what is already staged |
| `--repo PATH` | cwd | Repository |

## blast

| Argument | Default | Meaning |
|---|---|---|
| `PATH ...` | working-tree changes | Files to analyse |
| `--base REF` | none | Analyse the branch diff `REF...HEAD` |
| `--depth N` | 3 | Maximum graph depth |
| `--max-nodes N` | 200 | Node cap |
| `--timeout S` | 15.0 | Time budget in seconds |
| `--repo PATH` | cwd | Repository |

## conflict

`conflict [--repo PATH]`. No other flags.

## temporal

| Flag | Default | Meaning |
|---|---|---|
| `--base REF` | none (working tree vs HEAD) | Analyse the branch diff `REF...HEAD` |
| `--limit N` | 20 | Maximum findings |
| `--budget N` | 25 | Maximum history probes (the code caps it at 60, minimum 1) |
| `--timeout S` | 30.0 | Time budget in seconds |
| `--repo PATH` | cwd | Repository |

## memory

| Subcommand | Arguments |
|---|---|
| `index` | `[--max-commits N] [--rebuild]` |
| `status` | none |
| `cochange` | `<path> [--limit N=20]` |
| `hotspots` | `[--limit N=20]` |
| `churn` | `[PREFIX] [--limit N=20]` |
| `introduced` | `<path>` |
| `reverts` | `[--limit N=20]` |
| `authors` | `<path>` |
| `sessions` | `[--limit N=10]` |
| `forget` | `[--yes]` (refuses to delete without it) |

Every subcommand also takes `--repo PATH`. Paths may be repo-relative, cwd-relative or absolute.
Every subcommand except `status`, `sessions` and `forget` needs `memory_enabled: true`.

## guard

```
warp.py guard check "<command>" [--repo PATH] [--branch NAME] [--mode standard|strict] [--protected a,b,...]
```

| Flag | Default | Meaning |
|---|---|---|
| `<command>` | required | The shell command string to classify. It is not executed |
| `--repo PATH` | cwd | Repository for `.claude/git-warp.local.md` and the current branch |
| `--branch NAME` | looked up | Assume this current branch |
| `--mode` | from config | Override `safety_mode` |
| `--protected LIST` | from config | Comma-separated protected-branch globs; overrides `protected_branches` |

Output keys: `decision` (`allow|ask|deny`), `rule`, `operation`, `reason`, `safer`, `commands`, `branch`,
`safety_mode`.
