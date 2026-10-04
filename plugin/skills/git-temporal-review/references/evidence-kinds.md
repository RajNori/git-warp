# Temporal evidence kinds

| kind | meaning | typical quality |
|---|---|---|
| `reintroduced-removed-code` | a line/identifier now being ADDED was REMOVED by an earlier commit (found with `git log -S`, direction verified in that commit's diff) | exact-line, identifier |
| `removed-fix-code` | lines now being REMOVED were last changed (blame) by a fix-like commit | exact-line attribution; blame may point at a refactor |
| `dependency-previously-removed` | a manifest dependency being added/removed was removed earlier in the file history (flip-flop) | identifier |
| `touched-file-has-reverts` | revert/back-out/rollback commits (by message) touched a changed file | heuristic |
| `file-history-signals` | strong-signal commits on a touched file from the memory index (only if the index exists) | heuristic |

`signals` are keyword hits in the commit subject/body: race, deadlock, leak, vuln, security, XSS, injection, validate, sanitize, revert, regression, hotfix, CVE, csrf, overflow, crash, fix. A signal is a reason to read the commit, never proof.

Bounds: at most `--budget` (default 25) pickaxe probes, `--timeout` seconds, trivial lines (braces, short, comment-only, imports) are skipped. Absence of findings is not evidence of safety.
