# Cleanup log (branch `cleanup/claude-recovery`, on top of frozen recovery checkpoint `da94148`)

Evidence sources: the recovery records kept outside this repository (the lost session ran `git rm` on these exact files at 06:39:37Z), the independent recovery review (stale scaffold scripts still present at the checkpoint), pre-wipe directory listing (`ls scripts` in the lost session listed none of them), `git ls-tree af40260` (all six exist in the scaffold), and a repo-wide search for references (below).

## Removed (normal tracked deletion via `git rm`)

| path | why obsolete | current implementation | evidence |
|---|---|---|---|
| `scripts/_common.py` | Scaffold helper with its own `subprocess.run(["git", ...])` wrapper; a second Git execution layer, contradicting ARCHITECTURE.md ("only `core/git.py` spawns `git`") | `scripts/gitwarp/core/git.py` (`run`, typed errors, timeouts) | only importers are the four scripts below; no other file imports top-level `_common` (`gitwarp/history/_common.py` is an unrelated module) |
| `scripts/git_guard.py` | Old regex guard (bypassable) | `scripts/hook_git_guard.py` → `gitwarp/hooks/git_guard.py` → `gitwarp/safety/{tokenizer,classifier}.py` | `hooks/hooks.json` references only `hook_git_guard.py`; tests exercise the new hook |
| `scripts/session_context.py` | Old SessionStart context | `hook_session_start.py` → `gitwarp/hooks/session_start.py` | hooks.json |
| `scripts/change_tracker.py` | Old recorder storing raw `tool_input` (unredacted) | `hook_post_tool.py` → `gitwarp/hooks/post_tool.py` + `gitwarp/memory/recorder.py` (redaction) | hooks.json |
| `scripts/stop_report.py` | Old Stop report | `hook_stop.py` → `gitwarp/hooks/stop.py` | hooks.json |
| `.DS_Store` | macOS Finder metadata, tracked by mistake in the scaffold | n/a (`.gitignore` already lists `.DS_Store`) | `git ls-tree af40260`; lost session removed it too |

## Reference search (before removal)
`grep -rnE "_common|change_tracker|git_guard\.py|session_context|stop_report"` over the tree (excluding `.git`): matches are only the four stale scripts importing `_common`, `hook_git_guard.py`/`gitwarp.hooks.git_guard` (new code), and recovery docs. `grep -rnE "subprocess|os\.system|Popen|shell=True|eval\(|exec\(" scripts/gitwarp` outside `core/git.py`: only a docstring in `semantic/adapters/generic.py` that mentions the rule. No migration was needed: after removal `core/git.py` is the single Git execution implementation.

## Later commits on this branch (after the removals above)

| commit | summary |
|---|---|
| 2497803 | `test`: architecture invariants (subprocess only in `core/git.py`, no dangerous calls, hooks.json targets exist, scaffold scripts stay gone) |
| 351ef9f | `fix(core)`: bound redaction cost (quadratic regex could push the guard hook past its 10 s timeout, which fails open) |
| 3a3a1b6 | `fix(guard)`: classifier bypasses from the security review |
| 64e23a0 | `fix(security)`: redaction + injected-context hardening |
| 7bd953f | `fix(skills)`: narrower `allowed-tools` |
| c964283 | `docs`: README, CHANGELOG, docs/, examples/ rebuilt |

Reviews: SECURITY_REVIEW.md (pre-hardening) and FINAL_REVIEW.md (post-hardening, with a disposition of every finding).
