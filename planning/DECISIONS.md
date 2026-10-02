# Architecture Decisions

## D1 — Deterministic safety, agentic interpretation

Safety decisions and Git execution are code-owned. Model instructions can explain and recommend, but cannot be the only guard against destructive commands.

## D2 — argv-only Git subprocess interface

All normal Git calls pass an argument array to `subprocess.run`; `shell=True` is prohibited. Results retain code, output, error, cwd, and timing; timeouts are distinct failures.

## D3 — Conservative bounded shell parsing

No claim of complete Bash parsing. Tokenize common quoting/escaping and split common command-list operators; identify common wrappers (`env`, `command`, absolute Git path). If a segment may invoke Git but parsing is ambiguous, ask rather than allow. Clear destructive command forms deny.

## D4 — Preserve normal Claude permissions

The guardian emits no hook decision for safe/unclassified commands. It never returns `allow`; Claude Code's existing permission rules remain authoritative.

## D5 — Runtime data is opt-in by value, minimal by default

Record only explicit metadata, with path redaction and secret-pattern filtering. SQLite indexes commit metadata/path relationships, not file contents or prompts. Runtime data is local under the Git common directory.

## D6 — Heuristics are explained, not scored cosmetically

Use named evidence/risk signals rather than unsupported numeric confidence percentages. Each inferred relationship states the heuristic that produced it.

## D7 — No automatic recovery mutation

Recovery and bisect flows inspect evidence and propose exact commands. They do not reset, checkout over changes, create refs, revert, or commit without an explicit human action.
