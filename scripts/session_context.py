from _common import *
root=repo_root()
if not root:
    print("Git Warp: current directory is not inside a Git repository."); raise SystemExit

def g(a):
    rc,out,_=run_git(a,cwd=root); return out if rc==0 else ""
branch=g(["branch","--show-current"]) or "(detached HEAD)"
status=g(["status","--short"]); upstream=g(["rev-parse","--abbrev-ref","--symbolic-full-name","@{u}"])
ab=g(["rev-list","--left-right","--count",f"{upstream}...HEAD"]) if upstream else "n/a"
recent=g(["log","-5","--pretty=format:%h %ad %s","--date=short"])
stashes=len([x for x in g(["stash","list"]).splitlines() if x.strip()])
print(f"""Git Warp repository context
- Root: {root}
- Branch: {branch}
- Upstream: {upstream or 'none'}
- Ahead/behind: {ab}
- Dirty paths: {len(status.splitlines()) if status else 0}
- Stashes: {stashes}
Recent commits:
{recent or '(none)'}
Safety policy: preserve work, prefer reversible Git operations, inspect reflog before recovery, explain history rewrites before performing them.
""")
