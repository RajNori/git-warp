# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-04

First release. The implementation was recovered from session evidence (recovery checkpoint `da94148`), documented and
hardened afterwards, then independently compared against a second implementation with a neutral acceptance harness
(<https://github.com/RajNori/Disposable>); the hardening below closes the findings of that comparison.

### Added
- **Hooks** (`hooks/hooks.json`): `SessionStart` repository context and bounded incremental index; `PreToolUse`
  guard on `Bash`; `PostToolUse` flight recorder; `Stop` end-of-turn change report.
- **Git Guardian**: deterministic command-string classifier (DENY / ASK / DEFER) with a shell tokenizer, protected
  branches, `standard` and `strict` modes, fail-safe behaviour, and the `warp.py guard check` debug command.
  Documented blind spots in `docs/guard-limitations.md`.
- **Git X-Ray** (`git-xray`, `warp.py xray`): read-only state and risk report.
- **Git Rescue** (`git-rescue`, `warp.py rescue scan|inspect|preserve`): reflog, stash and fsck candidates with
  confidence labels; `preserve` creates a new `rescue/<date>-<sha8>` branch and never overwrites a ref.
- **Git Archaeology** (`git-archaeology`, `warp.py archaeology`): fact/inference/unknown timelines for files,
  symbols (`-S`), regexes (`-G`) and keyword questions.
- **AI Bisect** (`git-bisect-ai`, `warp.py bisect plan|status`): planning only; never starts a bisect or runs the
  test command.
- **PR Engineer** (`git-pr`, `warp.py pr`): facts for a PR description from `merge-base..HEAD`.
- **Semantic Commit Composer** (`git-commits`, `warp.py commits`): change clustering and ordered commit proposals;
  stages and commits nothing.
- **Blast Radius** (`git-blast-radius`, `warp.py blast`): import-graph impact with a rule-based level; Python, JS/TS
  and generic adapters.
- **Conflict Surgeon** (`git-conflict`, `warp.py conflict`): ancestry, regions and hints for merge, rebase,
  cherry-pick and revert conflicts.
- **Temporal Code Review** (`git-temporal-review`, `warp.py temporal`): cited historical evidence for the current
  change.
- **Flight Recorder**: redacted JSONL under `.git/git-warp/` with retention and size cap; read back with
  `warp.py memory sessions`.
- **Repository Memory** (`git-memory`, `warp.py memory index|status|cochange|hotspots|churn|introduced|reverts|authors|sessions|forget`):
  local SQLite history index.
- **Agents**: `git-forensic-analyst`, `git-history-analyst`, `git-risk-analyst` (tools: `Read, Grep, Glob`; no shell).
- **Configuration**: optional `.claude/git-warp.local.md` with `protected_branches`, `safety_mode`,
  `sensitive_paths`, `ignored_paths`, `test_paths`, `infra_paths`, `memory_enabled`, `recorder_enabled`,
  `recorder_retention_days`.
- Python standard library only; MIT license.

### Security and hardening
- **One Git process boundary** (`core/git.py`): argv only, bounded stdout/stderr, process-group kill, `GIT_*` handled by
  allowlist, command-level suppression of `core.fsmonitor`, `core.hooksPath`, pagers, `diff.external`, textconv and
  repository-scoped filters, an allowlist of subcommands, typed errors. What is *not* suppressed is documented in
  `docs/git-boundary.md`.
- **One revision-normalisation path** (`core/revisions.py`): option-shaped input is rejected, revisions are resolved with
  `--end-of-options` to full object ids, ranges are normalised endpoint by endpoint.
- **One secure-storage layer** (`core/storage.py`): `.git/git-warp` is `0700` and its files `0600`; symlinks, FIFOs, sockets,
  devices and unexpected directories are refused (never followed, replaced or deleted); `state.json` is written atomically
  under a lock; recorder appends are single locked writes; corrupt databases are quarantined (three generations), never
  deleted; first-time database creation is serialised across processes.
- **Privacy:** commit text, author names, paths, refs and session ids are redacted before they are stored; all CLI and hook
  output is redacted centrally; new patterns for SSH identity files, `--password=` flags, `curl -H` headers, npm and
  Hugging Face tokens, AWS secret keys. Database schema v2 empties derived v1 data so old raw commit text does not survive an upgrade.
- **Guardian** returns DENY / ASK / DEFER from one decision API. A dynamic expression that could conceal a destructive
  operation is never silently deferred (`git reset $(echo --hard)`, `cmd="git reset --hard"; $cmd`, `eval`, generated
  scripts, process substitution); forced fetch into local branches, unknown (possibly alias) subcommands and malformed hook
  payloads are handled; `git clean -n`/`-nfd` dry runs are not treated as destructive.
- **Trusted policy:** built-in floor, then user policy (`~/.claude/git-warp.local.md`), then repository policy; a lower-trust
  source can only tighten (protected branches are a union, the strictest `safety_mode` wins, nothing can disable the guard).
- **Agents** carry `Read, Grep, Glob` only: Claude Code cannot scope Bash in agent frontmatter, so Bash was removed instead of
  being described as read-only in prose. Skills keep scoped read-only `allowed-tools`.
- **Hooks:** bounded stdin, per-hook wall-clock budgets inside their `hooks.json` timeouts, PostToolUse prints exactly `{}`,
  the Stop hook cannot loop, SessionStart context is bounded and presents repository text as data.
- `commits` output is bounded on large change sets and `temporal` caps the untracked files it probes.

### Tests
- Unit, integration, security, acceptance (repository-state matrix, recovery fixtures, Guardian corpus, hostile
  repository and hostile environment fixtures), crash/concurrency and scale suites; an opt-in live Claude Code runner
  (`tests/live/live_acceptance.py`). Result at release: 3109 passed (baseline 1345; audit in `planning/BASELINE_TEST_AUDIT.md`).

### Known limitations
- The guard is a safety net, not a sandbox; it does not emulate a shell and only sees Bash command strings
  (`docs/guard-limitations.md`). Skills pre-approve scoped read-only prefixes; a prefix cannot exclude flags such as `--output`.
- Hook timeouts fail open if the host kills a hook.
- Git Warp's own Git calls do not suppress `include.path`, user-level filters (for example git-lfs) or attribute drivers other than
  textconv/external diff/filter (`docs/git-boundary.md`).
- Validated on macOS only; Linux is not yet validated and Windows is unsupported for this release (`docs/platforms.md`).
- `rm -rf foo.git` is denied (any path ending in `.git`): a known false positive.
- Not listed in a public marketplace.
