# Examples

Real output captured from Git Warp on **2026-10-02** against a small throwaway repository. Nothing here was
invented: every block labelled "captured output" is JSON printed by `python3 scripts/warp.py ...` (Python 3.13.2,
git 2.53.0, macOS), trimmed for length. Blocks labelled **ILLUSTRATIVE** are hand-written renderings that show how
a skill template might present the data; they are **not** model output.

## How to read the trimmed output

- `"...": "..."` marks keys or list items that were removed. Everything that remains is verbatim.
- The repository path was replaced by `<repo>` (the real path was a temporary directory).
- Commit dates show `+10:00` because that is the local time zone of the machine that ran the commands.
- Commit hashes are specific to this throwaway repository and will differ if you rebuild it.

## The demo repository

Built by a script (not included) with plain `git` commands, author `Ada Dev`:

| Step | What it contains |
|---|---|
| `main` | 5 commits: slug helper, whitespace fixes, an in-memory `src/cache.py` that is later removed (commit `revert: remove cache helper, stale data in tests`) |
| `experiment/search` | one commit (`feat: experimental search over slugs`, `55158fbb`) on a branch that was then **deleted** with `git update-ref -d`. The commit is only dangling |
| `feature/billing` (checked out) | 3 commits ahead of `main`: a migration with a `DROP TABLE`, a new `src/billing.py`, a `package.json` + `package-lock.json` change adding `decimal.js`, and a test file |
| working tree | one modified tracked file (`src/app.py`) and one untracked file (`notes.txt`) |

The flight-recorder and hook examples use synthetic hook JSON piped into the real hook scripts.

## Index

| File | Command(s) |
|---|---|
| [xray.md](xray.md) | `warp.py xray` |
| [pr.md](pr.md) | `warp.py pr` |
| [rescue-scan.md](rescue-scan.md) | `warp.py rescue scan`, `rescue inspect`, `rescue preserve --dry-run`, `rescue preserve` |
| [commits.md](commits.md) | `warp.py commits` |
| [blast.md](blast.md) | `warp.py blast`, `warp.py blast src/util.py`, `warp.py blast --base main` |
| [archaeology.md](archaeology.md) | `warp.py archaeology src/util.py` |
| [memory-hotspots.md](memory-hotspots.md) | `warp.py memory hotspots`, `memory status`, `memory cochange` |
| [guard-check.md](guard-check.md) | `warp.py guard check ...` |
| [flight-recorder.md](flight-recorder.md) | the four hook scripts and `memory sessions` |
| [temporal.md](temporal.md) | `warp.py temporal` |
| [bisect-and-conflict.md](bisect-and-conflict.md) | `warp.py bisect plan`, `warp.py conflict` |
