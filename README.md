# Git Warp

Git Warp is a local Claude Code plugin for Git safety checks, repository context, and evidence-based Git analysis. Its lifecycle hooks collect repository state and metadata; its skills guide Claude through read-only Git investigations.

## Load locally

From this checkout, launch Claude Code with the plugin directory:

```bash
claude --plugin-dir /path/to/git-warp
```

Validate the plugin layout with Claude Code:

```bash
claude plugin validate /path/to/git-warp
```

The hooks require `python3` and Git to be available. No Python package installation is required by the implementation in this repository. This is local loading; this checkout does not include a marketplace publishing setup.

## Skills

Claude Code exposes plugin skills under the plugin namespace, as `/<plugin>:<skill>`. The current skills are:

| Command | Purpose |
| --- | --- |
| `/git-warp:git-xray [base]` | Summarize repository state, changes, recent commits, and name-based risk signals. |
| `/git-warp:git-rescue [args]` | Find possible lost commits, branches, stashes, or overwritten work from refs, reflogs, and Git objects. |
| `/git-warp:git-archaeology [args]` | Build an evidence-backed history timeline for a path, symbol, decision, or bug. |
| `/git-warp:git-bisect-ai [args]` | Check whether a proposed bisect is ready and help define a deterministic regression predicate. It does not start a bisect. |
| `/git-warp:git-blast-radius [base]` | Find statically recognizable JavaScript, TypeScript, and Python imports of changed source files. |
| `/git-warp:git-commits [revision]` | Suggest related commit groups using commit subjects and changed-path overlap. |
| `/git-warp:git-conflict [base]` | Inspect unresolved index stages and optional merge-base evidence. |
| `/git-warp:git-memory [hotspots\|cochanges <path>\|history <path>]` | Query the local commit metadata index. |
| `/git-warp:git-pr <base>` | Prepare a reviewer-oriented analysis of the base-to-HEAD diff. |
| `/git-warp:git-temporal-review [paths...]` | Summarize the history of selected paths or changed paths. |

These skills provide instructions for Claude's analysis; they do not guarantee that every conclusion is correct. Ask for supporting Git evidence and treat interpretations as hypotheses where the history cannot prove intent or causation.

## Automatic hooks

The plugin registers four Claude Code hooks:

- **SessionStart** prints the repository root, branch, HEAD, upstream/divergence when available, changed-path count, stash count, latest reflog entry availability, and up to five recent commits. If repository inspection fails, session startup continues without this context.
- **PreToolUse (Bash)** checks shell commands that may invoke Git. It blocks selected destructive operations and asks for review on selected history-changing or ambiguous operations. It never auto-approves a command; commands outside its rules continue through Claude Code's normal permission flow.
- **PostToolUse (file edits)** records a small metadata-only event and current repository state when the working directory is in a Git repository. Recording is best-effort and does not block an edit.
- **Stop** shows a user-visible branch and changed-path summary when the working tree is dirty, including path-name signals for areas such as migrations, authentication, payments, infrastructure, and deployment. The message does not ask Claude to continue. These are naming heuristics, not a complete risk assessment.

### Safety behavior and limits

The Bash guard blocks recognized `git reset --hard`, forced `git clean`, path-restoring `git checkout`/`git restore`, reflog expiration, and `git gc --prune`. It asks for review for recognized force pushes (and blocks a proven force-push destination on a configured protected branch), remote branch deletion, forced local branch deletion, rebase, commit amend, and ambiguous Git shell/wrapper forms.

The guard parses a bounded set of shell syntax and Git options. It is a protective check, not a complete shell interpreter or Git policy engine: unrecognized wrappers, syntax, aliases, indirect tools, other Claude tools, and Git commands run outside this Claude Code session may not be covered. The built-in protected branch names are `main`, `master`, `develop`, `development`, `production`, `prod`, and `release`. A review request is a request for human approval through Claude Code, not an automatic denial. Preserve Claude Code's normal permission settings and review commands with care.

The analysis skills are read-only. In particular, rescue only identifies and inspects candidates; bisect only performs a preflight and proposes a predicate; conflict analysis does not resolve conflicts; commit grouping and PR readiness are suggestions. Skills do not automatically create refs, rewrite history, stage files, resolve conflicts, or revert changes.

## Local data and privacy

Git Warp does not send repository data to a Git Warp service. Claude may still process prompt context according to the Claude Code environment and its own settings.

The session hook prints repository metadata into Claude's session context. The PostToolUse recorder writes event time, event name, tool category, branch, HEAD, and up to 100 changed paths to `flight-recorder.jsonl` under the repository's Git common directory at `.git/git-warp/` (for linked worktrees, this is the shared Git common directory). It does not record prompts, command text, or file contents. Common credential patterns are redacted and paths resembling `.env`, secrets, or credentials are omitted; redaction is pattern-based and cannot guarantee detection of every sensitive value.

The memory index stores commit SHA, author, authored time, subject, and changed paths in `.git/git-warp/warp.db` (also under the Git common directory). It does not index source file contents or prompts. Commit metadata and paths can themselves reveal sensitive project information. Both the recorder and index attempt to restrict the `git-warp` data directory to owner-only permissions where supported. You can inspect or remove this local data yourself while Claude Code is closed.

## Memory CLI

The `/git-memory` skill invokes this CLI and incrementally indexes commit metadata before queries. You can also run it directly from a repository:

```bash
python3 /path/to/git-warp/scripts/git_memory.py --cwd . index
python3 /path/to/git-warp/scripts/git_memory.py --cwd . hotspots
python3 /path/to/git-warp/scripts/git_memory.py --cwd . cochanges src/app.py
python3 /path/to/git-warp/scripts/git_memory.py --cwd . history src/app.py
```

`index` and query actions process up to 200 commits per invocation by default; repeated calls continue indexing. Use `--batch-size N` to change the batch size and `--limit N` to change the number of query results (capped at 200). The CLI prints JSON. `cochanges` counts files recorded in the same commits; that is historical association, not proof of dependency or ownership. `hotspots` counts indexed commits per path, not edits or current risk.

The index reads and writes a bounded commit page per invocation. It counts and skips through the remaining Git history to resume, so indexing very large histories may revisit commits between batches.
