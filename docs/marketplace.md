# Marketplace listing draft and publishing checklist

**Git Warp is not listed in any marketplace.** Install it from this repository (see the README).
This page holds draft listing text and an honest list of what remains for a public listing. It describes only what is implemented.

## What `claude` says about marketplaces

From `claude plugin install --help` and `claude plugin marketplace --help` (Claude Code 2.1.285):

- `claude plugin install <plugin>` installs "from available marketplaces"; use `plugin@marketplace` to pick a
  marketplace. Scope option: `--scope user|project|local` (default `user`).
- `claude plugin marketplace add <source>` adds a marketplace from "a URL, path, or GitHub repo" (also `--scope`,
  `--sparse`); other subcommands: `list`, `remove`, `update`.
- `claude plugin validate <path>` validates a plugin or marketplace manifest (`--strict` treats warnings as errors).
- `claude plugin tag [path]` creates a `{name}--v{version}` Git tag, "validating that plugin.json and any enclosing
  marketplace entry agree".

Neither the repository nor these commands were used to publish anything. This repository has no
`.claude-plugin/marketplace.json`.

## Draft listing text

**Name:** `git-warp`

**One line:** Git Warp gives Claude Code a memory of a repository's past and a safety system for its future.

**Description (short, from `plugin.json`):** Git intelligence for Claude Code: a deterministic safety guard against
destructive Git commands, plus history archaeology, recovery, blast-radius, semantic commit planning, conflict
analysis, PR review and a local repository memory.

**Description (long):**

Git Warp is a Claude Code plugin with two halves. A deterministic `PreToolUse` hook inspects every Bash command and
blocks or asks about destructive Git operations such as `git reset --hard`, `git clean -f` and force pushes to
protected branches; the decision is made by tested code, not by a model. A small Python standard-library CLI gathers
Git evidence as JSON (repository state and risk, lost-commit candidates, file history timelines, import-graph
blast radius, merge-conflict ancestry, bisect plans, PR facts, semantic commit groupings), and ten skills tell
Claude how to run it and how to present the result. A local SQLite index and a redacted flight recorder, stored
only under `.git/git-warp/`, provide repository memory (co-change, hotspots, churn, reverts, contribution history,
earlier sessions). Everything is read-only except two documented operations: `rescue preserve` creates one new
branch, and `memory forget --yes` deletes Git Warp's own local files. The guard only sees Bash command strings and
is a safety net, not a sandbox.

**Keywords (from `plugin.json`):** git, safety, forensics, recovery, code-review, pull-requests, bisect,
developer-tools

**Author:** Raj Nori. **License:** MIT. **Repository:** https://github.com/RajNori/git-warp (as declared in
`plugin.json`). **Version in manifest:** `0.1.0`.

**Components:** 4 hooks (SessionStart, PreToolUse on Bash, PostToolUse, Stop), 10 skills (`git-xray`, `git-pr`,
`git-rescue`, `git-archaeology`, `git-bisect-ai`, `git-commits`, `git-blast-radius`, `git-conflict`,
`git-temporal-review`, `git-memory`), 3 agents (`git-forensic-analyst`, `git-history-analyst`, `git-risk-analyst`).

## Status and what remains for a public listing

Done for v0.1.0 (evidence in `planning/`):

- [x] Version `0.1.0` in `.claude-plugin/plugin.json`, `CHANGELOG.md` and the README; `claude plugin validate . --strict` passes
      for the plugin, `skills/` and `agents/`.
- [x] Full test suite, independent neutral acceptance corpus (`planning/DISPOSABLE_CONVERGENCE_RESULTS.md`) and an independent
      code review (`planning/CONVERGENCE_REVIEW.md`).
- [x] Fresh install as a user would do it, in an isolated Claude configuration: a local marketplace listing the plugin,
      `claude plugin marketplace add`, `claude plugin install`, `claude plugin details` (10 skills, 3 agents, 4 hooks), and the
      installed hook and CLI run from an unrelated directory (`planning/FRESH_INSTALL.md`).
- [x] Live Claude Code acceptance: skill discovery, SessionStart/PreToolUse/PostToolUse/Stop delivery, DENY / ASK / DEFER, ASK
      against a pre-approved Bash rule and against a skill-level pre-approval (the real interactive TUI), and every workflow skill
      (`planning/LIVE_ACCEPTANCE.md`).

Still to do before a **public marketplace** listing:

- [ ] Add a marketplace manifest in this repository (or get listed in an existing marketplace). This repository ships no
      `marketplace.json`; the install flow above was verified with a separate local marketplace directory.
- [ ] Linux validation and other Python/Git versions (`platforms.md`); Windows is unsupported for v0.1.0.
- [ ] CI configuration (none exists in the repository).
- [ ] Optional manifest metadata (`homepage`, author contact), screenshots or a recording of a real session.
- [ ] Decide whether `planning/` and `examples/` ship in the published artifact.
