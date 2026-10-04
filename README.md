# Git Warp

**Git Warp gives Claude Code a memory of a repository's past and a safety system for its future.**

Git Warp is a [Claude Code](https://claude.com/claude-code) plugin. It has two halves:

- **A deterministic safety layer.** Hooks inspect every Bash command Claude is about to run and block or ask about
  destructive Git operations (`git reset --hard`, `git clean -f`, force pushes to protected branches, and so on).
  The decision is made by ordinary, tested code, not by a model.
- **Deterministic Git evidence plus model judgment.** A small Python CLI (`scripts/warp.py`) gathers facts from Git as
  JSON (state, risk signals, lost-commit candidates, history timelines, import graphs, conflict ancestry, a local
  history index). Skills tell Claude how to run that CLI and how to interpret and present the result.

> Status: version `0.1.0` (see `.claude-plugin/plugin.json` and [CHANGELOG.md](CHANGELOG.md)). Validated on macOS only;
> Linux is not yet validated and Windows is unsupported for this release ([docs/platforms.md](docs/platforms.md)).
> Not listed in any public marketplace: install from this repository as described below.

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
so git 2.36+; POSIX `fcntl` locking is used when available), but older versions, Linux and Windows were **not
tested**; Windows is unsupported for this release. See [docs/platforms.md](docs/platforms.md).

## Installation

### Local

Clone the repository and point Claude Code at it for one session:

```bash
git clone https://github.com/RajNori/git-warp.git
claude --plugin-dir /path/to/git-warp
```

`.claude-plugin/plugin.json` is the manifest; `hooks/hooks.json`, `skills/` and `agents/` are discovered from the
plugin root. `claude plugin validate /path/to/git-warp --strict` passes.

### Marketplace

Git Warp is **not listed in any public marketplace**, and this repository contains no `marketplace.json`. The
install-as-a-user flow was verified with a local marketplace: put a copy of the plugin in a directory that has
`.claude-plugin/marketplace.json` listing it with `"source": "./git-warp"`, then

```bash
claude plugin marketplace add /path/to/that/directory
claude plugin install git-warp@<marketplace-name>
claude plugin details git-warp@<marketplace-name>   # 10 skills, 3 agents, 4 hooks
```

That run used an isolated `CLAUDE_CONFIG_DIR` and `claude plugin validate` accepted the result. Details and the
publishing checklist: [docs/marketplace.md](docs/marketplace.md).

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
| 3 agents | `agents/*.md` | Analysts Claude can delegate to: `git-forensic-analyst`, `git-history-analyst`, `git-risk-analyst`. Their tools are `Read, Grep, Glob` only: they have **no shell** and analyse the CLI output the calling assistant passes in |
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
**DENY** (blocked), **ASK** (the user must confirm) or **DEFER** (no objection: ordinary Claude permissions decide;
it is *not* an approval). Dynamic expressions that could conceal a destructive operation (`git reset $(echo --hard)`)
are ASK, not DEFER. If the guard itself errors, a command that mentions `git` becomes **ask**. It also exposes a debug command that never runs anything:

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

Optional files `~/.claude/git-warp.local.md` (user) and `.claude/git-warp.local.md` in the repository root. Policy is
built from the built-in floor, then the user file, then the repository file, and a repository can only tighten it
(see [docs/configuration.md](docs/configuration.md)). The repository file lives in the repository root (git-ignored by this repo's own `.gitignore`
pattern `.claude/*.local.md`; add the same to yours if you do not want it committed). It uses a small YAML-like
frontmatter parsed by `core/config.py` (scalars, inline lists `[a, b]`, and `- item` block lists). Text after the
closing `---` is ignored. Unknown keys and invalid values are ignored with a warning and the default is kept.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `protected_branches` | list of globs | `main, master, develop, development, production, prod, release` | Branches the guard protects (force push, forced fetch, delete, reset, `update-ref`). Your entries are **added** to the defaults; an empty list is ignored |
| `safety_mode` | `standard` or `strict` | `standard` | In `strict`, history-rewriting `ask` verdicts become `deny`; the strictest of user and repository wins |
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
noted). `deny` blocks the command; `ask` prompts the user; everything not listed is `defer` (no objection from Git Warp;
Claude Code's ordinary permission rules decide, and a `deny`/`ask` is never overridden by a pre-approved `Bash(...)` rule).

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
| deny | `git read-tree --reset -u ...` | `read-tree-reset` |
| deny | `git rm -rf .` (whole-tree `git rm` with force) | `rm-tree` |
| deny | `git checkout-index -f -a` | `checkout-discard-all` |
| deny | `rm -rf` of any path ending in `.git` (so `rm -rf foo.git` is a known false positive) | `rm-git` |
| deny | the same destructive Git forms reached through variables or unknown launchers, e.g. `$GIT reset --hard`, `GIT=git; $GIT reset --hard`, `git reset --hard$IFS`, `arch git reset --hard`, `flock x git clean -fd` (literal `git` word in the tail of an unknown launcher is classified) | `reset-hard`, `clean-force`, ... |
| ask | `git push --force` / `-f` / `--force-with-lease` to a non-protected branch | `push-force` |
| ask | `git push --delete <non-protected>` | `push-delete` |
| deny | `git fetch --force origin main:main`, `git fetch origin +refs/heads/*:refs/heads/*` (forced fetch into a protected local branch) | `fetch-force-protected` |
| ask | forced fetch into another local branch; `git pull --force` / `-f` | `fetch-force-local` |
| ask | `git reset $(echo --hard)`, ``git reset `echo --hard` ``, `git clean $(echo -fdx)`: a dynamic word in an option, subcommand or executable position of a destructive-capable subcommand | `dynamic-argument` |
| ask | `echo 'git reset --hard' > gen.sh && bash gen.sh` (a script written and run in one command) | `generated-script` |
| ask | `bash <(echo '...')` | `stdin-script` |
| ask | `git x`, `git nuke`, `git st`: a first word that is not a known Git subcommand may be a configured alias | `unknown-subcommand` |
| ask | `git rebase ...` | `rebase` |
| ask | `git commit --amend` | `commit-amend` |
| ask | `git branch -D <b>` | `branch-force-delete` |
| ask | `git branch -f ...` / `-M`; `git checkout -B` / `git switch -C` onto a protected or dynamic (`$X`) target | `branch-force-move` |
| ask | `git update-ref --stdin` | `update-ref-stdin` |
| ask | `git push --prune` | `push-prune` |
| ask | `git read-tree --reset` (without `-u`) | `read-tree-reset-index` |
| ask | `git rm -r .` (whole-tree, no force) | `rm-tree-ask` |
| ask | `git checkout-index -f <path>` | `checkout-index-force` |
| ask | interpreter `-c`/`-e` strings containing a destructive git phrase (`python3 -c "... git reset --hard ..."`) | `interpreter-git` |
| ask | an option word with a dynamic suffix on a destructive-capable subcommand when the literal part does not already prove it destructive | `option-unresolved` |
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
| defer | `git status`, `git log`, `git add`, `git commit`, `git push` (no force), `git switch -c`, `git stash push`, `git gc`, `git branch -d`, `git clean -n` / `-nfd` (dry runs), `git reset --soft`, `git log $(git merge-base HEAD main)..HEAD` | none |

With `safety_mode: strict`, these `ask` rules become `deny`: `rebase`, `commit-amend`, `branch-force-delete`,
`branch-force-move`, `push-force`, `push-delete`, `reset-protected`, `update-ref-protected`. The `deny` rules do not
depend on the mode.

Things the guard allows that you might expect it to stop (each verified with `guard check`, see
[Limitations](#limitations)): `git checkout -- <one file>`, `git restore <one file>` (discarding one named file's
edits), `git rm -f <one file>`, `git fetch -f origin main` (no `src:dst` refspec), `git checkout -B topic` (non-protected
literal target) and `git reset <rev>` or `git reset --soft` off a protected branch. A dynamic expression the guard cannot
resolve is never silently deferred in a destructive-capable position: `cmd="git reset --hard"; $cmd` is `deny` and
`git reset $(echo --hard)` is `ask`.

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

The directory is created `0700` and every file `0600`; a symlink, FIFO, socket, device or unexpected directory in place of a
state file is refused (never followed, deleted or replaced); `state.json` is replaced atomically; a corrupt `warp.db`
is moved aside as `warp.db.corrupt` rather than deleted. See [docs/privacy.md](docs/privacy.md).

Never stored by Git Warp: file contents, edit strings, prompts, tool output, environment variables, or the
Git config. Redaction (`core/redact.py`) is applied before anything is persisted and to all CLI and hook output; it is pattern based
and can miss unusual secret formats. The index does contain commit author names and emails and commit subjects, because those
are what it indexes. To delete the index and recorder: `python3 scripts/warp.py memory forget --yes` (removes
`warp.db` and its sidecar files and `flight-recorder.jsonl`/`.1`; `state.json` and the `.git/git-warp/` directory
remain, delete them by hand if you want them gone). Full details: [docs/privacy.md](docs/privacy.md).

## Architecture overview

```
Claude Code --hooks--> scripts/hook_*.py --> gitwarp/hooks/*      (deterministic, fail-safe)
     |
     +--skills--> SKILL.md tells Claude to run  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/warp.py <cmd>
                  --> gitwarp/{analysis,history,semantic,memory,safety}/cli.py  (evidence as JSON)
                  Claude interprets the JSON and writes the answer
All Git access --> gitwarp/core/git.py       (the one Git process boundary; docs/git-boundary.md)
All revisions  --> gitwarp/core/revisions.py (the one normalisation path)
All state I/O  --> gitwarp/core/storage.py   (the one secure-storage layer)
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

`pytest` is not bundled; install it yourself. Result at release: **3109 passed** (the Phoenix baseline was 1345; no baseline test was skipped or loosened, see [planning/BASELINE_TEST_AUDIT.md](planning/BASELINE_TEST_AUDIT.md)). The suite includes unit,
integration (real temporary repositories and the hook scripts through subprocess), security (guard bypass attempts and
hostile-repository / hostile-environment contracts), acceptance (a repository-state matrix, recovery fixtures and a
Guardian corpus), crash/concurrency and scale tests. `tests/live/live_acceptance.py` is an opt-in runner that drives
real headless Claude Code sessions (it costs API money and is not part of `pytest`); its record is
[planning/LIVE_ACCEPTANCE.md](planning/LIVE_ACCEPTANCE.md). Use `PYTHONDONTWRITEBYTECODE=1` to avoid `__pycache__`.

## Troubleshooting

Short version (full list in [docs/troubleshooting.md](docs/troubleshooting.md)):

- Every CLI error is JSON with an `error` key and a non-zero exit code. Read the `hint` if present.
- `not a git repository`: run inside a repo or pass `--repo PATH`.
- A Git command was blocked or prompted: that is the guard. Check what it matched with `warp.py guard check "<cmd>"`.
- `memory ...` says memory is disabled: `memory_enabled: false` in `.claude/git-warp.local.md`.
- Memory index looks wrong: `warp.py memory index --rebuild`.
- Configuration seems ignored: run `warp.py memory status` and look at `warnings` and `enabled`.

## Limitations

- **The guard is a safety net, not a sandbox, and it does not emulate a shell.** It only inspects the command string
  of the `Bash` tool. It cannot see inside scripts (`./cleanup.sh`, `make clean`), values that only exist at run time,
  `curl ... | sh`, `docker exec`/`ssh` wrappers, other tools (Write/Edit, MCP, IDE actions), or non-Git destruction other
  than `rm -rf` of a `.git` path. Where a dynamic expression could conceal destruction it asks instead of guessing.
  Full list: [docs/guard-limitations.md](docs/guard-limitations.md).
- Single-file discards (`git checkout -- file`, `git restore file`, `git rm -f file`), `git clean -i` and non-hard
  resets off protected branches are deferred (not flagged).
- Policy: a repository's `.claude/git-warp.local.md` can only *tighten* the policy (more protected branches, stricter
  mode); it cannot remove the built-in floor or any unconditional rule ([docs/configuration.md](docs/configuration.md)).
- Git Warp's **own** Git calls neutralise repository-configured `core.fsmonitor`, textconv, external diff, pagers,
  `core.hooksPath` and filters and ignore ambient `GIT_*` variables ([docs/git-boundary.md](docs/git-boundary.md) lists
  what is *not* suppressed, e.g. `include.path`, user-level filters such as git-lfs). Raw `git` that Claude runs through
  Bash is not covered by that boundary.
- The three agents have no shell (Claude Code cannot scope Bash in agent frontmatter). Skills pre-approve scoped
  read-only prefixes such as `git diff/log/show`; a prefix cannot exclude flags, so `--output=<file>` on those can
  overwrite a file and repository-configured helpers can run when Claude runs raw `git status/diff/blame`.
- Analyses are heuristic where they say so (clusters, "sensitive" path words such as `billing` or `auth`, missing
  tests, dependency drift, fix-like messages, co-change, hotspots). Risk levels are rule outcomes, not probabilities.
- Shallow clones give truncated history; commands report this in `warnings`.
- Rescue cannot recover edits that were never committed or staged, and cannot see objects already removed by
  `git gc`/`prune`.
- Blast radius covers Python and JS/TS imports plus a generic text fallback; dynamic imports and cross-repo
  consumers are invisible.
- Only tested on macOS with Python 3.13 and git 2.53. Linux is not yet validated; Windows is unsupported for this
  release ([docs/platforms.md](docs/platforms.md)).
- Not listed in a public marketplace; install from the repository (version `0.1.0`).
- The plugin does not run your tests, bisect predicates or repository scripts.

## Documentation index

- [docs/getting-started.md](docs/getting-started.md)
- [docs/safety-model.md](docs/safety-model.md) and [docs/guard-limitations.md](docs/guard-limitations.md)
- [docs/commands.md](docs/commands.md) and [docs/cli-reference.md](docs/cli-reference.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/privacy.md](docs/privacy.md)
- [docs/git-boundary.md](docs/git-boundary.md)
- [docs/platforms.md](docs/platforms.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)
- [docs/marketplace.md](docs/marketplace.md)
- [examples/](examples/) (real captured outputs, trimmed)
- [CHANGELOG.md](CHANGELOG.md)

Project history and verification evidence: [CHANGELOG.md](CHANGELOG.md), [planning/](planning/).

## License

MIT. See [LICENSE](LICENSE).
