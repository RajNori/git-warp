from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

def read_event():
    try:
        raw=sys.stdin.read(); return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}

def run_git(args,cwd=None,timeout=5):
    try:
        p=subprocess.run(["git",*args],cwd=cwd,capture_output=True,text=True,timeout=timeout,check=False)
        return p.returncode,p.stdout.strip(),p.stderr.strip()
    except Exception as e:
        return 1,"",str(e)

def repo_root():
    rc,out,_=run_git(["rev-parse","--show-toplevel"]); return Path(out) if rc==0 and out else None

def json_out(obj):
    sys.stdout.write(json.dumps(obj))
