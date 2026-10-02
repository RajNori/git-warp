# Marketplace listing draft and publishing checklist

**Git Warp is not published to any marketplace.** Nothing in this repository has been tagged, pushed or submitted.
This page holds draft listing text and an honest list of what remains. It describes only what is implemented.

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

## Checklist: what remains before publishing

Derived from the repository's current state. Unchecked means not done or not verified by the documentation author.

### Repository and release

- [ ] Decide the release version. `plugin.json` says `0.1.0`; no `1.0.0` is claimed anywhere. The CHANGELOG lists
      `0.1.0` as unreleased.
- [ ] Merge `cleanup/claude-recovery` (the recovery and cleanup work) to `main` and push. As of
      `planning/POST_RESTORE_VALIDATION.md` GitHub's `main` was still the scaffold commit `af40260`.
- [ ] Commit the new documentation (README, `docs/`, `examples/`, `CHANGELOG.md`); they are uncommitted work.
- [ ] Commit or remove the untracked `planning/CLEANUP_LOG.md` and `planning/POST_RESTORE_VALIDATION.md`.
- [ ] Decide whether `planning/` ships in the published repository (it holds working notes, including a stale
      `STATUS.md` that shows every work package as pending).
- [ ] Create a release tag only when ready (`claude plugin tag` is the documented helper; not run).
- [ ] Add a marketplace manifest (or get listed in an existing marketplace) and re-run `claude plugin validate`
      against it. Its required shape was not researched here.
- [ ] Optional manifest metadata: `claude plugin validate . --strict` passes today, but `plugin.json` has no
      `homepage` field and the author has no email/URL.

### Verification not yet done

- [ ] Re-run the test suite on the commit to be released (1070 passed at checkpoint `da94148`; not re-run during
      documentation work). There is no CI configuration in the repository.
- [ ] End-to-end check in a real interactive Claude Code session: skills trigger from natural language, the hooks
      fire, the guard's `deny`/`ask` decisions are honoured and displayed as expected. Only a headless listing of
      skill names was observed.
- [ ] Test on Linux and on other Python (3.8 to 3.12) and git versions. Only Python 3.13.2 / git 2.53.0 / macOS
      were used. Code needs at least: Python 3.8+ (walrus operator), SQLite 3.24+ (upsert), git 2.36+
      (`worktree list -z`), by reading the code. Windows is untested.
- [ ] Test on a large repository to learn real index and analysis timings (none are claimed here).

### Known issues to fix or document before release

- [ ] `rescue inspect` over-redacts the `stat` field (`Author:`/`AuthorDate:` values become `[REDACTED]`).
- [ ] The guard allows `git checkout -- <file>` and `git restore <file>` (single-file discards), and allowed
      `$GIT reset --hard` in a test run. Decide whether that is acceptable and say so in the listing.
- [ ] `planning/ARCHITECTURE.md` and `planning/STATUS.md` are out of date with the code (see
      [architecture.md](architecture.md)).
- [ ] Skill/code discrepancies found while writing docs are listed in the documentation hand-off report. The skills
      were also being edited (uncommitted `allowed-tools` changes) while these docs were written, so re-check them
      against the CLI before release. One item observed: `skills/git-commits` tells Claude it may run
      `git add`/`git commit` when the user asks, but its `allowed-tools` does not list them, so those calls would go
      through normal permission prompts.
- [ ] Verify that `${CLAUDE_PLUGIN_ROOT}` inside `SKILL.md` command lines is expanded by Claude Code in a live
      session (the hooks use it in `hooks/hooks.json`; the skills rely on it too and this was not observed).
- [ ] Security review of the hook scripts and redaction by someone other than the author.

### Listing content

- [ ] Screenshots or a recording of a real session (none exist; `examples/` holds captured CLI output only).
- [ ] Decide whether to ship the `examples/` directory in the plugin.
- [ ] Re-read this draft listing against the code at release time.
