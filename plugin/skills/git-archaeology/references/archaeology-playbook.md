# Archaeology playbook

## Choosing the query
| Question | Use | Caveat |
|---|---|---|
| "history of this file" | path | follows renames with `--follow`; a rename below ~50% similarity looks like delete+add |
| "when did X first appear" | `--symbol X` | `-S` counts occurrences: a moved line is not reported; the oldest hit is where X first appeared, usually its introduction |
| "which commits touched code like this" | `--regex` | POSIX ERE; quote carefully |
| "why was X changed" | `--question` | message text only; no code is read |
| "what happened to a directory" | directory path | no rename following; churn is summed over the directory |

## Reading the output
- `introduction.tag == "fact"` only when the window is complete and the oldest commit adds the path. Otherwise say "oldest commit seen".
- `large_rewrites` threshold: churn at least 50 lines and twice the median. A formatter run, vendored update or file move can look like a rewrite: open the commit before calling it a redesign.
- `fix_like_commits` are message keyword matches. They do not prove a bug existed. Order by `blame_summary` survival to find the fixes whose lines still live in the file.
- Revert pairing: `linked_via: body` comes from "This reverts commit <sha>" (fact). `subject-match` pairs `Revert "<subject>"` with an identical subject (inference). A revert of a revert ("Reapply") appears as another entry; follow the chain manually.
- `merge_points` show where branch work arrived on the first-parent line; the merge message often names the PR/issue.
- `deleted_then_restored` and `currently_deleted` come from add/delete statuses in followed history.

## Phrasing
- Fact: "Commit a1b2c3d4 (2024-03-02, Sam) added the file."
- Inference: "The message suggests this was a bug fix (keyword 'fix'); I have not verified the behaviour changed."
- Unknown: "Git does not record why the retry loop was removed; the commit message says only 'cleanup'."

## Useful read-only follow-ups
- `git show --stat <sha>`; `git show <sha> -- <path>`
- `git log -L :<funcname>:<path>` for function-level history
- `git blame -w -C -C <path>` to see lines moved between files
- `git log --all --oneline -S'<text>'` to look beyond HEAD's history (unmerged branches)
- Issue/PR numbers in messages: ask the user to open them; Git Warp does not fetch from the network.
