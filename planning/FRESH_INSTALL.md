# Fresh-install acceptance (v0.1.0)

Goal: install Git Warp the way a user would, without the development checkout and without touching the user's normal Claude configuration.

**Method.** A clean `git archive` export of the commit under test was placed in a throwaway directory containing
`.claude-plugin/marketplace.json` (a local marketplace listing the plugin with `"source": "./git-warp"`). Everything below ran with an isolated
`CLAUDE_CONFIG_DIR` (so nothing was written to `~/.claude`), Claude Code 2.1.285 on macOS.

```
commit under test: 807f052057240838571141ab0984ff73982d9efd   (the plugin directory has not changed in a way that affects installation since; the later commits touch guard rules, skills text and docs, and every later full run loads a fresh export)
== validate marketplace
Validating marketplace manifest: /private/tmp/gw-live/fi/mkt/.claude-plugin/marketplace.json

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
  LSP servers (0)

Projected token cost
```

**Installed copy exercised from an unrelated directory** (cwd `/`, `CLAUDE_PLUGIN_ROOT` set to the installed cache path
`.../plugins/cache/gw-release-check/git-warp/0.1.0`):

- the guard hook answered `permissionDecision: "deny"` for `git reset --hard` in a throwaway repository;
- `warp.py xray --repo <repo>` returned the full evidence JSON;
- every command in the installed `hooks/hooks.json` is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook_*.py"`, so nothing depends on the developer's cwd.

**What this does not prove.** Installing and *running* the installed copy inside a live Claude session requires the user's authenticated
configuration, so the live runs (`planning/LIVE_ACCEPTANCE.md`) load the same export with `--plugin-dir` for the session instead of installing
it. The install flow and the live behaviour were therefore verified separately, on the same artifact layout.
