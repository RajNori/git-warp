# Status

Updated on branch `cleanup/claude-recovery` (frozen recovery checkpoint: `da94148`). Original build plan: BUILD_PLAN.md (work packages WP0–WP7).

| WP | Area | State | Tests |
|---|---|---|---|
| WP0 | core (`gitwarp/core`), dispatcher, hook entry points, fixtures | done | core unit tests |
| WP1 | Git Guardian (`gitwarp/safety`, `hooks/git_guard.py`) | done; hardened after security review | classifier / tokenizer / hook / hardening / security suites |
| WP2 | Forensics (`gitwarp/history`: rescue, archaeology, bisect) | done | `test_history_*` |
| WP3 | Semantic (`gitwarp/semantic`: commits, blast, conflict, temporal) | done | `test_semantic_*` |
| WP4 | Memory + hooks (`gitwarp/memory`, session_start/post_tool/stop) | done; context/redaction hardened | `test_memory_*`, `test_hook_*` |
| WP5 | Analysis (`gitwarp/analysis`: xray, pr) | done | `test_analysis_*` |
| WP6 | Adversarial QA | **not completed** — the QA agent's planned `test_adv_*` suite never existed (the run was interrupted by the incident described in the recovery records). Partial substitute: the independent security reviews and `tests/unit/test_guard_hardening.py`. | — |
| WP7 | Docs | rebuilt after recovery (README, CHANGELOG, docs/, examples/) | — |

Full suite at the last validation: see POST_RESTORE_VALIDATION.md. Plugin version is `0.1.0` (unreleased).
Known open items are listed in FINAL_REVIEW.md ("Disposition").
