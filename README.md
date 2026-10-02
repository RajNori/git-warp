# Git Warp

**Git Warp gives Claude Code a memory of a repository's past and a safety system for its future.**

Git Warp is a [Claude Code](https://claude.com/claude-code) plugin. It has two halves:

- **A deterministic safety layer.** Hooks inspect every Bash command Claude is about to run and block or ask about
  destructive Git operations (`git reset --hard`, `git clean -f`, force pushes to protected branches, and so on).
  The decision is made by ordinary, tested code, not by a model.
- **Deterministic Git evidence plus model judgment.** A small Python CLI (`scripts/warp.py`) gathers facts from Git as
  JSON (state, risk signals, lost-commit candidates, history timelines, import graphs, conflict ancestry, a local
  history index). Skills tell Claude how to run that CLI and how to interpret and present the result.

> Status: version `0.1.0` (see `.claude-plugin/plugin.json`). **Not published to any marketplace yet.** Install it
> locally as described below. See [CHANGELOG.md](CHANGELOG.md) and [docs/marketplace.md](docs/marketplace.md).

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [What you get](#what-you-get)
- [Features](#features) (Git Guardian, X-Ray, Rescue, Archaeology, AI Bisect, PR Engineer, Semantic Commit Composer,
  Blast Radius, Conflict Surgeon, Temporal Code Review, Flight Recorder, Repository Memory)
- [Configuration](#configuration)
- [Safety model](#safety-model)
- [What is stored and where](#what-is-stored-and-where)
- [Architecture overview](#architecture-overview)
- [Commands and skills table](#commands-and-skills-table)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Documentation index](#documentation-index)
- [License](#license)

## Requirements

- Claude Code (version `2.1.285` was the one on the author's machine while these docs were written; other versions were not checked).
- `python3` on `PATH`. Standard library only: there are no Python dependencies to install.
- `git` on `PATH`.
- Python's `sqlite3` module linked against SQLite 3.24 or newer (the memory index uses
  `INSERT ... ON CONFLICT DO UPDATE`).

What was actually verified: Python 3.13.2, git 2.53.0, SQLite 3.51.0 (the version Python reports), macOS (Darwin).
Reading the code shows a few lower bounds (the walrus operator, so Python 3.8+; `git worktree list --porcelain -z`,
so git 2.36+; POSIX `fcntl` locking is used when available), but older versions and Windows were **not tested**.

## Installation

### Local (the only supported path today)

Clone the repository and point Claude Code at it for one session:

```bash
git clone https://github.com/RajNori/git-warp.git
claude --plugin-dir /path/to/git-warp
```

`.claude-plugin/plugin.json` is the manifest; `hooks/hooks.json`, `skills/` and `agents/` are discovered from the
plugin root. `claude plugin validate /path/to/git-warp` accepts the manifest (that was the result at the recovery
checkpoint and again while writing these docs).

### Marketplace

Git Warp is **not published to any marketplace**. Per `claude plugin install --help`, installation from a
marketplace uses `claude plugin install <plugin>` or `claude plugin install <plugin>@<marketplace>`, and per
`claude plugin marketplace --help` marketplaces are managed with `claude plugin marketplace add|list|remove|update`
(`add` takes "a URL, path, or GitHub repo"). Until a marketplace lists `git-warp`, those commands cannot install it.
This repository contains no `marketplace.json`. The publishing checklist is in
[docs/marketplace.md](docs/marketplace.md).

### Skill names

In a headless Claude Code session the skills were listed namespaced as `plugin:skill`, for example
`git-warp:git-xray` and `git-warp:git-rescue`. This README uses that form. Whether a shorter form (`/git-xray`)
also works depends on Claude Code, not on this plugin, and was not verified. You can also just describe the task
("what's risky in this branch?") and Claude can pick the matching skill from its description.

First steps are in [docs/getting-started.md](docs/getting-started.md).

## What you get

| Piece | Where | What it does |
|---|---|---|
| 4 hooks | `hooks/hooks.json`, `scripts/hook_*.py` | `PreToolUse` guard for `Bash`; `SessionStart` context; `PostToolUse` flight recorder; `Stop` change report |
| 10 skills | `skills/*/SKILL.md` | Drive the CLI and shape the answer (see the table below) |
| 3 agents | `agents/*.md` | Read-only analysts Claude can delegate to: `git-forensic-analyst`, `git-history-analyst`, `git-risk-analyst` |
| 1 CLI | `scripts/warp.py` | JSON evidence: `xray pr rescue archaeology bisect commits blast conflict temporal memory guard` |

The CLI is read-only except for two documented cases: `rescue preserve` creates one new branch, and
`memory forget --yes` deletes Git Warp's own local state files. Several commands also write Git Warp's own state
under `.git/git-warp/` (the memory index is created or refreshed by `memory ...` queries, the temporal analysis and
the SessionStart hook).

## Features

Each feature below lists what is implemented. "Skill" is the skill that drives it; "CLI" is the underlying
`warp.py` call. Full flag lists: [docs/cli-reference.md](docs/cli-reference.md); per-command detail and observed
JSON keys: [docs/commands.md](docs/commands.md); real captured runs: [examples/](examples/).

### Git Guardian (safety hook)

A `PreToolUse` hook on the `Bash` tool (`scripts/hook_git_guard.py` -> `gitwarp/hooks/git_guard.py` ->
`gitwarp/safety/{tokenizer,classifier}.py`). It tokenizes the command string (quotes, `;`, `&&`, `||`, pipes,
subshells, `$( )`, backticks, `bash -c`, literal `eval`, `xargs`, `find -exec`, wrappers such as `sudo`/`env`,
`git -C`, `git -c`) and classifies every Git invocation it finds, returning the most severe verdict:
**deny** (blocked), **ask** (the user must confirm) or **allow** (silent). If the guard itself errors, a command
that mentions `git` becomes **ask**. It also exposes a debug command that never runs anything:

```bash
python3 scripts/warp.py guard check "git reset --hard HEAD~1"
```

Details and the full rule table: [Safety model](#safety-model) and [docs/safety-model.md](docs/safety-model.md).
It only sees Bash command strings; see [Limitations](#limitations).

### Git X-Ray (`git-warp:git-xray`)

Read-only snapshot of repository state and risk: branch, upstream, ahead/behind, operation in progress, worktrees,
stashes, working-tree counts, high-churn files, sensitive paths, migration/lockfile/config signals, change
clusters, recovery options, a LOW/MEDIUM/HIGH risk level with named drivers, and recommendations.
CLI: `warp.py xray [--untracked-all] [--repo PATH]`. Example: [examples/xray.md](examples/xray.md).

### Git Rescue (`git-warp:git-rescue`)

Finds work that looks lost. `rescue scan` reads reflogs, stashes, `ORIG_HEAD` and (unless `--no-fsck`) `git fsck`
dangling commits and ranks candidates with a `high|medium|low` confidence label and reasons (labels, not
probabilities). `rescue inspect <sha>` shows one candidate; `rescue preserve <sha>` creates a new branch
`rescue/<date>-<sha8>` pointing at it. `preserve` refuses to move an existing ref, never touches HEAD, the index or
the work tree, and supports `--dry-run`. Uncommitted, never-staged edits are not stored by Git and cannot be
recovered. Example: [examples/rescue-scan.md](examples/rescue-scan.md).

### Git Archaeology (`git-warp:git-archaeology`)

Explains how a file, directory, symbol or pattern came to be: introduction commit, renames, deleted-then-restored
history, reverts, fix-like commits, large rewrites, merge points, blame survival and an authorship timeline, each
tagged `fact` or `inference`, plus an `unknown` list and `commits_worth_reading`. Works for deleted paths.
CLI: `warp.py archaeology <path> | --symbol NAME | --regex RE | --question TEXT [--since] [--limit]`.
Authorship is contribution history, not ownership. Example: [examples/archaeology.md](examples/archaeology.md).

### AI Bisect (`git-warp:git-bisect-ai`)

Plans a bisect without running it. `bisect plan --good REF --bad REF [--test CMD]` validates the refs and range,
reports blockers (dirty tree, operation in progress, good not an ancestor of bad, unknown ref), reproducibility
risks (lockfile/manifest/migration changes), predicate guidance and the exact commands to run in an isolated
worktree. The `--test` string is validated syntactically and echoed, **never executed**. `bisect status` reports
progress of a bisect you started. Git Warp never starts a bisect.

### PR Engineer (`git-warp:git-pr`)

Turns `merge-base(base, HEAD)..HEAD` into facts for a reviewer-ready PR description: commits and Conventional
Commit conformance, files, clusters, tags (migration, lockfile, sensitive, test), API-surface and debug-leftover
heuristics, secret findings (path and line only, values never printed), rollback facts, risk level, and
`not_included` (uncommitted work). Git Warp never runs tests (`testing_evidence.ran_by_git_warp` is `false`).
CLI: `warp.py pr [BASE | --base REF]`. Example: [examples/pr.md](examples/pr.md).

### Semantic Commit Composer (`git-warp:git-commits`)

Groups the working-tree change set (or the index with `--staged`) into clusters and proposes an ordered list of
Conventional Commit skeletons with `git add -- <paths>` / `git commit` command text. It stages and commits nothing;
messages contain `<placeholders>` and `needs_type_decision` flags for Claude to resolve.
CLI: `warp.py commits [--staged]`. Example: [examples/commits.md](examples/commits.md).

### Blast Radius (`git-warp:git-blast-radius`)

Builds a static import/reference graph outward from the changed files (Python and JS/TS adapters plus a generic
fallback) and reports dependants, tests and interface signals with a deterministic LOW/MEDIUM/HIGH level and the
rules behind it. Edges are labelled `import-graph` (static) or heuristic (`naming`, `text-match`).
CLI: `warp.py blast [paths...] [--base REF] [--depth N] [--max-nodes N] [--timeout S]`.
Example: [examples/blast.md](examples/blast.md).

### Conflict Surgeon (`git-warp:git-conflict`)

For an active merge, rebase, cherry-pick or revert conflict: reports which side is `ours`/`theirs` (including the
rebase swap), the merge base, per-file conflict type, index stages, conflict regions with ours/theirs/base text and
blame, per-side commit history and a deterministic `relationship_hint`. Analysis only; the skill proposes a
resolution and does not edit files unless asked. CLI: `warp.py conflict`.

### Temporal Code Review (`git-warp:git-temporal-review`)

Searches history for evidence relevant to the current change: earlier removals of the same code, fix commits,
reverts and dependency flip-flops, using `git log -S` probes within a budget and the memory index. Each finding
carries cited commits, a `match_quality` and a caveat; with no evidence it says so and states that this is not proof
of safety. CLI: `warp.py temporal [--base REF] [--limit N] [--budget N] [--timeout S]`.

### Flight Recorder (hooks; `memory sessions`)

The `PostToolUse` hook (matcher `Write|Edit|MultiEdit|NotebookEdit|Bash`) appends one redacted JSON line per tool
call to `.git/git-warp/flight-recorder.jsonl`: timestamp, session id, tool name and category, repo-relative file
path for edits, a redacted and truncated (300 chars) Bash command, branch and short head. It never stores file
contents, edit strings, prompts, tool output or environment. Default retention is 30 days and the file is capped
at 5 MiB with one rotated generation. The `SessionStart` hook also injects compact repository context (branch,
upstream, working-tree counts, stashes, worktrees, last 5 commits and a short policy reminder), and the `Stop`
hook emits a short end-of-turn change report when the working tree has changed. Read it back with
`warp.py memory sessions`. Details: [docs/privacy.md](docs/privacy.md).

### Repository Memory (`git-warp:git-memory`)

A local SQLite index of Git history (`.git/git-warp/warp.db`) answering: `cochange <path>`, `hotspots`,
`churn [prefix]`, `introduced <path>`, `reverts`, `authors <path>`, `sessions`, plus `index [--max-commits N]
[--rebuild]`, `status` and `forget --yes`. Hotspot scores are a relative ranking formula, not a defect prediction;
co-change is correlation; authors are contribution history, not ownership. Example:
[examples/memory-hotspots.md](examples/memory-hotspots.md).

## Configuration

Optional file `.claude/git-warp.local.md` in the repository root (git-ignored by this repo's own `.gitignore`
pattern `.claude/*.local.md`; add the same to yours if you do not want it committed). It uses a small YAML-like
frontmatter parsed by `core/config.py` (scalars, inline lists `[a, b]`, and `- item` block lists). Text after the
closing `---` is ignored. Unknown keys and invalid values are ignored with a warning and the default is kept.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `protected_branches` | list of globs | `main, master, develop, development, production, prod, release` | Branches the guard protects (force push, delete, reset, `update-ref`). An empty list is rejected and the default kept |
| `safety_mode` | `standard` or `strict` | `standard` | In `strict`, history-rewriting `ask` verdicts become `deny` |
| `sensitive_paths` | list of globs | `[]` | Extra paths tagged `sensitive` |
| `ignored_paths` | list of globs | `[]` | Extra paths tagged `generated` (excluded from several analyses) |
| `test_paths` | list of globs | `[]` | Extra paths tagged `test` |
| `infra_paths` | list of globs | `[]` | Extra paths tagged `infra` |
| `memory_enabled` | bool | `true` | `false` disables the history index and `memory` queries |
| `recorder_enabled` | bool | `true` | `false` disables the flight recorder |
| `recorder_retention_days` | integer 0-3650 | `30` | Recorder retention; `0` disables recording |

Example:

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
```

Booleans accept `true/yes/on` and `false/no/off`. Full reference: [docs/configuration.md](docs/configuration.md).

## Safety model

The guard classifies Git invocations found in a Bash command string. Verdicts below were produced with
`python3 scripts/warp.py guard check "<cmd>" --branch <branch>` (standard mode, default protected branches unless
noted). `deny` blocks the command; `ask` prompts the user; everything not listed is `allow`.

| Decision | Command (examples) | Rule |
|---|---|---|
| deny | `git reset --hard ...` (any branch) | `reset-hard` |
| deny | `git clean -f`, `-fd`, `-fdx` | `clean-force` |
| deny | `git checkout .`, `git restore .`, `git checkout main -- .` (whole-tree discard) | `checkout-discard-all` |
| deny | `git checkout -f ...` / `--force` | `checkout-force` |
| deny | `git switch -f ...` / `--discard-changes` | `switch-discard` |
| deny | `git reflog expire ...`, `git reflog delete ...` | `reflog-destroy` |
| deny | `git gc --prune=now` | `gc-prune-now` |
| deny | `git prune` | `prune` |
| deny | `git stash clear` | `stash-clear` |
| deny | `git update-ref -d refs/heads/...` | `update-ref-delete` |
| deny | `git push --force` / `-f` / `+ref` / `--force-with-lease` to a protected branch | `push-force-protected` |
| deny | `git push --mirror` | `push-mirror` |
| deny | `git push --delete` of a protected branch | `push-delete-protected` |
| deny | `git filter-branch`, `git filter-repo` | `history-tool` |
| deny | `rm -rf .git` | `rm-git` |
| ask | `git push --force` / `-f` / `--force-with-lease` to a non-protected branch | `push-force` |
| ask | `git push --delete <non-protected>` | `push-delete` |
| ask | `git rebase ...` | `rebase` |
| ask | `git commit --amend` | `commit-amend` |
| ask | `git branch -D <b>` | `branch-force-delete` |
| ask | `git branch -f ...` / `-M` | `branch-force-move` |
| ask | `git reset <rev>` (non-hard) while on a protected branch | `reset-protected` |
| ask | `git update-ref <protected ref> ...` | `update-ref-protected` |
| ask | `git stash drop` | `stash-drop` |
| ask | `git update-ref -d <non-branch ref>` (for example a tag) | `update-ref-delete-other` |
| ask | `git tag -d` / `-f` | `tag-rewrite` |
| ask | `git worktree remove --force` | `worktree-force-remove` |
| ask | `git submodule deinit --force` | `submodule-deinit-force` |
| ask | `git config alias.*`, `git -c alias.*=`, `GIT_CONFIG_KEY_n=alias.*` | `alias-config`, `alias-inline`, `env-alias` |
| ask | `git remote set-url` / `remove` | `remote-modify` |
| ask | unresolved `$VAR` as Git subcommand or as `reset`/`clean` argument; computed `eval`/`bash -c` that mentions git; over-long or too deeply nested input that mentions git | `unresolved-subcommand`, `reset-unresolved`, `clean-unresolved`, `eval-git`, `shell-git`, `too-complex` |
| allow | `git status`, `git log`, `git add`, `git commit`, `git push` (no force), `git switch -c`, `git stash push`, `git gc`, `git branch -d`, `git clean -n`, `git reset --soft` | none |

With `safety_mode: strict`, these `ask` rules become `deny`: `rebase`, `commit-amend`, `branch-force-delete`,
`branch-force-move`, `push-force`, `push-delete`, `reset-protected`, `update-ref-protected`. The `deny` rules do not
depend on the mode.

Things the guard allows that you might expect it to stop (observed, see [Limitations](#limitations)):
`git checkout -- <one file>`, `git restore <one file>` (discarding one named file's edits), and `git reset <rev>`
or `git reset --soft` off a protected branch.

Each `deny`/`ask` message includes safer alternatives (for example `git branch rescue/pre-reset`, `git stash push -u`,
`git clean -n`). More: [docs/safety-model.md](docs/safety-model.md) and
[docs/guard-limitations.md](docs/guard-limitations.md).

## What is stored and where

Runtime state lives only under the repository's common Git directory, `<repo>/.git/git-warp/` (shared by
worktrees, never in the work tree, never committed):

| File | Content |
|---|---|
| `warp.db` | SQLite history index: commits, files, per-commit file stats, co-change counts, authors (name, email, counts), sessions, refs |
| `flight-recorder.jsonl` (+ `.1` after rotation) | Redacted tool-call metadata, see [Flight Recorder](#flight-recorder-hooks-memory-sessions) |
| `state.json` | Small bookkeeping (last compaction time, last session id, last report hash, auto-index back-off) |

Never stored by Git Warp: file contents, edit strings, prompts, tool output, environment variables, or the
Git config. Redaction (`core/redact.py`) is applied to recorded commands; it is pattern based and can miss
unusual secret formats. The index does contain commit author names and emails and commit subjects, because those
are what it indexes. To remove everything: `python3 scripts/warp.py memory forget --yes`, or delete the directory
yourself. Full details: [docs/privacy.md](docs/privacy.md).

## Architecture overview

```
Claude Code --hooks--> scripts/hook_*.py --> gitwarp/hooks/*      (deterministic, fail-safe)
     |
     +--skills--> SKILL.md tells Claude to run  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/warp.py <cmd>
                  --> gitwarp/{analysis,history,semantic,memory,safety}/cli.py  (evidence as JSON)
                  Claude interprets the JSON and writes the answer
All Git access --> gitwarp/core/git.py  (the only module that spawns `git`)
```

Design rules: Python standard library only; deterministic code for safety decisions and evidence gathering, model
judgment only for interpretation; the CLI prints JSON (errors included); no LLM decision is the sole guard for a
destructive operation. Details: [docs/architecture.md](docs/architecture.md) and
[planning/ARCHITECTURE.md](planning/ARCHITECTURE.md), decisions in [planning/DECISIONS.md](planning/DECISIONS.md).

## Commands and skills table

| Skill (`git-warp:<name>`) | CLI | Mutates |
|---|---|---|
| `git-xray` | `xray` | no |
| `git-pr` | `pr` | no |
| `git-rescue` | `rescue scan / inspect / preserve` | `preserve` creates one branch ref |
| `git-archaeology` | `archaeology` | no |
| `git-bisect-ai` | `bisect plan / status` | no |
| `git-commits` | `commits` | no |
| `git-blast-radius` | `blast` | no |
| `git-conflict` | `conflict` | no |
| `git-temporal-review` | `temporal` | no (may refresh `warp.db`) |
| `git-memory` | `memory index / status / cochange / hotspots / churn / introduced / reverts / authors / sessions / forget` | writes `warp.db`; `forget --yes` deletes state files |
| (hook, no skill) Guardian | `guard check` | no |
| (hooks, no skill) Flight Recorder | `memory sessions` | recorder writes `flight-recorder.jsonl` |

"No" means the command does not change your repository, index, work tree or refs. Details: [docs/commands.md](docs/commands.md).

## Testing

```bash
python3 -m pytest tests -q
```

`pytest` is not bundled; install it yourself. At the recovery checkpoint (commit `da94148`) the suite reported
**1070 passed, 1 warning** (a `SyntaxWarning` in a test string literal). That number was reported to the
documentation author and was not re-run while writing these docs. The suite includes unit, integration (real
temporary repositories and the hook scripts through subprocess), security (guard bypass attempts) and
architecture-invariant tests. Use `PYTHONDONTWRITEBYTECODE=1` if you want to avoid `__pycache__` directories.

## Troubleshooting

Short version (full list in [docs/troubleshooting.md](docs/troubleshooting.md)):

- Every CLI error is JSON with an `error` key and a non-zero exit code. Read the `hint` if present.
- `not a git repository`: run inside a repo or pass `--repo PATH`.
- A Git command was blocked or prompted: that is the guard. Check what it matched with `warp.py guard check "<cmd>"`.
- `memory ...` says memory is disabled: `memory_enabled: false` in `.claude/git-warp.local.md`.
- Memory index looks wrong: `warp.py memory index --rebuild`.
- Configuration seems ignored: run `warp.py memory status` and look at `warnings` and `enabled`.

## Limitations

- **The guard is a safety net, not a sandbox.** It only inspects the command string of the `Bash` tool. It cannot see
  inside scripts (`./cleanup.sh`, `make clean`), variable expansion (`$GIT reset --hard` was *allowed* in a test run),
  computed `eval`, `curl ... | sh`, aliases defined in your global Git config (`git nuke`), `docker exec`/`ssh`
  wrappers, other tools (Write/Edit, MCP, IDE actions), or non-Git destruction other than `rm -rf .git`.
  Full list: [docs/guard-limitations.md](docs/guard-limitations.md).
- Single-file discards (`git checkout -- file`, `git restore file`) and non-hard resets off protected branches are
  allowed.
- Analyses are heuristic where they say so (clusters, "sensitive" path words such as `billing` or `auth`, missing
  tests, dependency drift, fix-like messages, co-change, hotspots). Risk levels are rule outcomes, not probabilities.
- Shallow clones give truncated history; commands report this in `warnings`.
- Rescue cannot recover edits that were never committed or staged, and cannot see objects already removed by
  `git gc`/`prune`.
- `rescue inspect` includes a `stat` string that is passed through the redactor; in a captured run the redactor
  replaced the word after `Author:` and `AuthorDate:` with `[REDACTED]` (an over-redaction, safe but lossy).
- Blast radius covers Python and JS/TS imports plus a generic text fallback; dynamic imports and cross-repo
  consumers are invisible.
- Only tested on macOS with Python 3.13 and git 2.53. Windows is untested.
- Not a published plugin yet: no marketplace listing, no release tag, version `0.1.0`.
- The plugin does not run your tests, bisect predicates or repository scripts.

## Documentation index

- [docs/getting-started.md](docs/getting-started.md)
- [docs/safety-model.md](docs/safety-model.md) and [docs/guard-limitations.md](docs/guard-limitations.md)
- [docs/commands.md](docs/commands.md) and [docs/cli-reference.md](docs/cli-reference.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/privacy.md](docs/privacy.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)
- [docs/marketplace.md](docs/marketplace.md)
- [examples/](examples/) (real captured outputs, trimmed)
- [CHANGELOG.md](CHANGELOG.md)

Project history: the implementation was recovered from session evidence at commit `da94148` and verified
(1070 tests passing at that checkpoint); this documentation was written afterwards, as new work, from the code
and from real runs.

## License

MIT. See [LICENSE](LICENSE).
