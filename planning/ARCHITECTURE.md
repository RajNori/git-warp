# Git Warp Architecture

## Product boundary

Git Warp is a Claude Code plugin backed by a Python standard-library runtime. Skills explain investigative workflows; deterministic Python modules own Git subprocess execution, safety classification, structured analysis, and persistence. Claude interprets evidence and proposes actions. Analysis commands never mutate repository refs or the index.

## Runtime layers

1. **Claude integration:** `.claude-plugin/plugin.json`, `hooks/hooks.json`, hook entry points in `scripts/`, skill definitions, and read-only agent profiles.
2. **Protocol/adapters:** decode event JSON, resolve the repository from the event's `cwd` or validated process cwd, call services, and encode event-correct output. Malformed or oversized input is handled explicitly.
3. **Core Git API (`scripts/git_warp/git.py`):** argv-only subprocess calls; explicit cwd, timeout, return code, stdout/stderr; typed `GitResult`/`GitError`; focused repo queries. No shell interpolation and no mutation by default.
4. **Services:** safety, forensics, PR/change analysis, semantic heuristics, and memory; each consumes the shared Git API and returns evidence-bearing structures.
5. **Persistence:** runtime files only below the resolved Git common directory's `git-warp/`; SQLite/JSONL writes are bounded, atomic where practical, and redact/allowlist values.

## Shared contracts

- Core interface (implemented by the Git layer owner): `run_git(args: Sequence[str], *, cwd: str | Path, timeout: float) -> GitResult`; `repo_root`, `git_dir`, `git_common_dir`, `current_branch`, `head_commit`, `status_porcelain`, and `repo_info` all require keyword-only explicit `cwd` and `timeout`.
- `GitResult`: `args`, `cwd`, `returncode`, `stdout`, `stderr`, `duration_ms`; nonzero exits remain structured results. Process-start/timeout failures raise typed execution/timeout errors; checked repo queries raise a typed command error retaining diagnostics.
- Additional Git reads are implemented as focused functions over `run_git` (upstream/ahead-behind, changed paths/diffs, merge-base, metadata, reflog, stash/worktree, blame/show/log/branches/remotes). Never add shell interpolation.
- Repository selection: hook `cwd` is untrusted input. Resolve it to a real path, require it to be inside a Git worktree, then use `git rev-parse --show-toplevel` and `--git-common-dir`. Never concatenate it into a shell command.
- Safety classification: pure function from a tokenized command segment plus `SafetyContext` to `Decision` (`ALLOW`, `ASK`, `DENY`) and stable reason codes/messages. Parse uncertainty involving Git command execution yields `ASK`; a clearly destructive match yields `DENY`.
- Hook output: `PreToolUse` is either `{}` (normal Claude permission flow) or the documented `hookSpecificOutput` decision envelope. Hooks do not auto-allow commands.
- Analysis result: findings carry `level`, `claim`, `evidence` (paths/SHAs/commands), and `certainty` (`FACT`, `INFERENCE`, `UNKNOWN`) where applicable. Do not synthesize unobserved test results or intent.
- Read-only service contract: analyzers run read-only Git commands only. Explicit opt-in recovery commands are shown to the human; the tool never executes them automatically.
- Recorder contract: persist timestamp, event class, tool category/name, branch, HEAD, path list (bounded and redacted), and declared outcome fields only. Never persist prompts, complete `tool_input`, source text, environment values, or credentials.

## Dependency direction

Claude adapters -> services -> Git API/models. Safety classifier remains independently testable and does not depend on Claude or invoke Git to parse a command. Recorder is a leaf service. Skills describe use of these capabilities and do not reproduce deterministic policy.

## Initial supported scope

macOS/Linux, Python 3.10+ standard library, Git CLI, and SQLite. JavaScript/TypeScript/Python receive import/dependency heuristics; other languages receive path/history heuristics with explicit limits.
