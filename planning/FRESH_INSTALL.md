# Fresh-install acceptance (v0.1.0)

Goal: install Git Warp the way a user would, without the development checkout and without touching the user's normal Claude configuration.

**Method.** A clean `git archive` export of the commit under test was placed in a throwaway directory containing
`.claude-plugin/marketplace.json` (a local marketplace listing the plugin with `"source": "./git-warp"`). Everything ran with an isolated
`CLAUDE_CONFIG_DIR` (nothing was written to `~/.claude`), Claude Code 2.1.285 on macOS. The commit below is the tip at the time of the run;
only documentation and planning files changed afterwards.

## Install flow

```
commit under test: c096098bf3c50815e167e9395d935365d6ae0bee
== validate marketplace (strict)

✔ Validation passed
== validate plugin (strict)

✔ Validation passed
== marketplace add
Adding marketplace…✔ Successfully added marketplace: gw-release-check (declared in user settings)
== install
Installing plugin "git-warp@gw-release-check"...✔ Successfully installed plugin: git-warp@gw-release-check (scope: user)
== list
Installed plugins:

  ❯ git-warp@gw-release-check
    Version: 0.1.0
    Scope: user
    Status: ✔ enabled
== details
git-warp 0.1.0
  Description: Git Warp (fresh-install acceptance)
  Source: git-warp@gw-release-check

Component inventory
  Skills (10)  git-archaeology, git-bisect-ai, git-blast-radius, git-commits, git-conflict, git-memory, git-pr, git-rescue, git-temporal-review, git-xray
  Agents (3)  git-history-analyst, git-risk-analyst, git-forensic-analyst
  Hooks (4)  SessionStart, PreToolUse, PostToolUse, Stop  (harness-only — no model context cost)
  MCP servers (0)
```

## Installed copy exercised from an unrelated directory

cwd `/`, `CLAUDE_PLUGIN_ROOT` set to the installed cache path, a throwaway repository as the hook's `cwd`:

```
== installed root: plugins/cache/gw-release-check/git-warp/0.1.0
guard from /: deny
xray from /: ok, branch main
  SessionStart python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook_session_start.py"
  PreToolUse python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook_git_guard.py"
  PostToolUse python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook_post_tool.py"
  Stop python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook_stop.py"
```

- the installed guard hook answers `deny` for `git reset --hard`;
- the installed CLI returns evidence JSON;
- every hook command is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook_*.py"`, so nothing depends on the developer's working directory.

## What this does not prove

Running the *installed* copy inside a live Claude session needs the user's authenticated configuration, which an isolated config directory does
not have. The live runs (`planning/LIVE_ACCEPTANCE.md`) therefore load the same export with `--plugin-dir` for the session instead of installing it:
the install flow and the live behaviour were verified separately, on the same artifact layout.
