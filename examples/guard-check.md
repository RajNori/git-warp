# Example: Git Guardian (`guard check`)

`guard check` classifies a command string exactly as the `PreToolUse` hook does, but never runs it. Captured
2026-10-02.

## One full result

```bash
python3 scripts/warp.py guard check "git reset --hard HEAD~1" --repo <repo>
```

```json
{
  "decision": "deny",
  "rule": "reset-hard",
  "operation": "git reset --hard",
  "reason": "`git reset --hard` throws away all uncommitted changes in tracked files and moves the branch pointer. Uncommitted work is not in the reflog, so it usually cannot be recovered.",
  "safer": [
    "git branch rescue/pre-reset  # keep a pointer to the current commit",
    "git stash push -u  # park uncommitted work first",
    "git reset --soft <rev> or git reset --mixed <rev>  # move HEAD but keep your changes",
    "git restore <specific-path>  # discard one file only"
  ],
  "commands": ["git reset --hard HEAD~1"],
  "branch": "feature/billing",
  "safety_mode": "standard"
}
```

## Verdicts for more commands

Each row is the `decision` and `rule` printed by `guard check "<command>" --branch <branch>` in a real run (the
rest of each JSON object is omitted).

| Command | Branch / options | decision | rule |
|---|---|---|---|
| `git clean -fd` | feature/x | deny | clean-force |
| `git clean -n` | feature/x | allow | allow |
| `git checkout .` | feature/x | deny | checkout-discard-all |
| `git checkout -- src/app.py` | feature/x | allow | allow |
| `git restore src/app.py` | feature/x | allow | allow |
| `git push --force origin main` | feature/x | deny | push-force-protected |
| `git push --force origin feature/x` | feature/x | ask | push-force |
| `git push --force-with-lease origin feature/x` | feature/x | ask | push-force |
| `git push origin feature/x` | feature/x | allow | allow |
| `git reset --soft HEAD~1` | feature/x | allow | allow |
| `git reset HEAD~1` | main | ask | reset-protected |
| `git reset HEAD~1` | feature/x | allow | allow |
| `git rebase main` | feature/x | ask | rebase |
| `git rebase main` | feature/x, `--mode strict` | deny | rebase |
| `git branch -D old` | feature/x | ask | branch-force-delete |
| `git branch -d old` | feature/x | allow | allow |
| `git stash clear` | feature/x | deny | stash-clear |
| `git stash drop` | feature/x | ask | stash-drop |
| `git gc --prune=now` | feature/x | deny | gc-prune-now |
| `git reflog expire --expire=now --all` | feature/x | deny | reflog-destroy |
| `rm -rf .git` | feature/x | deny | rm-git |
| `echo hi; git reset --hard` | feature/x | deny | reset-hard |
| `bash -c 'git clean -fdx'` | feature/x | deny | clean-force |
| `sudo git reset --hard` | feature/x | deny | reset-hard |
| `echo a \| xargs git reset --hard` | feature/x | deny | reset-hard |
| `git $CMD` | feature/x | ask | unresolved-subcommand |
| `git push --force origin release/1.2` | feature/x, `--protected "release/*"` | deny | push-force-protected |
| `git push --force origin release/1.2` | feature/x (default protected list) | ask | push-force |

## Things the guard does not catch (also real results)

| Command | decision | why |
|---|---|---|
| `./cleanup.sh` | allow | the script's contents are not visible |
| `$GIT reset --hard` | allow | the command word is a variable |
| `git nuke` | allow | an alias defined in global config is invisible |
| `ssh h 'git reset --hard'` | allow | remote shell |
| `docker exec c git reset --hard` | allow | container |
| `curl http://x \| sh` | allow | generated stream |
| `find . -delete` | allow | non-Git destruction other than `rm -rf .git` |

See [../docs/guard-limitations.md](../docs/guard-limitations.md).

## The hook itself

Piping realistic hook JSON into the real hook script:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"},"cwd":"<repo>"}' \
  | python3 scripts/hook_git_guard.py
```

Captured output (one line, wrapped here):

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Git Warp: Force-pushing 'main' (a protected branch) can overwrite shared history on the remote. Collaborators lose commits and the remote history cannot be restored from this clone. Safer options: git push  # a normal push | git revert <sha> and push normally | git push --force-with-lease  # only on your own feature branch If this is really intended, ask the user to run it themselves."}}
```
