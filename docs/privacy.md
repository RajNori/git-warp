# Privacy and local data

Everything Git Warp stores stays on your machine inside the repository's Git directory. This page lists what is
stored, what is redacted, how long it is kept, and how to turn it off or delete it. Verified against
`memory/recorder.py`, `core/redact.py`, `memory/index.py`, the hooks, and real runs.

## Where

`<repo>/.git/git-warp/` (precisely `<common git dir>/git-warp/`, so linked worktrees share it). It is inside
`.git`, never in the working tree, and is not committed.

| File | Written by | Content |
|---|---|---|
| `flight-recorder.jsonl` | `PostToolUse` and `SessionStart` hooks | Redacted metadata about tool calls, one JSON object per line |
| `flight-recorder.jsonl.1` | rotation | The one previous generation, created when the file would exceed 5 MiB |
| `warp.db` | `memory` commands, `temporal`, `SessionStart` auto-index | SQLite index of Git history plus session rows |
| `state.json` | recorder and hooks | Bookkeeping: last compaction time, last session id, last Stop-report hash, auto-index back-off timestamp, schema number |

Observed permissions in a real run: `flight-recorder.jsonl` and `state.json` were `-rw-------` (owner only);
`warp.db` was `-rw-r--r--` (the default for new SQLite files under your umask).

The code contains no network calls (a search for socket/HTTP/urllib imports found only `urllib.parse.quote`).
Git Warp does not upload anything. Note that, as with any Claude Code plugin, text that a hook injects into the
conversation (see below) and JSON that Claude reads from the CLI is processed by Claude like other tool output.

## Flight recorder: exactly what a record contains

Real records from a run (a throwaway repository; the Bash call had embedded secrets, the Edit call had edit
strings):

```json
{"ts":"2026-10-02T08:05:49.365Z","session_id":"sess-1","hook_event":"SessionStart","source":"startup","branch":"feature/billing","head":"afef50ed2294"}
{"ts":"2026-10-02T08:05:49.623Z","session_id":"sess-1","hook_event":"PostToolUse","tool":{"name":"Edit","category":"edit"},"files":["src/app.py"],"branch":"feature/billing","head":"afef50ed2294"}
{"ts":"2026-10-02T08:05:49.752Z","session_id":"sess-1","hook_event":"PostToolUse","tool":{"name":"Bash","category":"git"},"command":"git push https://[REDACTED]@example.com/r.git main && export API_TOKEN=[REDACTED]","branch":"feature/billing","head":"afef50ed2294"}
```

Fields the code can write: `ts`, `session_id`, `hook_event`, `tool.name`, `tool.category` (`edit`, `git`, `shell`,
`other`), `files` (edit tools only: one repo-relative path, or `<outside-repo>` for paths outside the repository),
`command` (Bash only), `source` (`startup`, `resume`, `clear`, `compact` for SessionStart), `branch`, `head` (12
characters), and optionally `dirty_count` and `test_outcome` (`pass`/`fail`/`error`/`skipped`) if the event
supplies them. The shipped hooks do not supply the last two.

**Never recorded:** file contents, the `old_string`/`new_string` of edits (the Edit above had
`SECRET_BODY_OLD/NEW` in its input; neither appears), prompts, tool responses/output, environment variables.

Commands are redacted, newlines become ` ⏎ `, and they are cut to 300 characters followed by `…`.

## Redaction

`core/redact.py` replaces probable secrets with `[REDACTED]` before anything is persisted or echoed from command
text. Patterns: private-key blocks; `Authorization:`/`Proxy-Authorization:` values; `Bearer`/`Basic` tokens;
`sk-`/`pk-`/`rk-` style keys; GitHub tokens (`ghp_` etc., `github_pat_`); GitLab `glpat-`; Slack `xox*-`; AWS
`AKIA`/`ASIA` ids; Google `AIza` keys; JWTs; credentials in URLs (`scheme://user:pass@`); any `NAME=value` or
`name: value` where the name contains `secret`, `token`, `password`/`passwd`/`pwd`, `api_key`, `access_key`,
`private_key`, `credential` or `auth`; and flags such as `--password`, `--token`, `--secret`, `--api-key`, `--auth`.
JSON keys with secret-like names have their string values replaced.

Redaction input is capped at 8000 characters per string (anything beyond is replaced by a truncation marker, never
passed through unredacted; added in commit `351ef9f` to keep hooks inside their timeouts).

Redaction is pattern-based. It will miss secrets in unusual formats and can over-redact. (An earlier over-redaction of
`Author:`/`AuthorDate:` lines in `rescue inspect` output was fixed in commit `64e23a0`; those fields are readable now.)
Do not rely on redaction as your only protection when you
type secrets into commands.

## The history index (`warp.db`)

Built from `git log` of your repository: commit hashes, parents, **author names and emails**, author/commit dates,
subjects, merge/revert flags, per-commit file paths with added/deleted line counts, co-change counts between
files, author aggregates, ref names and hashes, session rows (`session_id`, start time, branch, head) and a small
`events` table (a `session_start` row each time the SessionStart hook registers a session). No file contents and no commit bodies are stored as columns
(bodies are parsed to detect `This reverts commit <sha>`). `memory authors` prints the stored emails.

## Context injected into the conversation

- `SessionStart` adds: branch, short HEAD, upstream and ahead/behind, working-tree counts, stash and worktree
  counts, last five commit short hashes and subjects (cleaned and truncated), a shallow-clone warning if relevant,
  and a fixed safety-policy reminder. Commit subjects are repository data and are labelled as data, not
  instructions, in that text.
- `Stop` prints a short report: branch, change counts, risk level and drivers, a diffstat, a next step. It is
  shown only when the working tree changed and the report differs from the previous one.

## Retention

- Recorder: records older than `recorder_retention_days` (default 30) are dropped by a compaction that runs at
  most once per 24 hours when a record is written; unparseable lines are dropped too. The `.1` rotation file is
  removed once its modification time is older than the window. Size cap: 5 MiB per file, one previous generation.
- `warp.db` has no expiry. It is rebuilt incrementally and keeps what it indexed.

## Turning it off

In `.claude/git-warp.local.md`:

```
recorder_enabled: false     # no flight recorder
memory_enabled: false       # no automatic indexing; memory queries refuse to run
```

`recorder_retention_days: 0` also stops recording. Disabling does not delete existing files.

## Deleting it

```bash
python3 scripts/warp.py memory forget          # shows what it would delete and refuses without --yes
python3 scripts/warp.py memory forget --yes    # deletes warp.db(+sidecars) and flight-recorder.jsonl(+.1)
```

`forget` keeps `state.json`. You can also delete `.git/git-warp/` by hand. Cloning or copying the repository
elsewhere copies `.git/git-warp/` too unless you exclude it (for example a fresh `git clone` does not include it,
since it is not part of Git's transferred objects).

## Honest limits

- Local files are not encrypted.
- The recorder logs the redacted text of every Bash command Claude runs, not only Git commands.
- Anyone with read access to your `.git` directory can read these files.
