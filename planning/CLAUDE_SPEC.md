# Claude Code Plugin Specification Notes

Verified against the current official Claude Code documentation on 2026-10-02.

## Packaging and manifest

- A plugin is a directory of components. `.claude-plugin/plugin.json` is optional for loading from a standard layout, but Git Warp keeps it to declare identity and metadata.
- `name` is the only required manifest field and must be a kebab-case plugin identifier. Current documented fields include `displayName`, `version`, `description`, `author` (`name` required), `homepage`, `repository`, `license`, `keywords`, `defaultEnabled`, `dependencies`, `metadata`, `settings`, `userConfig`, and component declarations. Unknown top-level keys are stripped at load and reported by validation; strict nested config shapes reject unknown keys.
- Component directories such as `skills/`, `agents/`, and `hooks/` live at the plugin root, not inside `.claude-plugin/`. The manifest is needed to point at non-default component paths or define inline components.
- Manifest component paths are relative to plugin root, start with `./`, and must stay inside the root. Default component locations are discovered without explicit manifest entries.
- Plugins can be developed without a marketplace by loading the folder with `claude --plugin-dir <path>`.
- Marketplace metadata belongs in `.claude-plugin/marketplace.json` in the marketplace repository. A marketplace entry describes where the plugin is fetched; it is separate from `plugin.json`.

## Skills and agents

- Skills are directories containing `SKILL.md`, discovered under `skills/<skill-name>/SKILL.md`; installed plugin skills are namespaced when invoked.
- Skill frontmatter supports fields including `name`, `description`, `argument-hint`, `allowed-tools`, `model`, `context`, `agent`, `hooks`, `user-invocable`, `disable-model-invocation`, and `effort`. Exact accepted fields and types depend on current skill metadata; use documented fields only. A plugin skill is `skills/<skill-name>/SKILL.md` and appears namespaced as `/<plugin>:<skill>`.
- Agent definitions are Markdown files under `agents/`. Plugin agents support documented frontmatter such as `name`, `description`, `model`, `tools`, `disallowedTools`, `skills`, and `memory`. `permissionMode`, `hooks`, and `mcpServers` are ignored for plugin agents; do not copy project-agent assumptions into plugin definitions.

## Hooks

- Hook configuration is `hooks/hooks.json` at the plugin root; this file must wrap its event map in a top-level `hooks` object. Its shape matches the `hooks` map from Claude settings.
- Command hooks receive an event-specific JSON object on stdin. They must parse defensively and must not assume every event has the same fields.
- Hook command output is event-dependent. For standard decision events, JSON output is parsed from stdout; `PreToolUse` decisions use `hookSpecificOutput` with `hookEventName`, `permissionDecision`, and optional `permissionDecisionReason`. Current docs list `allow`, `deny`, `ask`, and `defer` for `PreToolUse`; omit a decision to leave the normal permission flow intact.
- Exit code 2 is the blocking-error signal for blockable events such as `PreToolUse`; other nonzero codes generally report non-blocking hook errors. Use structured `deny` output for policy decisions and reserve exit 2 for an internal/policy failure that should block.
- Hook event schemas differ. `SessionStart` and `Stop` accept context/output differently from `PreToolUse`; implement only the fields needed and verify against their event sections. `PermissionRequest` is separate and uses `hookSpecificOutput.decision.behavior` (`allow`/`deny`), so do not conflate it with `PreToolUse`.
- `SessionStart` plain-text stdout becomes context for Claude; `Stop` plain-text stdout is not a user-visible report. For a concise dirty-tree notice, `Stop` can emit the documented universal `systemMessage` field, which is shown to the user without continuing the conversation. A Stop `additionalContext` or block decision does continue the conversation and should not be emitted for routine reporting.
- `${CLAUDE_PLUGIN_ROOT}` is the documented mechanism for absolute references to files in the plugin installation. Quote the expanded path in shell commands.

## Loading, settings, validation, marketplaces

- For local development use `claude --plugin-dir <plugin-directory>`; a marketplace is not required.
- Plugin enablement and install scope are managed by Claude Code settings and the plugin commands. Git Warp must not write user settings automatically.
- Marketplace publishing requires a separate `.claude-plugin/marketplace.json` in a catalog repository, with marketplace-level `name`, `owner`, and `plugins`; each plugin entry requires `name` and `source`. Git Warp will document an example entry but will not publish one.
- Validate with `claude plugin validate <path>`; `--strict` treats warnings as errors. Local loading uses `claude --plugin-dir <plugin-root>` and a running session can reload changes with `/reload-plugins`.
- Claude Code's validator/runtime is the integration validator. If the `claude` CLI is unavailable, record local schema/static checks and mark live loading unverified.

## Sources

- Plugins overview: https://code.claude.com/docs/en/plugins
- Create a plugin: https://code.claude.com/docs/en/plugins/create
- Plugin components: https://code.claude.com/docs/en/plugins/components
- Manifest reference: https://code.claude.com/docs/en/plugins/manifest-reference
- Plugin CLI reference: https://code.claude.com/docs/en/plugins/cli-reference
- Hook reference: https://code.claude.com/docs/en/hooks
- Skill authoring: https://code.claude.com/docs/en/skills
- Subagents: https://code.claude.com/docs/en/sub-agents
- Marketplace authoring: https://code.claude.com/docs/en/plugin-marketplaces

These pages were opened directly from the official `code.claude.com` documentation and cross-checked with the plugin reference. Exact schema details remain subject to the manifest and event-specific sections; validate with the installed `claude` CLI before claiming live compatibility.
