# Git Warp

Git intelligence for Claude Code: a deterministic safety guard against destructive Git commands, plus history
archaeology, recovery, blast-radius, semantic commit planning, conflict analysis, PR review and a local repository memory.

**The plugin lives in [`plugin/`](plugin/). Full documentation: [plugin/README.md](plugin/README.md).**

```bash
claude --plugin-dir /path/to/git-warp/plugin
```

## Repository layout

| Path | What it is | Ships with the plugin |
|---|---|---|
| `plugin/` | The plugin: manifest, skills, agents, hooks, scripts, README, LICENSE | Yes |
| `docs/`, `examples/` | Reference docs and captured example runs | No |
| `tests/` | Test suite (`pytest` from the repository root) | No |
| `planning/` | Design notes, reviews and verification records | No |
| `CHANGELOG.md` | Release history | No |
| `assets/` | Icon source | No |

Only `plugin/` is installed or submitted to the plugin directory.

## Development

```bash
pip install pytest pytest-asyncio
python3 -m pytest -q
```

## License

MIT. See [LICENSE](LICENSE).
