# Git Warp — Architecture

> Deterministic for safety. Agentic for judgment.

## 1. Layers

```
Claude Code ──hooks──▶ scripts/hook_*.py ──▶ gitwarp/hooks/*      (deterministic, fast, fail-safe)
     │
     └──skills──▶ SKILL.md instructs Claude to run
                 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/warp.py <cmd> ...`
                  └▶ gitwarp/{analysis,history,semantic,memory,safety}/cli.py   (deterministic evidence → JSON)
                  Claude then INTERPRETS the JSON (clustering names, risk narrative, archaeology, conflict reasoning)
All Git access ──▶ gitwarp/core/git.py   (only place that spawns `git`)
```

| Layer | Package | Responsibility |
|---|---|---|
| Git interaction | `gitwarp.core.git` | argv-only subprocess, timeouts, structured parsing, typed errors |
| Shared core | `gitwarp.core.{config,redact,paths,output}` | config, secret redaction, path classification, JSON/hook I/O |
| Hook layer | `gitwarp.hooks.*`, `scripts/hook_*.py` | stdin JSON → decision/context; must never crash Claude |
| Safety | `gitwarp.safety` | shell tokenizer + Git command classifier (deny / ask / allow) |
| Analysis | `gitwarp.analysis` | X-Ray, PR range analysis |
| History/forensics | `gitwarp.history` | rescue candidates, archaeology timelines, bisect planning |
| Semantic | `gitwarp.semantic` | change clustering, blast radius, conflict ancestry, temporal evidence |
| Persistence | `gitwarp.memory` | SQLite index (`warp.db`), flight recorder (`flight-recorder.jsonl`), state |
| Skills | `skills/*/SKILL.md` | prompts that drive the CLI and shape output |
| Agents | `agents/*.md` | read-only analysts Claude can delegate to |

## 2. Deterministic vs agentic

Deterministic (code, tested): destructive-command classification, protected-branch checks, status/ref/reflog parsing,
candidate discovery (dangling commits, reflog), import graph extraction, co-change counts, redaction, indexing.
Agentic (Claude): naming/explaining clusters, interpreting archaeology, judging semantic compatibility of conflict sides,
PR prose, temporal-review judgement, recovery recommendation. **No LLM decision is ever the sole guard for a destructive op.**

## 3. CLI contract (`scripts/warp.py`)

`warp.py <command> [args]`. Output: JSON on stdout (errors too: `{"error": "..."}` + non-zero exit). Common flags: `--repo PATH` (default cwd).
Read-only unless stated. Dispatch table lives in `scripts/warp.py` (lead-owned).

| command | module | notes |
|---|---|---|
| `xray` | analysis.cli | `--untracked-all` |
| `pr [base]` | analysis.cli | merge-base range; `--base REF` |
| `rescue` | history.cli | subcommands: `scan` (default), `inspect <sha>`; **`preserve <sha> [--name N]` is the only mutator: creates `rescue/<date>-<sha8>` branch, refuses to move existing refs** |
| `archaeology <target>` | history.cli | `--symbol`, `--regex`, `--limit` |
| `bisect` | history.cli | `plan --good REF --bad REF [--test CMD]`, `status` ; never starts bisect itself |
| `commits` | semantic.cli | clusters + commit proposals; read-only |
| `blast <paths...>` | semantic.cli | defaults to working-tree changes; `--base REF` |
| `conflict` | semantic.cli | read-only; reports stages/ancestry |
| `temporal` | semantic.cli | `--base REF` ; evidence search on added/removed patterns |
| `memory` | memory.cli | `index`, `status`, `cochange <path>`, `hotspots`, `churn`, `introduced <path>`, `reverts`, `authors <path>`, `sessions` |
| `guard` | safety.cli | `guard check "<command string>"` → classification JSON (debug/test aid) |

## 4. Module contracts (cross-agent)

### core (stable, lead-owned; request changes via lead)
`git.run/Result`, `repo_root`, `git_dir`, `common_dir`, `state_dir(cwd, create)`, `current_branch`, `head_sha`, `upstream`, `ahead_behind`,
`working_tree_status → [StatusEntry(xy,path,orig_path)]`, `changed_files`, `numstat`, `repo_operation`, `stashes`, `worktrees`, `reflog`,
`log_commits(rev_range, paths, limit, with_files, extra) → [Commit]`, `commit_metadata`, `show_commit`, `diff`, `blame`, `ls_files_stage`,
`tracked_files`, `show_file`, `merge_base`, `default_branch`, `branches`, `check_ref`. `paths.classify_path(path, cfg) → set[str]` tags.
`config.load_config(root) → Config`. `redact.redact/redact_obj`. `output.emit/fail/read_hook_event/pretool_decision/additional_context/write_hook`.
**No module other than `core/git.py` may call `subprocess`** (exceptions: `bisect`'s documented test runner is NOT executed by Git Warp at all — it only emits the command).

### semantic.cluster (producer: semantic agent; consumers: analysis, memory)
```python
@dataclass class Cluster: id:int; label:str; kind:str  # "source"|"test"|"docs"|"migration"|"infra"|"ui"|"deps"|"config"|"mixed"
                 paths:list[str]; reasons:list[str]; added:int; deleted:int
def cluster_paths(entries: list[dict], cfg: Config, repo: Path|None=None) -> list[Cluster]
#   entries: [{"path": str, "added": int, "deleted": int, "status": "M|A|D|R|?"}]
```
Deterministic heuristic grouping (directory/module affinity, path tags, test↔source pairing, import affinity where cheap). Labels are
*suggestions*; Claude renames/merges in the skill.

### memory.index (producer: memory agent; consumers: semantic.temporal, history, hooks)
```python
def open_db(cwd) -> sqlite3.Connection                      # creates .git/git-warp/warp.db, migrates schema
def ensure_indexed(cwd, max_commits: int|None=None) -> dict  # incremental by HEAD; returns {"indexed":n,"head":sha,...}
def cochange(cwd, path, limit=20) -> list[dict]; hotspots(cwd, limit=20) -> list[dict]; churn(cwd, path_prefix=None, limit=20) -> list[dict]
def file_commits(cwd, path, limit=50) -> list[dict]
```
Hooks/skills must work if the DB is absent/corrupt (degrade, never crash). Respect `cfg.memory_enabled`.

### memory.recorder
```python
def record(cwd, event: dict) -> bool    # appends redacted JSONL under state_dir; honors cfg.recorder_enabled; enforces retention
```
Record only: ts, session_id, hook event, tool name/category, redacted+truncated command (Bash) or file path (edits), branch, head, dirty-count.
Never record file contents, prompts, env.

### safety.classifier (producer: guardian)
```python
@dataclass class Verdict: decision:str  # allow|ask|deny
        rule:str; operation:str; reason:str; safer:list[str]; commands:list[str]
def classify_command(command: str, cfg: Config, branch: str|None) -> Verdict
```
Pure function (no I/O except what the caller injects). `hooks.git_guard.main()` wraps it with git branch lookup + hook JSON.

### analysis (X-Ray / PR) output shapes are documented by the owning agent in docs/ (see `docs/cli-reference.md`).

## 5. Persistence & security boundaries

* Runtime state only in `<common git dir>/git-warp/` (`warp.db`, `flight-recorder.jsonl`, `state.json`). Nothing in the work tree.
* Hook stdin, filenames, branch names, commit messages, git config, repo contents are untrusted: argv lists only, no `shell=True`/`eval`/`exec`,
  refs validated by `check_ref`, paths passed after `--`.
* Redaction (`core/redact.py`) runs before any persistence or echo of command text.
* Git Warp never runs repository-provided code (no test commands, hooks, scripts). Bisect emits a command for the user/Claude to run under normal permissions.
* Mutations: only `rescue preserve` (creates a new branch ref, never overwrites) and local state files. Everything else read-only.

## 6. Failure behaviour

* Hooks: malformed stdin → no-op output, exit 0, **except** the guard, which on internal error returns `ask` for any command that mentions `git` (fail-safe, not fail-open) and `allow` otherwise.
* Not a git repo / unborn / detached / shallow / no upstream: every CLI command returns a structured result with explicit `warnings`, never a traceback.
* Timeouts surface as `{"error": ..., "partial": ...}`; nothing is silently swallowed (errors are included in output `warnings`/`errors`).

## 7. Configuration

`.claude/git-warp.local.md` frontmatter (Claude Code plugin-settings convention); parsed by `core/config.py`. Keys: `protected_branches`,
`safety_mode` (standard|strict), `sensitive_paths`, `ignored_paths`, `test_paths`, `infra_paths`, `memory_enabled`, `recorder_enabled`,
`recorder_retention_days`. Works with no file.

## 8. Extension points

New CLI command = module with `main(argv)` + one row in `warp.py` COMMANDS + a skill. New ecosystem adapter for blast radius =
class in `semantic/adapters/` implementing `imports(path, text) -> list[str]` and `resolve(spec, from_path, files) -> str|None`.
