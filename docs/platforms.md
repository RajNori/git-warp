# Supported and tested platforms (v0.1.0)

| Platform | Status for v0.1.0 |
|---|---|
| macOS (Darwin 25.x) with Python 3.13 and Git 2.53 | **Tested.** The full test suite, the independent acceptance harness, the fresh-install flow and the live Claude Code acceptance run were all executed here. |
| Linux | **Not yet validated.** The code uses POSIX features (`fcntl`/`flock`, `O_NOFOLLOW`, process groups) that exist on Linux, but no test run was made there. |
| Windows | **Not yet validated / unsupported for this release.** Process-group kill, `O_NOFOLLOW`, `flock` and POSIX permission bits are not available or behave differently; the storage hardening (0700/0600) is a no-op where the platform has no POSIX modes. Do not rely on Git Warp's safety properties on Windows. |

Requirements: `python3` and `git` on `PATH`; Python's `sqlite3` linked against SQLite 3.24 or newer. Code reading shows
lower bounds (Python 3.8+, `git worktree list --porcelain -z` needs Git 2.36+), but versions other than the ones above
were not tested. Claude Code `2.1.285` was used for validation, the fresh-install run and the live acceptance run.

Nothing in Git Warp makes a network call.
