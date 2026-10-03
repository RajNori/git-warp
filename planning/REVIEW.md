# Independent Review

Review scope: current `codex/greenfield-v1` branch, including Claude Code plugin wiring, safety classification, Git helpers and porcelain parsing, hook protocols, persistence/privacy, all analysis and forensics services, skills, and README claims. Review was read-only apart from this report. No live Claude API/session invocation was used.

## Findings

### High — Diff-base/revision values can turn read-only analysis into arbitrary file writes

**Status: Fixed and reverified.** `analysis.common.resolve_commit` now rejects option-shaped/NUL input and resolves the value to a full commit object ID using `--end-of-options` (`scripts/git_warp/analysis/common.py:30–41`). X-Ray, PR, Blast Radius, conflict history, commit grouping, `changed_paths`, `diff_stat`, and `log_records` now pass resolved object IDs rather than caller revision text to option-sensitive Git commands. Regression coverage invokes each affected analyzer with `--output=<path>`, expects `ValueError`, and checks that no output file appears (`tests/test_analysis.py:79–94`). The affected analysis and memory tests pass.

### Medium — Blast Radius does not resolve nested working directories to the repository root

**Status: Fixed and reverified.** `analyze_blast_radius` now resolves `cwd` through the shared repository-root helper (`scripts/git_warp/analysis/blast_radius.py:35–38`). A regression fixture starts in `src/nested` and confirms that the scan still finds an importer at the repository root (`tests/test_analysis.py:72–80`).

### Medium — Memory index batches writes but still materializes full history in memory

**Status: Materialization fixed; pagination cost remains a limitation.** `_commit_shas` now returns only a bounded page and a remaining count, and `index_history` writes that page before storing its cursor (`scripts/git_warp/memory/index.py:81–153`). Coverage indexes seven commits in two-commit batches and verifies that no batch reports more than two indexed commits (`tests/test_memory.py:62–74`). This removes full SHA-list and pending-set materialization. Each page still counts the remaining commit range and uses `--skip`, so Git may revisit a large portion of history for every batch; repositories with very long histories may see repeated traversal cost. Consider a streaming/cursor approach if this becomes a measured performance problem.

## Finding corrected during review

The Guardian initially missed leading `+` refspecs when a remote was present: `git push origin +HEAD:refs/heads/main` classified as no decision. The issue was reported to the lead and the current working tree now checks refspecs after the remote (`scripts/git_warp/safety/classifier.py:221–238`). I rechecked that the protected `main` target returns DENY and a non-protected forced target returns ASK. The direct regression case should remain in the safety suite.

## Areas reviewed without a material finding

- **Claude plugin structure and lifecycle wiring:** manifest and `hooks/hooks.json` are valid. SessionStart, PreToolUse, PostToolUse, and Stop entries point to the intended launchers and match the adapters' event-specific output shapes. Stop emits `systemMessage` only for a dirty tree; PostToolUse emits `{}`. Hook commands use `${CLAUDE_PLUGIN_ROOT}` and Python's launcher adds the plugin `scripts` directory to imports.
- **Guardian parser and fail behavior:** bounded `shlex` parsing, wrapper handling, unknown-subcommand review, shell expansion ambiguity, and malformed/oversized event handling were inspected. The Guardian does not emit `allow`; ordinary commands defer to Claude permissions. Confirmed the current leading-plus push handling as described above.
- **Porcelain/path parsing:** core status parsing consumes NUL-delimited porcelain v1 entries and handles rename source records; integration tests cover whitespace/newline paths, staged/unstaged/untracked state, nested repositories, unborn HEAD, and linked remote fixtures. No material parser defect found in that tested core path.
- **Memory privacy and storage:** recorder data is allowlisted metadata, common credential forms are redacted, sensitive-looking paths are excluded, output size is capped, and symlink/non-regular-file checks are present. SQLite uses parameterized queries and stores commit metadata/path relationships rather than source text. Residual pattern-based redaction limits are documented. This review did not stress-test concurrent local symlink replacement.
- **Forensics and history claims:** rescue, conflict, bisect preflight, and temporal reports attach Git evidence and describe interpretation limits; recovery and bisect services do not perform the suggested mutations. Skills consistently frame heuristic conclusions as proposals or signals.
- **Claude validation and tests:** After the review fixes, `claude plugin validate . --strict` passed and `python3 -m unittest discover -s tests -v` passed all 63 tests. No interactive Claude session was started; live loading remains unverified.
