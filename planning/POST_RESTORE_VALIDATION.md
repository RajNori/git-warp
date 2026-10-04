# Post-restore validation

Run from `/Users/rajnori/Desktop/git-warp` (fresh clone of the recovery checkpoint `da94148fd080637e75250af4e485b6e1ac2a742f`, `origin` re-pointed to GitHub, no fetch/reset/merge performed). Date: 2026-10-02.

| Check | Command | Result |
|---|---|---|
| HEAD | `git rev-parse HEAD` | `da94148fd080637e75250af4e485b6e1ac2a742f` |
| Tree | `git status` | clean |
| Tests | `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q` | **1070 passed, 1 warning in 49.62s** |
| Plugin | `claude plugin validate .` | Validation passed |
| Python syntax | `ast.parse` over `**/*.py` | 90 files parse |
| JSON | `json.load` on `.claude-plugin/plugin.json`, `hooks/hooks.json` | ok |
| Debris | `git status --short`, `find . -name __pycache__` | none |

The one warning is a `SyntaxWarning: invalid escape sequence '\;'` in a string literal in `tests/unit/test_guard_branches.py:18` (original agent code, unchanged).

Note: the local remote-tracking ref `origin/main` inherited from the clone points at `da94148`; GitHub's real `main` is still `af40260`. Do not trust `git status` "up to date with origin/main" until a deliberate `git fetch`.

## Final validation after cleanup (branch `cleanup/claude-recovery`)

| Check | Command | Result |
|---|---|---|
| Tests (baseline) | restored checkout, `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q` | 1070 passed |
| Tests (post-cleanup) | same command | **1345 passed, 1 warning in 52.85s** (+275 new tests; 0 test lines deleted versus `da94148`) |
| Plugin | `claude plugin validate .` | Validation passed |
| Python | `ast.parse` over every `**/*.py` | 89 files parse (90 minus the 5 removed scaffold scripts plus new test files) |
| JSON | `json.load` on plugin.json, hooks.json | ok |
| Hooks | each `scripts/hook_*.py` fed realistic hook JSON in a throwaway repo | SessionStart context ok; guard `deny` for `git reset --hard HEAD~1`, `{}` for `git status`; PostToolUse `{}` and state only under `.git/git-warp`; Stop systemMessage; malformed stdin → `{}`, exit 0 |
| Tracked debris | `git ls-files \| grep -E "DS_Store\|__pycache__\|\.pyc\|\.bak"` | none |

Untested: live interactive Claude Code session (skill invocation, `${CLAUDE_PLUGIN_ROOT}` expansion inside `allowed-tools`). See FINAL_REVIEW.md "Disposition".
