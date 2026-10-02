# Rescue scenarios

All commands here are read-only unless marked PRESERVE. Replace `<sha>` with a full or 8-char id from `warp.py rescue scan`.
Always start with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py" rescue scan --repo "$PWD"`.

## 1. Accidental `git reset --hard`
- Signature: HEAD reflog entry `reset: moving to <target>`; candidate kind `reset-abandoned`; confidence usually high.
- The abandoned tip is the HEAD value just before that entry. Commits that were only staged/uncommitted are NOT in the reflog.
- Check: `git reflog -n 20`, `git show --stat <sha>`.
- PRESERVE: `python3 ".../warp.py" rescue preserve <sha>` then offer `git switch <rescue-branch>` or `git cherry-pick`.
- Uncommitted edits discarded by the reset are gone unless they had been staged (look at `dangling_objects.blob_samples`; `git cat-file -p <blob>` is read-only).

## 2. Deleted branch (`git branch -D`)
- Signature: HEAD reflog `checkout: moving from <name> to ...` where `<name>` is no longer a branch; kind `deleted-branch-tip`; scan lists `deleted_branch_candidates`.
- If the branch was never checked out in this clone, there is no HEAD reflog trail; rely on `dangling` candidates and `--grep` the likely commit subject.
- Remote copies: `git branch -r --contains <sha>` (read-only) or ask whether the branch exists on the remote.

## 3. Bad rebase
- Signature: reflog `rebase (start)` (kind `rebase-original`) plus `ORIG_HEAD`; scan reports `state.orig_head`.
- The pre-rebase tip holds all original commits as ancestors; one preserved branch recovers the whole series.
- Compare: `git range-diff <rescue-branch-base>...<rescue-branch> HEAD` (read-only) shows what the rebase changed.
- If the rebase is still in progress (`state.operation == "rebase"`), do not abort or continue on the user's behalf; present options.

## 4. Dropped or popped stash
- Signature: dangling commit whose subject starts `WIP on` or `On <branch>:` with 2-3 parents; kind `dropped-stash`.
- A `stash pop` that succeeded already applied the changes; compare against the working tree before treating it as lost.
- Inspect: `git show --stat <sha>`; files are diffed against the stash base (parent 1). Untracked files, if stashed with `-u`, are in the third parent.
- Restore after preserving: `git stash apply <sha>` is safe (applies, keeps the commit) but changes the working tree: explain and ask first.

## 5. Work committed on detached HEAD
- Signature: reflog `checkout: moving from <sha8> to <branch>`; kind `detached-head-work`. If the user is still detached, the candidate shows `reachable_from: ["HEAD (detached)"]`, confidence medium: preserve it before switching away.

## 6. Overwritten by force-push (local clone has the old tip)
- Signature: `refs/remotes/origin/<branch>` reflog `fetch ...: forced-update`; kind `force-push-overwritten`.
- Only works when this clone fetched the old tip earlier. Colleagues' clones, CI caches and hosting-provider "reflog"/events pages may also hold it.
- Never force-push again as part of recovery; push the preserved branch under a NEW name if sharing is needed.

## 7. Nothing found
- Reflog expiry (default 90 days reachable, 30 days unreachable) and `git gc` remove evidence. Say what was searched and what cannot be known.
- Try: widening filters (`--grep`, `--path`, no `--since`), `--include-reachable`, `rescue scan` in other worktrees (`state` lists them), IDE local history, backups.

## Confidence labels
- high: unreachable from all refs AND the reflog/stash shape shows it was a tip that was moved away from (reset, deleted branch, detached HEAD, rebase corroborated by ORIG_HEAD, stash-shaped dangling commit).
- medium: unreachable and recorded, but no explicit abandon marker; or amend originals (usually intentional); or at risk on a detached HEAD.
- low: only fsck reports it dangling, or it is reachable and was listed because of `--include-reachable`.
