from _common import *
import re

event=read_event(); tool_input=event.get("tool_input") or {}; cmd=str(tool_input.get("command") or "").strip()
if not cmd: json_out({}); raise SystemExit
normalized=re.sub(r"\s+"," ",cmd).strip()
blocks=[
 (r"(^|[;&|]\s*)git\s+reset\s+--hard\b","git reset --hard can irreversibly discard uncommitted work."),
 (r"(^|[;&|]\s*)git\s+clean\s+-(?=[A-Za-z]*f)(?=[A-Za-z]*d)[A-Za-z]+\b","git clean with -f/-d can permanently delete untracked files."),
 (r"(^|[;&|]\s*)git\s+checkout\s+--\s+\.","git checkout -- . can discard all working-tree edits."),
 (r"(^|[;&|]\s*)git\s+restore(?:\s+--worktree)?\s+\.","git restore . can discard all working-tree edits."),
 (r"(^|[;&|]\s*)git\s+reflog\s+expire\b","Expiring reflogs removes important recovery history."),
 (r"(^|[;&|]\s*)git\s+gc\b.*--prune(?:=now|\s+now)?","Aggressive pruning can destroy recoverable Git objects.")]
for pat,reason in blocks:
    if re.search(pat,normalized,re.I):
        json_out({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':'Git Warp blocked this destructive Git operation: '+reason+' Use a recovery-safe workflow instead.'}}); raise SystemExit

if re.search(r"\bgit\s+push\b.*(?:--force(?:-with-lease)?|-f)\b",normalized,re.I):
    protected={'main','master','develop','development','production','prod','release'}
    rc,branch,_=run_git(['branch','--show-current']); branch=branch.strip() if rc==0 else ''
    if branch in protected:
        json_out({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':f"Git Warp blocked a force-push from protected branch '{branch}'. Use a new branch or an explicitly reviewed recovery workflow."}}); raise SystemExit

asks=[(r"\bgit\s+branch\s+-D\b","forced branch deletion"),(r"\bgit\s+push\b.*--delete\b","remote branch deletion"),(r"\bgit\s+rebase\b","history rewriting with rebase"),(r"\bgit\s+commit\b.*--amend\b","rewriting the previous commit")]
for pat,label in asks:
    if re.search(pat,normalized,re.I):
        json_out({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'ask','permissionDecisionReason':'Git Warp detected '+label+'. Review this operation before execution.'}}); raise SystemExit
json_out({})
