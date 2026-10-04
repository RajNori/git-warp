# Limited semantic quality review (v0.1.0)

The neutral acceptance harness deliberately did not judge whether the analyses are *useful*. This is a limited, human-readable
check: each command was run on a realistic repository and every claim in its output was compared with the repository's actual
contents. It is not a benchmark and does not claim the heuristics are complete.

**Fixture** (built fresh for the review, branch `feature/billing`): a small Python service with `app/payments/gateway.py`
(sensitive path), `app/core/orders.py` importing it, `tests/test_orders.py` importing `orders`, a manifest `requirements.txt` and an
unrecognised lock name `requirements.lock`, `migrations/001_orders.sql`; five commits touching the two hot files; a feature branch
adding `invoices.py`, a migration and a dependency bump (2 commits ahead of `main`); a branch with one commit that was then deleted
(`git branch -D`); and a working tree with two unstaged edits plus two untracked files, one of which contains an obvious fake API key.

| Analysis | What the output claimed | Checked against the repository | Verdict |
|---|---|---|---|
| **X-Ray** | Branch `feature/billing`; 2 unstaged (`invoices.py`, `test_orders.py`), 2 untracked (`extra.py`, `secrets_config.py`); high churn `orders.py` and `gateway.py` at 5 commits each; 2 commits ahead of `main`, no upstream; risk HIGH because of 1 secret-looking added line, MEDIUM for a sensitive path | All counts and paths match `git status` and `git log`; the secret-looking line is in the untracked file; the 5-commit churn matches `git log --follow` for both files | Correct and evidence-backed |
| **PR report** (`--base=main`) | 4 files (`invoices.py` added, `orders.py` modified, `002_invoices.sql` added, `requirements.txt` modified), +5/−1; risk MEDIUM: 1 migration changed, source changed with no test files changed | Matches `git diff main...HEAD --stat`; the migration and the absence of test changes are both true | Correct. Limitation: `requirements.txt` was changed next to `requirements.lock`, but `.lock` is not in the recognised lockfile names, so no `manifest_without_lockfile` signal (the output labels the dependency check heuristic) |
| **Commit composer** | 3 clusters: {`extra.py`, `invoices.py`}, {`secrets_config.py`}, {`tests/test_orders.py`}; no mixed concerns | The secret-bearing file is isolated in its own cluster, tests are separate; grouping by directory/kind is coherent. It proposes, stages and commits nothing | Coherent |
| **Blast radius** (`gateway.py`) | Changed → dependant `orders.py` ("imports app.payments.gateway") → test `tests/test_orders.py` ("imports app.core.orders"); level MEDIUM from the sensitive-path driver; caveat that edges are static and best effort | Both import edges exist in the source; no other file imports the module; the working-tree run reports `invoices.py` with one direct dependant (`orders.py` imports it on this branch) | Correct, with an honest caveat |
| **Rescue** | The deleted-branch commit `8121a87e` ("feat: lost-work experiment", file `app/core/lost.py`) is listed with kinds `deleted-branch-tip`, `reflog-only`, `dangling` | The SHA is exactly the commit deleted with `git branch -D`; recovery is proposed (`rescue preserve`), not executed | Correct; found by reflog and fsck |
| **Archaeology** (`gateway.py`) | Newest-first timeline: "add refund" then "orders and gateway tweak 3", … with SHAs, added/deleted counts and status | Matches `git log --follow -- app/payments/gateway.py` | Correct |
| **Temporal review** | No findings | The changed files have almost no history, so there is no repeat-fix or revert evidence to cite; reporting nothing is the honest outcome | Appropriate |
| **Bisect plan** (`--good HEAD~3 --bad HEAD`) | Planning only (`executed: false`); blocker `dirty_working_tree` naming the 2 tracked files; safe alternatives (stash, detached worktree) | The working tree is dirty exactly as stated; nothing was started | Correct and safe |

Conflict analysis (merge, rebase and cherry-pick states), the recovery states, 21 repository shapes and the privacy canaries are
covered by the acceptance suites rather than re-judged here.

**Defects found:** none that required an algorithm change. The one limitation worth knowing (`requirements.lock` not recognised as a
lockfile) is a naming-convention gap in a heuristic that is already labelled as one; no code was changed for it.
