# Build Plan

## Phases and gates

1. Verify Claude Code plugin spec and capture sources. **Complete.**
2. Freeze architecture and hook/Git/result contracts before parallel code. **Complete.**
3. Implement the shared argv-only Git layer and temporary-repository fixtures. **Complete.**
4. Replace the guardian with a deterministic bounded shell lexer/classifier and real adversarial tests. **Complete.**
5. Implement recovery, archaeology, and deterministic bisect guidance/services. **Complete.**
6. Implement X-Ray and PR evidence analysis. **Complete.**
7. Add commit grouping, blast radius, conflict analysis, and temporal review with explainable heuristics. **Complete.**
8. Add redacted flight recorder and incremental SQLite repository index. **Complete.**
9. Security hardening and malformed/hostile-input tests. **Complete.**
10. Run unit/integration/security checks and plugin static validation. **Complete.**
11. Independent review; resolve Critical/High findings and investigate Medium findings. **Complete; no open Critical/High findings.**
12. Align README and skills with verified behavior, then inspect full diff and leave a clean reviewable branch. **Complete.**

## Acceptance rules

- Every analyzer is read-only and returns evidence; rescue suggestions create no refs.
- Guardian blocks destructive patterns deterministically, escalates ambiguous/history-rewriting patterns, and never claims to parse all shell syntax.
- Runtime state stays under `.git/git-warp/`; raw prompts/tool payloads are excluded.
- The manifest/hooks are checked as JSON and against official Claude Code references. Live CLI loading is reported only if actually run.
- Tests use temporary real Git repositories for Git behavior and pure functions for parsing/classification.
- No push, PR, merge, publication, branch deletion, force operation, or history rewrite.
