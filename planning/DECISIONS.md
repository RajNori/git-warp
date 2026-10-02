# Decisions

- **ADR-1 Python stdlib only.** No PyYAML/GitPython; SQLite via `sqlite3`. Keeps install zero-dependency (macOS/Linux).
- **ADR-2 Skills call a CLI, CLI returns JSON.** Evidence gathering is deterministic and testable; Claude does interpretation. Skills never embed long shell pipelines.
- **ADR-3 Single Git execution layer.** `core/git.py` is the only `subprocess` user; argv lists; `GIT_OPTIONAL_LOCKS=0` so read-only analysis never touches the index.
- **ADR-4 Config via `.claude/git-warp.local.md`.** Matches the plugin-settings guidance; tiny frontmatter parser; invalid values fall back and are reported.
- **ADR-5 Guard is a command-string classifier, honestly scoped.** It tokenizes shell (quotes, `;`, `&&`, `||`, pipes, subshells, `env`/`command`/`sudo`/absolute-path wrappers, `git -C/-c`) and classifies Git invocations. It cannot see through `eval`, variable expansion, scripts, or aliases defined outside the string; those are documented limitations and unresolved `$VAR` git subcommands escalate to `ask`.
- **ADR-6 Guard fails safe.** Internal error → `ask` if the command mentions git; never a silent allow of a recognised-destructive form.
- **ADR-7 State under the common git dir.** `<common-dir>/git-warp/` — shared across worktrees, never in the work tree, not committed.
- **ADR-8 Rescue only mutates by creating a new branch.** `rescue preserve` refuses to overwrite refs; everything else is read-only.
- **ADR-9 Bisect never auto-runs.** Git Warp plans and validates; it does not execute repository test commands.
- **ADR-10 "Authorship" not "ownership".** Memory reports contribution history only.
- **ADR-11 Hooks use `type: command` only.** Safety decisions must be deterministic; prompt-type hooks are not used for guarding.
