# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). No release has been published or tagged; the version in
`.claude-plugin/plugin.json` is `0.1.0`.

## Project history

The implementation in this repository was **recovered from session evidence** and committed as the recovery
checkpoint `da94148` ("recovery: reconstruct Git Warp implementation from Claude session evidence"). At that
checkpoint the test suite reported 1070 passed (see `planning/POST_RESTORE_VALIDATION.md`). The documentation
(README, `docs/`, `examples/`, this changelog) was **written afterwards, as new work**, from the code and from real
runs; it is not part of the recovered material. Everything under "Unreleased" below happened after the checkpoint.

## [Unreleased]

### Changed
- Git execution consolidated into `scripts/gitwarp/core/git.py` (commit `1d684c2`). It is now the only module that
  spawns `git`.
- Documentation rebuilt from scratch: the previous `README.md` was a stale scaffold (it described a roadmap and
  a marketplace snippet rather than the implementation). New `README.md`, `docs/` (getting started, safety model,
  commands, CLI reference, configuration, architecture, troubleshooting, privacy, marketplace draft) and
  `examples/` (real captured outputs from a throwaway repository, trimmed).

### Removed
- Obsolete scaffold scripts `scripts/_common.py`, `scripts/git_guard.py`, `scripts/session_context.py`,
  `scripts/change_tracker.py`, `scripts/stop_report.py` (commit `1d684c2`). They are replaced by
  `scripts/hook_*.py` entry points and the `gitwarp` package. Details and evidence:
  `planning/CLEANUP_LOG.md`.
- Tracked `.DS_Store` (commit `67a6b68`); `.gitignore` already lists it.

### Added
- Architecture-invariant tests (commit `2497803`, `tests/unit/test_architecture_invariants.py`): `subprocess` only
  in `core/git.py`, no dangerous calls in production code, every script named in `hooks/hooks.json` exists, and the
  obsolete scaffold scripts stay gone.

### Fixed
- Secret redaction no longer has a quadratic worst case: the identifier prefix is bounded and redaction input is
  capped at 8000 characters, so a very long command cannot push the guard hook past its timeout (commit `351ef9f`,
  with regression tests).

### Known issues
- `rescue inspect` over-redacts the word after `Author:`/`AuthorDate:` in its `stat` string (observed while
  writing the docs; not fixed).
- The guard allows single-file discards (`git checkout -- <file>`, `git restore <file>`). See
  `docs/safety-model.md` and `docs/guard-limitations.md`.

## [0.1.0] - Unreleased

First version, as declared in `.claude-plugin/plugin.json`. Not published or tagged. Contents as implemented in the
recovered code:

### Added
- **Hooks** (`hooks/hooks.json`): `SessionStart` repository context and bounded incremental index; `PreToolUse`
  guard on `Bash`; `PostToolUse` flight recorder; `Stop` end-of-turn change report.
- **Git Guardian**: deterministic command-string classifier (deny / ask / allow) with a shell tokenizer, protected
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
- **Agents**: `git-forensic-analyst`, `git-history-analyst`, `git-risk-analyst` (read-only by prompt).
- **Configuration**: optional `.claude/git-warp.local.md` with `protected_branches`, `safety_mode`,
  `sensitive_paths`, `ignored_paths`, `test_paths`, `infra_paths`, `memory_enabled`, `recorder_enabled`,
  `recorder_retention_days`.
- Python standard library only; MIT license.

### Not done
- No marketplace listing, release tag, CI configuration, or live-session end-to-end verification (see
  `docs/marketplace.md`).
