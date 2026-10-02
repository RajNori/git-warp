from _common import *
root=repo_root()
if not root: raise SystemExit
def g(a):
    rc,out,_=run_git(a,cwd=root,timeout=8); return out if rc==0 else ""
status=g(["status","--short"]); diffstat=g(["diff","--stat"]); staged=g(["diff","--cached","--stat"]); branch=g(["branch","--show-current"]) or "(detached HEAD)"
changed=len(status.splitlines()) if status else 0; risk='LOW' if changed<15 else ('MEDIUM' if changed<40 else 'HIGH')
sensitive=[]
for line in status.splitlines():
    p=line[3:] if len(line)>3 else line; low=p.lower()
    if any(k in low for k in ['migration','schema','auth','permission','payment','billing','terraform','cloudformation','docker','deploy','.github/workflows','package-lock','pnpm-lock','yarn.lock']): sensitive.append(p)
if sensitive and risk=='LOW': risk='MEDIUM'
parts=['Git Warp end-of-turn report',f'- Branch: {branch}',f'- Changed paths: {changed}',f'- Change risk heuristic: {risk}']
if sensitive: parts.append('- Sensitive/high-blast-radius paths: '+', '.join(sensitive[:12]))
if diffstat: parts.append('Working tree diffstat:\n'+diffstat)
if staged: parts.append('Staged diffstat:\n'+staged)
parts.append('Recommendation: keep commits atomic; inspect git diff before committing and run tests appropriate to the changed surface.' if changed else 'Working tree is clean.')
print('\n'.join(parts))
