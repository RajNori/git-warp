# Live Claude Code acceptance

- Commit under test: `66b09d3f84b3ddc7ec3543975d1080841bce2196` (clean `git archive` export loaded with `--plugin-dir`; user/project settings excluded)
- Claude Code: 2.1.285 (Claude Code)
- Date (UTC): 2026-10-04T13:07:12Z
- Result: PASS 28, FAIL 0, INCONCLUSIVE 0; API cost ≈ $3.68
- Method: real headless sessions (`claude -p --output-format stream-json --verbose --include-hook-events`) in disposable repositories; hook events, tool calls, tool results and permission denials are read from the event stream; repository state is inspected afterwards.
- Limits: headless mode cannot show an interactive prompt, so ASK is verified as "the tool did not auto-run and Claude Code recorded a permission denial"; the plugin is loaded per session (`--plugin-dir`), not installed (installation is verified separately in an isolated config dir).

## LIVE-001 — PASS — SessionStart + Stop delivered; 10 skills and 3 agents discovered

- skills (10): ['git-warp:git-archaeology', 'git-warp:git-bisect-ai', 'git-warp:git-blast-radius', 'git-warp:git-commits', 'git-warp:git-conflict', 'git-warp:git-memory', 'git-warp:git-pr', 'git-warp:git-rescue', 'git-warp:git-temporal-review', 'git-warp:git-xray']
- agents (3): ['git-warp:git-forensic-analyst', 'git-warp:git-history-analyst', 'git-warp:git-risk-analyst']
- SessionStart hook responses: 1 exit=[0]
- Stop hook responses: 1 exit=[0]
- plugin loaded from /var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin (version 0.1.0)

## LIVE-002 — PASS — PreToolUse DENY: git reset --hard is blocked even with Bash pre-approved (work preserved)

- command: 'git reset --hard'
- PreToolUse decisions: ['deny']
- tool_result is_error=True: 'PreToolUse:Bash hook error: Git Warp: `git reset --hard` throws away all uncommitted changes in tracked files and moves the branch pointer. Uncommitted work is '
- permission_denials: 1
- branches after: ['feature/x', '*', 'main', 'scratch']
- file check: (True, 'uncommitted edit preserved')

## LIVE-003 — PASS — PreToolUse ASK: git branch -D is not auto-run despite blanket Bash pre-approval

- command: 'git branch -D scratch'
- PreToolUse decisions: ['ask']
- tool_result is_error=True: "Git Warp: Force-deleting branch 'scratch' (even if unmerged). Unmerged commits become reachable only via the reflog. Safer options: git branch -d <branch>  # re"
- permission_denials: 1
- branches after: ['feature/x', '*', 'main', 'scratch']

## LIVE-004 — PASS — PreToolUse DEFER: git status runs under ordinary permissions

- command: 'git status --short'
- PreToolUse decisions: ['defer']
- tool_result is_error=False: ' M src/app.py\n?? notes.txt'
- permission_denials: 0
- branches after: ['feature/x', '*', 'main', 'scratch']

## LIVE-005 — PASS — Dynamic expression: git reset $(echo --hard) is not run (ASK)

- command: 'git reset $(echo --hard)'
- PreToolUse decisions: ['ask']
- tool_result is_error=True: 'Git Warp: A destructive-capable Git command receives an argument produced by a command substitution or by a variable that was assigned an option-like or dynamic'
- permission_denials: 1
- branches after: ['feature/x', '*', 'main', 'scratch']
- file check: (True, 'uncommitted edit preserved')

## LIVE-006 — PASS — INTERACTIVE: ASK surfaces as a dialog when a SKILL pre-approves Bash(git branch:*) (with unguarded control)

- CONTROL (skill pre-approves Bash(git branch:*), no Git Warp): dialogs=['skill-approval']; branch deleted without a Bash prompt=True
- GUARDED (same skill + Git Warp): dialogs=['skill-approval', 'guardian-ask']; branch survives=True
- interpretation: the control proves the skill-level pre-approval is effective in an interactive session; the guarded run shows whether Guardian's ASK still surfaces as a permission dialog despite it

## LIVE-008 — PASS — TUI: Guardian ASK is a real permission dialog (reason shown); command not run

- dialogs=['guardian-ask']
- branch survives=True
- dialog text: ────────────────────────────────────────Bashcommandgitbranch-DscratchDeletethescratchbranch│HookPreToolUse:Bashrequiresconfirmationforthiscommand:│GitWarp:Force-deletingbranch'scratch'(evenifunmerged).Unmergedcommitsbecomereachableonlyviathereflog.Saferoptions:gitbranch-d<branch>#refuses│ifunmerged|gitbranchrescue/<name><branch>#keepapointersettings.jsontoupdatehooksDoyouwanttoproceed?❯1.Yes2.NoEsctocancel·Tabtoamend

## LIVE-007 — PASS — PostToolUse capture: redacted flight recorder, private state dir

- PostToolUse hook responses: 1 exit=[0]
- flight recorder exists: True lines=2 state dir mode=0o700
- synthetic secret persisted: False

## LIVE-101 — PASS — workflow skill git-xray (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" xray'
- permission_denials: 0
- repo unchanged: True
- final answer: 'GIT WARP — X-RAY  STATE Branch:      main Upstream:    none Ahead:       n/a Behind:      n/a Dirty paths: 2 (0 staged, 1 unstaged, 1 untracked, 0 conflicted) In progress: none   Stashes: 0   Worktrees: 1   Shallow: no  RISK: LOW WHY - No r'

## LIVE-102 — PASS — workflow skill git-pr (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" pr main'
- permission_denials: 0
- repo unchanged: True
- final answer: 'PR TITLE Add feature module src/feature.py (alt: "feat: feature x")  RATIONALE Unknown, ask author. The commit body is empty, there are no ticket refs, and the diff gives no intent beyond the subject "feat: feature x".  IMPLEMENTATION SUMMA'

## LIVE-103 — PASS — workflow skill git-rescue (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 2 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" rescue scan --grep pr'
- permission_denials: 0
- repo unchanged: True
- final answer: 'I found the lost commit and didn\'t modify the repository.  ``` STATE   On main @ a358eca4. No operation in progress, not shallow, no stashes, one worktree.  EVIDENCE   HEAD@{4}: commit "feat: precious work on a branch that will be deleted" '

## LIVE-104 — PASS — workflow skill git-archaeology (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" archaeology src/util.'
- permission_denials: 0
- repo unchanged: True
- final answer: 'STATE   `src/util.py`, file mode. I examined 7 commits. The history is not truncated, not shallow, and has no `--since` window, so the introduction is established. There were no warnings. I did not modify the repository.  TIMELINE   - e1e7f'

## LIVE-105 — PASS — workflow skill git-bisect-ai (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" bisect plan --good HE'
- permission_denials: 0
- repo unchanged: True
- final answer: 'STATE   Good is HEAD~5 (84c08ff7). Bad is HEAD (32d71303) on main. The range has 5 commits, no merges, and 6 changed files. No bisect is in progress. The tree is dirty: `src/app.py` is modified and `notes.txt` is untracked.  EVIDENCE   - Th'

## LIVE-106 — PASS — workflow skill git-commits (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" commits'
- permission_denials: 0
- repo unchanged: True
- final answer: 'STATE: main, 2 files, +2/-1, staged 0 / unstaged 1 / untracked 1, no operation in progress.  GROUPS: 1. root source (source): `src/app.py`. `run()` now calls `util.add(2, 3)` instead of `util.add(1, 2)`. 2. docs: `notes.txt`. Untracked, and'

## LIVE-107 — PASS — workflow skill git-blast-radius (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" blast src/util.py'
- permission_denials: 0
- repo unchanged: True
- final answer: '``` BLAST RADIUS: MEDIUM STATE: 1 changed file (explicit path), mode=paths, branch main @ ba4f1e0 TREE:   src/util.py  (source, exports: add)     depth 1  src/app.py  imports src.util     depth 1  src/m0.py … src/m5.py  import src.util (6 f'

## LIVE-108 — PASS — workflow skill git-conflict (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" conflict'
- permission_denials: 0
- repo unchanged: True
- final answer: 'CONFLICT: merge, 1 file, 1 region (nothing was modified)  `src/util.py` (both-modified), region 1 at lines 2-6 - **OURS** (HEAD, `main`): `53a89859` "main edit" changes `return a + b + 5` to `return \'main\'`. - **THEIRS** (MERGE_HEAD, branch'

## LIVE-109 — PASS — workflow skill git-temporal-review (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" temporal'
- permission_denials: 0
- repo unchanged: True
- final answer: "NO TEMPORAL EVIDENCE FOUND  - The working tree (`src/app.py` modified, `notes.txt` untracked) was compared against HEAD `a926e07` on `main`. The clone isn't shallow and there were no warnings. - The run returned 0 findings after 3 of 25 pro"

## LIVE-110 — PASS — workflow skill git-memory (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-vr_umdmc/plugin/scripts/warp.py" memory hotspots --rep'
- permission_denials: 0
- repo unchanged: True
- final answer: '`src/util.py` is the clear hotspot, with a score of 10.34. It changed in all 7 indexed commits (7 of 7 within 90 days), with 14 lines changed by 1 author, last on 2026-10-04. The next file, `src/app.py`, scores 1.7 (1 commit, 4 lines). The '

## LIVE-201 — PASS — workflow skill git-xray (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py xray appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-202 — PASS — workflow skill git-pr (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py pr appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-203 — PASS — workflow skill git-rescue (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py rescue appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-204 — PASS — workflow skill git-archaeology (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py archaeology appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-205 — PASS — workflow skill git-bisect-ai (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py bisect appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-206 — PASS — workflow skill git-commits (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py commits appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-207 — PASS — workflow skill git-blast-radius (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py blast appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-208 — PASS — workflow skill git-conflict (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py conflict appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-209 — PASS — workflow skill git-temporal-review (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py temporal appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-210 — PASS — workflow skill git-memory (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py memory appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## Defects found by this gate (and fixed before release)

1. **`--repo "$PWD"` made three skills prompt (LIVE-203/204/205, and their headless twins).** The rescue, archaeology and bisect skills told
   Claude to run `warp.py ... --repo "$PWD"`. Claude Code reports "Contains shell syntax (string) that cannot be statically analyzed" for
   `"$PWD"` and asks, even though the `warp.py` prefix is pre-approved. The other seven skills run from the current directory and passed.
   Fixed by dropping `--repo "$PWD"` (the CLI defaults to the current directory), adding a note to the skills, and a regression test
   (`tests/unit/test_skills_no_shell_expansion.py`). The run above is the re-run on the fixed skills.
2. **Headless mode cannot evaluate a skill's `allowed-tools`.** In `claude -p` a skill's `allowed-tools` does not pre-approve Bash at all
   (an unguarded control with a skill allowing `Bash(git commit:*)` still required approval), whereas a CLI `--allowedTools` rule does. The
   question "does Guardian's ASK survive a skill-level pre-approval?" was therefore answered in the **real interactive TUI** (LIVE-006):
   with the same skill alone, the command ran with no Bash prompt (the pre-approval is effective); with Git Warp loaded, Guardian's
   ASK still appeared as a permission dialog ("Hook PreToolUse:Bash requires confirmation for this command ...") and the branch survived.
3. **Runner mistakes found along the way (not product defects):** scenario retries reused a repository directory name; the Skill tool itself
   needs approval in headless mode; a dialog classifier matched the echoed prompt instead of the dialog title. All are fixed in
   `tests/live/live_acceptance.py` and `tests/live/tui_driver.py`.

## How to reproduce

```bash
python3 tests/live/live_acceptance.py --budget 1.0        # full run (about 25 minutes, a few dollars of API usage)
python3 tests/live/live_acceptance.py --only LIVE-006      # one scenario, or a group: discovery,guard,tui,recorder,skills,skills-tui
```
