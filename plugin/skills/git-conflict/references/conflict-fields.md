# `warp.py conflict` fields

- `operation`: merge | rebase | cherry-pick | revert | null. `theirs.ref` is MERGE_HEAD / REBASE_HEAD / CHERRY_PICK_HEAD / REVERT_HEAD.
- `swap_note`: present for rebase (ours/theirs swapped), revert and cherry-pick semantics.
- `merge_base`, `pick_base` (parent of the replayed commit for rebase/cherry-pick/revert), `rebase` (onto, branch being rebased).
- per file: `conflict_type` = both-modified | both-added | deleted-by-ours | deleted-by-theirs | both-deleted | added-by-us | added-by-them; `stages` {1 base, 2 ours, 3 theirs} with blob shas (inspect with `git show <sha>`); `binary`, `worktree_missing`, `line_endings`, `rename_hints`, `submodule`.
- `regions[]`: `start_line/end_line`, `ours/theirs/base` {text,line_count}, `*_range`, `has_diff3_base`, `blame.ours/theirs` (commits that last touched those lines on each side), `relationship_hint`.
- `history.ours/theirs`: commits since the merge base on each side that touched the file.
- `relationship_hint.hint` values: identical-change, whitespace-only-difference, one-side-only-whitespace, only-ours-changed, only-theirs-changed (diff3 only), independent-hunks, overlapping, delete-vs-modify, add-add-different, both-deleted, binary-choose-a-side, submodule-pointer-conflict, unclear. Always a hint.
- Without `merge.conflictstyle=diff3` the base text is absent from markers; suggest `git config merge.conflictstyle diff3` plus `git checkout --merge -- <path>` only to the user, as text (that command rewrites the file).
