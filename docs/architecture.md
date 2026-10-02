# Architecture (short)

The authoritative design documents are [../planning/ARCHITECTURE.md](../planning/ARCHITECTURE.md) and
[../planning/DECISIONS.md](../planning/DECISIONS.md). This page is a summary written against the code as it
exists; where planning text and code differ the code was taken as the truth.

## Layers

```
Claude Code --hooks--> scripts/hook_*.py --> gitwarp/hooks/*        deterministic, fail-safe
     |
     +--skills--> SKILL.md tells Claude to run
                  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/warp.py <cmd> ...
                    --> gitwarp/{analysis,history,semantic,memory,safety}/cli.py   evidence as JSON
                  Claude interprets the JSON
All Git access --> gitwarp/core/git.py
```

| Package | Responsibility |
|---|---|
| `gitwarp.core.git` | The only module that spawns `git`: argument lists, timeouts, structured parsing, typed errors (`GitNotFound`, `GitTimeout`, `GitError`), `GIT_OPTIONAL_LOCKS=0` |
| `gitwarp.core.{config,redact,paths,output}` | Config parsing, secret redaction, path tagging, JSON and hook-protocol output |
| `gitwarp.hooks` + `scripts/hook_*.py` | stdin hook JSON in, decision or context out; must not crash Claude Code |
| `gitwarp.safety` | Shell tokenizer and Git command classifier (deny/ask/allow) |
| `gitwarp.analysis` | `xray`, `pr` (plus secret and diff scanning, risk rules) |
| `gitwarp.history` | `rescue`, `archaeology`, `bisect` |
| `gitwarp.semantic` | `commits`, `blast`, `conflict`, `temporal`; clustering; ecosystem adapters (`python`, `js`, `generic`) |
| `gitwarp.memory` | SQLite index (`warp.db`), flight recorder, state file, snapshot helpers for the Stop hook |
| `skills/*/SKILL.md` | Prompts that run the CLI and define the answer format |
| `agents/*.md` | Three read-only analyst agents |

`scripts/warp.py` is a dispatch table mapping each command to a module exposing `main(argv)`. `scripts/hook_*.py`
are thin entry points that add `scripts/` to `sys.path` and call `gitwarp.hooks.*.main`.

## Deterministic versus model judgment

Deterministic and tested: classifying destructive commands, protected-branch checks, status/ref/reflog parsing,
candidate discovery, import-graph extraction, co-change counts, redaction, indexing, risk rules. Model judgment:
naming and explaining clusters, interpreting archaeology, deciding whether conflict sides are compatible, PR prose,
temporal-review judgment, recovery recommendations. By design no model decision is the sole guard for a
destructive operation (hooks are `type: command`, ADR-11).

## Key decisions (from planning/DECISIONS.md)

Python standard library only (ADR-1); skills call a CLI that returns JSON (ADR-2); a single Git execution layer
(ADR-3); config in `.claude/git-warp.local.md` (ADR-4); the guard is a command-string classifier with honestly
documented limits (ADR-5) and fails safe (ADR-6); state under the common git dir (ADR-7); rescue mutates only by
creating a new branch (ADR-8); bisect never auto-runs (ADR-9); "authorship" not "ownership" (ADR-10).

## Hook failure behaviour

- Malformed or empty stdin: no-op (no output, no side effects) for SessionStart/Stop; `{}` for the others.
- Guard error: `ask` if the command mentions git, otherwise allow.
- Stop hook never blocks and exits immediately if `stop_hook_active` is set.
- Memory degrades gracefully: if `.git/git-warp/` is not writable or `warp.db` is unreadable, the index is rebuilt
  (the old file is kept once as `warp.db.corrupt`) or kept in memory for that run with a warning.

## Extension points

A new CLI command is a module with `main(argv)`, one row in `warp.py`'s `COMMANDS` table, and a skill. A new
ecosystem for blast radius is a class in `semantic/adapters/` with `imports(path, text)` and
`resolve(spec, from_path, files)`.

## Tests that protect the architecture

`tests/unit/test_architecture_invariants.py` contains four tests: `subprocess` is used only in `core/git.py`;
no dangerous calls (`eval`, `exec`, `system`, `popen`, `shell=True`) in production code; every script referenced in
`hooks/hooks.json` exists; and the obsolete scaffold scripts are gone. The cleanup log in
[../planning/CLEANUP_LOG.md](../planning/CLEANUP_LOG.md) records the removal of the old scaffold scripts that
violated the single-Git-layer rule.

## Known drift in planning documents

`planning/ARCHITECTURE.md` lists `memory` subcommands without `forget` and `rescue` flags without `--dry-run`;
the code and the skills have both. `planning/STATUS.md` still shows every work package as pending. This
documentation follows the code.
