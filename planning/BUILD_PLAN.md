# Build Plan

Architecture: see ARCHITECTURE.md. Principle: deterministic evidence in Python (tested), judgment in skills/agents.

## Phases
| Phase | Content | Gate |
|---|---|---|
| P0 foundation (lead) | core git layer, config, redact, paths, output, CLI dispatcher, hook entry points, test fixtures, plan docs | core tests green |
| P1 features (parallel agents) | WP1–WP5 below | each agent's unit+integration tests green; no cross-file edits |
| P2 hardening | WP6 adversarial QA reviews P1 and extends tests; WP7 docs; lead integration | full suite + security suite green |
| P3 review | fresh reviewer → planning/REVIEW.md; fix Critical/High | `claude plugin validate`, real plugin load, all findings resolved |

## Work packages (owner / files / deps)
| WP | Owner | Files | Depends on |
|---|---|---|---|
| WP0 | lead | `.claude-plugin/`, `hooks/hooks.json`, `scripts/warp.py`, `scripts/hook_*.py`, `scripts/gitwarp/core/**`, `tests/conftest.py`, `planning/` | — |
| WP1 Guardian | safety-engineer | `scripts/gitwarp/safety/**`, `scripts/gitwarp/hooks/git_guard.py`, `tests/security/test_guard*.py`, `tests/unit/test_guard*.py`, `tests/integration/test_hook_guard*.py` | WP0 |
| WP2 Forensics | forensics-engineer | `scripts/gitwarp/history/**`, `skills/git-{rescue,archaeology,bisect-ai}/**`, `agents/git-forensic-analyst.md`, `agents/git-history-analyst.md`, `tests/**/test_history*` | WP0 |
| WP3 Semantic | semantic-engineer | `scripts/gitwarp/semantic/**`, `skills/git-{commits,blast-radius,conflict,temporal-review}/**`, `agents/git-risk-analyst.md`, `tests/**/test_semantic*` | WP0, contract `memory.index` (temporal) |
| WP4 Memory | memory-engineer | `scripts/gitwarp/memory/**`, `scripts/gitwarp/hooks/{session_start,post_tool,stop}.py`, `skills/git-memory/**`, `tests/**/test_memory*`, `tests/**/test_hook_*` (non-guard) | WP0 |
| WP5 Analysis | analysis-engineer | `scripts/gitwarp/analysis/**`, `skills/git-{xray,pr}/**`, `tests/**/test_analysis*` | WP0, contract `semantic.cluster` |
| WP6 QA | qa-engineer | `tests/**` (new files only, prefix `test_adv_`), `tests/fixtures/**` | WP1–5 |
| WP7 Docs | docs-engineer | `README.md`, `docs/**`, `examples/**`, `CHANGELOG.md` | WP1–5 |

## Testing strategy
pytest, real temp repos via `RepoBuilder` (tests/conftest.py); hook scripts exercised end-to-end via subprocess with realistic hook JSON;
security suite (injection, wrappers, redaction); coverage measured with `coverage` if installable, else stdlib `trace`-based line report.

## Integration strategy
Lead merges: manifests, `warp.py` table, `hooks.json`, README. Agents report contract changes to lead instead of editing shared files.

## Acceptance criteria
See the Definition of Done in the mission; each item is tracked in STATUS.md with evidence.
