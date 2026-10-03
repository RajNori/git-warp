---
name: git-rescue
description: Recover lost commits, branches, stashes, or overwritten work using reflog and Git object history.
argument-hint: "[args]"
allowed-tools: [Read, Glob, Grep, Bash]
---

# git-rescue

Treat recovery as read-only forensics. Use Git Warp's recovery discovery service to inventory branch and remote refs, all reflogs, stash entries, and unreachable commits. The service does not create refs or change the index or worktree. Keep each candidate's source (reflog selector, stash selector, ref, or fsck output) attached to the commit ID.

For promising candidates, inspect `git show --stat <commit>` and then `git show <commit>`; compare the tree and relevant files with the user's expected work. A candidate's presence is a fact; whether it is the lost work is an inference until its content is verified. If the evidence does not establish what was lost, say so.

After a branch is deleted, its branch reflog may be gone while `HEAD` reflog entries or unreachable objects still retain the tip commit. Report the exact surviving selector or `fsck` evidence, and do not claim the deleted branch name unless that name is still recorded in Git output.

Never run `gc`, `prune`, `reflog expire`, `reset --hard`, `clean -f`, or destructive restore while investigating. Do not run recovery commands automatically. Once the user chooses a verified candidate, present a narrowly scoped command such as `git branch rescue/<name> <commit>` for the user to run, then verify the resulting ref and content before cleanup.
