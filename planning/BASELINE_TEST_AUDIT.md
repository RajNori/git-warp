# Baseline test audit (Phoenix `d909d6d` → convergence)

Requirement: no baseline test may silently disappear, and none may be skipped, xfailed or loosened to obtain green.

Method: `pytest --collect-only` of `d909d6d` (1345 test ids) compared with the unit/integration/security ids of the
convergence tip (re-run on the final tip). 1234 baseline ids are unchanged. The other 111 are accounted for:

| Count | Baseline ids | What happened | Why it is not a loosening |
|---|---|---|---|
| 89 | `tests/unit/test_guard_*::...-allow]` and similar parametrised ids | Renamed `-defer]`: the Guardian's "no objection" outcome is now called DEFER | Mechanical rename; the same inputs and the same expected behaviour are asserted under the new id |
| 17 | `tests/integration/test_hook_guard.py::test_malformed_input_is_a_noop[...]` | Replaced by `test_nothing_to_classify_is_defer`, `test_uninterpretable_payload_is_ask_never_silent` and oversized / invalid-UTF-8 tests | The old test asserted that empty, malformed or incomplete PreToolUse payloads produce no output (fail open). That behaviour was an independently demonstrated defect (Disposable GW-HOOK-018/020/021/024/025): a payload the guard cannot interpret must ASK. The replacement is stricter, covers every old input, and is complemented by `tests/security/test_convergence_contracts.py::test_guard_fails_safe_on_malformed_payloads` |
| 2 | `tests/security/test_guard_malicious.py::test_unicode_lookalikes_do_not_crash_and_do_not_false_positive[git rеset --hard]` and `[git re​set --hard]` | Moved to `test_unicode_lookalike_subcommands_ask_as_possible_aliases` | A first word that is not a real Git subcommand could be a configured alias, so it now ASKs (rule `unknown-subcommand`); the inputs are still tested, with a stricter expectation |
| 1 | `tests/unit/test_guard_branches.py::test_cases[git reset --soft $X-allow]` | Expectation changed to `ask` | In real Git the last mode flag wins (`git reset --soft --hard` is a hard reset), so a dynamic word after `--soft` is not provably harmless; the old DEFER encoded unsafe behaviour |
| 1 | `tests/unit/test_guard_hardening.py::test_dynamic_false_positives_stay_allowed[git push origin "$BRANCH"]` | Moved to an asserting-`ask` test (rule `dynamic-argument`) | Independent review round 2 demonstrated that with `BRANCH=:dev` this command deletes a remote branch; the old DEFER was unsafe |
| 1 | `tests/unit/test_guard_hardening.py::test_new_rule_false_positives_stay_allowed[git bisect run make test]` | Moved to an asserting-`ask` test (rule `exec-option`) | Git itself executes the program given to `bisect run`; `make test` cannot be told apart from an arbitrary helper |

Other existing tests edited (all disclosed in the owning agent's report and visible in the merge history):

- `tests/integration/test_hook_guard.py::test_config_protected_branches_and_strict_mode_are_honoured`: a repository policy of
  `protected_branches: [other]` used to make a force push on `main` ASK; `main` is floor-protected now, so it is DENY.
- `tests/unit/test_core_misc.py::test_config_parse`: `protected_branches` is now the defaults plus the additions (union), not a
  replacement.
- `tests/unit/test_core_git.py::test_timeout_and_missing_git`: it forced a timeout with `-c alias.slow=!sleep 5 slow`, which the
  runner now refuses on purpose; it uses a stand-in `git` that sleeps and exercises the same `GitTimeout` path.
- `tests/acceptance/test_hostile_fixtures.py::_run_ops` (new in this release, not a baseline test): marker firings are attributed
  to the operation under test rather than to the test's own snapshot (a test bug, fixed in commit `ae73351`).

Final native result on the convergence tip: **3994 passed**, 0 failed, 0 skipped (unit, integration, security, acceptance, crash, scale).
