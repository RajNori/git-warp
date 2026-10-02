from _common import *
from datetime import datetime,timezone
event=read_event(); root=repo_root()
if not root: json_out({}); raise SystemExit
rc,gd,_=run_git(["rev-parse","--git-dir"],cwd=root)
if rc!=0: json_out({}); raise SystemExit
gitdir=Path(gd); gitdir=gitdir if gitdir.is_absolute() else root/gitdir
d=(gitdir/'git-warp'); d.mkdir(parents=True,exist_ok=True)
with (d/'flight-recorder.jsonl').open('a',encoding='utf-8') as f:
    f.write(json.dumps({'ts':datetime.now(timezone.utc).isoformat(),'event':event.get('hook_event_name') or 'PostToolUse','tool':event.get('tool_name'),'tool_input':event.get('tool_input')},ensure_ascii=False)+'\n')
json_out({})
