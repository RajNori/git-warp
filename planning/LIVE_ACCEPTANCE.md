# Live Claude Code acceptance

- Commit under test: `f57423b13719407aca9f0a980fbc6139879eba13` (clean `git archive` export loaded with `--plugin-dir`; user/project settings excluded)
- Claude Code: 2.1.285 (Claude Code)
- Date (UTC): 2026-10-04T12:53:24Z
- Result: PASS 22, FAIL 6, INCONCLUSIVE 0; API cost ≈ $3.81
- Method: real headless sessions (`claude -p --output-format stream-json --verbose --include-hook-events`) in disposable repositories; hook events, tool calls, tool results and permission denials are read from the event stream; repository state is inspected afterwards.
- Limits: headless mode cannot show an interactive prompt, so ASK is verified as "the tool did not auto-run and Claude Code recorded a permission denial"; the plugin is loaded per session (`--plugin-dir`), not installed (installation is verified separately in an isolated config dir).

## LIVE-001 — PASS — SessionStart + Stop delivered; 10 skills and 3 agents discovered

- skills (10): ['git-warp:git-archaeology', 'git-warp:git-bisect-ai', 'git-warp:git-blast-radius', 'git-warp:git-commits', 'git-warp:git-conflict', 'git-warp:git-memory', 'git-warp:git-pr', 'git-warp:git-rescue', 'git-warp:git-temporal-review', 'git-warp:git-xray']
- agents (3): ['git-warp:git-forensic-analyst', 'git-warp:git-history-analyst', 'git-warp:git-risk-analyst']
- SessionStart hook responses: 1 exit=[0]
- Stop hook responses: 1 exit=[0]
- plugin loaded from /var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin (version 0.1.0)

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
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" xray'
- permission_denials: 0
- repo unchanged: True
- final answer: 'GIT WARP — X-RAY  STATE Branch:      main Upstream:    none Ahead:       n/a Behind:      n/a Dirty paths: 2 (0 staged, 1 unstaged, 1 untracked, 0 conflicted) In progress: none   Stashes: 0   Worktrees: 1   Shallow: no  RISK: LOW WHY - No r'

## LIVE-102 — PASS — workflow skill git-pr (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" pr main'
- permission_denials: 0
- repo unchanged: True
- final answer: 'PR TITLE Add feature module computing F via util.add  RATIONALE Unknown, ask author. The commit body and ticket refs are empty. The only evidence is the subject "feat: feature x".  IMPLEMENTATION SUMMARY - root source cluster: `src/feature.'

## LIVE-103 — FAIL — workflow skill git-rescue (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 3 (errors: 1); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" rescue scan --repo "$'
- permission_denials: 1
- repo unchanged: True
- final answer: 'I found the lost commit and changed nothing in the repository. I did not run `preserve`, since you said not to modify it.  - **Commit:** `514ba598` "feat: precious work on a branch that will be deleted" adds one line to `src/precious.py`. I'

## LIVE-104 — FAIL — workflow skill git-archaeology (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 2 (errors: 1); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" archaeology src/util.'
- permission_denials: 1
- repo unchanged: True
- final answer: '``` STATE   src/util.py, file mode, 7 commits examined on main (HEAD 1d633ae4). Not truncated, not shallow, no --since, no warnings.  TIMELINE   61983898 (2026-10-04) feat: initial app    - adds file (2 lines)   e43e65a5 feat: module 0     '

## LIVE-105 — FAIL — workflow skill git-bisect-ai (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 2 (errors: 1); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" bisect plan --good HE'
- permission_denials: 1
- repo unchanged: True
- final answer: '**Bisect plan: `HEAD~5` (good) to `HEAD` (bad). Not ready, and I started no bisect.**  ``` STATE   good 772e4401 (HEAD~5) -> bad e36faf32 (HEAD, main). 5 commits, 0 merges, 6 files changed, not shallow, no operation in progress.   Working t'

## LIVE-106 — PASS — workflow skill git-commits (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" commits'
- permission_denials: 0
- repo unchanged: True
- final answer: 'STATE: main @ c469929, 2 files, +2/-1, staged 0 / unstaged 1 / untracked 1, no operation in progress  GROUPS:   1. root source (source) `src/app.py`: `run()` changes its arguments from `util.add(1, 2)` to `util.add(2, 3)`.   2. docs (docs) '

## LIVE-107 — PASS — workflow skill git-blast-radius (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" blast src/util.py'
- permission_denials: 0
- repo unchanged: True
- final answer: '``` BLAST RADIUS: MEDIUM STATE: 1 changed file, mode=paths, branch main @ 5e1dc84a TREE:   src/util.py  (source, exports: add)     depth 1  src/app.py  imports src.util     depth 1  src/m0.py … src/m5.py  imports src.util (6 files)     test'

## LIVE-108 — PASS — workflow skill git-conflict (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" conflict'
- permission_denials: 0
- repo unchanged: True
- final answer: 'CONFLICT: merge, 1 file, 1 region  src/util.py (both-modified), region 1 at lines 2-6   OURS (HEAD, `main`): replaces the body of `add` with `return \'main\'`. Commit 46e549fa "main edit".   THEIRS (MERGE_HEAD, `side`): replaces the body of `'

## LIVE-109 — PASS — workflow skill git-temporal-review (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" temporal'
- permission_denials: 0
- repo unchanged: True
- final answer: 'NO TEMPORAL EVIDENCE FOUND  The temporal scan of the working tree against HEAD (`a1ba9b7`) returned no findings. It covered the changed files `src/app.py` and `notes.txt`. It used 3 of 25 probes and ran without timeouts or warnings. The clo'

## LIVE-110 — PASS — workflow skill git-memory (headless, content): invoked, warp.py ran, repo unchanged

- skill invoked: 1x
- warp.py invocations: 1 (errors: 0); first: 'python3 "/var/folders/4q/19z5p10s14l52wc5kz32bj240000gn/T/gw-live-q_4wk8ja/plugin/scripts/warp.py" memory hotspots --rep'
- permission_denials: 0
- repo unchanged: True
- final answer: '**Hotspots** (index complete, 7 commits, head `1bebc52`; recency-weighted churn, so a relative signal and not a bug prediction):  | File | Score | Commits | Lines changed | |---|---|---|---| | `src/util.py` | 10.34 | 7 | 14 | | `src/app.py`'

## LIVE-201 — PASS — workflow skill git-xray (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval', 'claude-own-bash-permission']
- warp.py xray appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0
- note: a prompt for a different command (not covered by this skill's allowed-tools): ken|secret|password)[^]*/[x]/Ig'Inspectifiedfilediffandnoe│sedcommandcontainsoperationsthatrequireexplicitapproval(e.g.,writecommands,executecommands)Doyouwanttoproceed?❯1.Yes2.Yes,andswitchtoautomode·automodehandlesthesepromptsforyou3.NoEsctocancel·Tabtoamend

## LIVE-202 — PASS — workflow skill git-pr (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval']
- warp.py pr appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 0

## LIVE-203 — FAIL — workflow skill git-rescue (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval', 'claude-own-bash-permission']
- warp.py rescue appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 1
- FAIL detail: 4wk8ja/plugin/scripts/warp.py"rescuescan--repo"$PWD"--greppreciousScforlostcommits(read-only)Containsshellsyntax(string)thatcannotbestaticallyanalyzedDoyouwanttoproceed?❯1.Yes2.Yes,andswitchtoautomode·automodehandlesthesepromptsforyou3.NoEsctocancel·Tabtoamend

## LIVE-204 — FAIL — workflow skill git-archaeology (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval', 'claude-own-bash-permission']
- warp.py archaeology appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 1
- FAIL detail: 4wk8ja/plugin/scripts/warp.py"archaeologysrc/util.py--repo"$PWD"Rungitarchaeologyonsrc/util.yContainsshellsyntax(string)thatcannotbestaticallyanalyzedDoyouwanttoproceed?❯1.Yes2.Yes,andswitchtoautomode·automodehandlesthesepromptsforyou3.NoEsctocancel·Tabtoamend

## LIVE-205 — FAIL — workflow skill git-bisect-ai (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)

- dialogs=['skill-approval', 'claude-own-bash-permission']
- warp.py bisect appears in the session: True
- repo unchanged: True
- prompts for the warp.py call (allowed-tools pattern failed to match): 1
- FAIL detail: wk8ja/plugin/scripts/warp.py"bisectplan--goodHEAD~5--badHEAD--repo"$PWD"Runread-nlybisectplanContainsshellsyntax(string)thatcannotbestaticallyanalyzedDoyouwanttoproceed?❯1.Yes2.Yes,andswitchtoautomode·automodehandlesthesepromptsforyou3.NoEsctocancel·Tabtoamend

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
