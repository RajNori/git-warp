# Security Model

## Threat inputs

Treat shell command text, hook stdin, repository paths, refs, branch names, filenames, commit messages, Git configuration, and repository files as hostile. Never evaluate or interpolate them as shell code.

## Controls

- Python subprocess calls use argv arrays, bounded timeouts, captured output, and explicit cwd.
- The Git guardian is deterministic, returns only deny/ask/no-op, and uses a bounded parser. Known parser limits are documented; ambiguous Git execution escalates.
- Protected branch checks inspect the command's target ref where it can be determined. If the target cannot be resolved safely, escalate.
- No hook automatically stages, commits, checks out, rebases, resets, cleans, prunes, pushes, or deletes refs.
- Hook JSON is size-limited and parsed with explicit malformed-input behavior. Output is valid JSON and event-specific.
- The flight recorder uses allowlisted fields, bounds record sizes, redacts credential-like strings, and never records full tool input, prompt text, source contents, environment variables, or secrets.
- SQLite uses parameterized statements and lives under the Git common directory. It indexes metadata and paths, not source contents.
- Git output is bounded before inclusion in user-facing analysis; claims cite refs, paths, or command evidence.

## Review targets

Shell operators, command wrappers, Git aliases/config, option ordering, combined short options, pathspec magic, unusual filenames, symlinked paths, malicious branch names, invalid JSON, secret-like values, common Git dirs/worktrees, detached/unborn repositories, and partial Git states.
