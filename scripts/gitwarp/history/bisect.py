"""Bisect planning and status.  Never starts a bisect and never runs a test command (ADR-9)."""
from __future__ import annotations

import math
import re
import shlex
from pathlib import Path
from typing import Optional

from ..core import git
from ..core.config import load_config
from ..core.paths import classify_path
from ..core.redact import redact
from ._common import brief, clip, commit_files, is_ancestor, repo_state

PREDICATE_GUIDANCE = {
    "exit_codes": {
        "0": "this commit is GOOD (the bug is absent)",
        "1-124, 126, 127": "this commit is BAD (the bug is present)",
        "125": "SKIP: this commit cannot be tested (does not build, unrelated breakage)",
        "128-255": "ABORT the whole bisect (use for 'the test harness itself is broken')",
    },
    "rules": [
        "Make the predicate deterministic: same commit, same result, every run. If it is flaky, repeat it N times inside the script and fail only on a stable failure, or bisect manually.",
        "Test the symptom, not an implementation detail: assert the observable wrong behaviour (a failing test, a wrong output) rather than a specific file content.",
        "Exit 125 (not 1) when the build/setup fails, otherwise an unrelated build break is blamed as 'the culprit'.",
        "Keep the script OUTSIDE the work tree (git checks out old commits that may not contain it) and make it executable.",
        "Rebuild/reinstall dependencies inside the script when lockfiles or manifests changed across the range; reset any database state when migrations changed.",
        "Do not modify tracked files from the script; if it must, `git stash`/`git checkout -- .` afterwards so the next checkout is clean.",
        "Dry-run the predicate yourself on the good ref (expect 0) and the bad ref (expect 1) BEFORE starting the bisect.",
    ],
}


def _validate_test(test: Optional[str]) -> dict:
    if test is None:
        return {"provided": False}
    issues = []
    if not test.strip():
        issues.append("test command is empty")
    if any(ch in test for ch in ("\n", "\r", "\x00")):
        issues.append("test command must be a single line (no newline/NUL)")
    warnings = []
    if re.search(r"(?<!\|)\|(?!\|)", test):
        warnings.append("a pipeline reports only the last command's exit status; use `set -o pipefail` in a script")
    if ";" in test:
        warnings.append("with `;` only the last command's exit status counts; prefer `&&` or a script")
    shown = redact(test)
    if shown != test:
        warnings.append("secret-like text was redacted from the echo; never put credentials in a bisect test command")
    return {"provided": True, "valid": not issues, "issues": issues, "warnings": warnings, "echo": clip(shown, 400),
            "executed": False, "note": "Git Warp only validated this string syntactically; it was NOT executed."}


def plan(cwd, good: list, bad: Optional[str], test: Optional[str] = None) -> tuple:
    warnings: list = []
    blockers: list = []
    state = repo_state(cwd)
    base = {"command": "bisect plan", "executed": False,
            "note": "Planning only: Git Warp never starts a bisect or runs your test command.", "state": state}
    if not good:
        return {**base, "error": "at least one --good REF is required (usage: bisect plan --good REF --bad REF [--test CMD])"}, 2
    if state["unborn"]:
        blockers.append({"code": "unborn_repository", "message": "the repository has no commits, nothing to bisect"})
        return {**base, "ready": False, "blockers": blockers, "warnings": warnings}, 0
    if bad is None:
        bad = "HEAD"
        warnings.append("--bad not given: defaulting to HEAD")

    # --- resolve refs
    def res(ref):
        try:
            return git.rev_parse(ref, cwd)
        except ValueError:
            return None

    bad_sha = res(bad)
    good_res = [(g, res(g)) for g in good]
    for name, sha in [(bad, bad_sha)] + good_res:
        if not sha:
            blockers.append({"code": "unknown_ref", "message": f"{name!r} does not resolve to a commit", "suggest": ["git log --oneline -20   # find a commit id", "git tag --list"]})
    if blockers:
        return {**base, "ready": False, "blockers": blockers, "warnings": warnings}, 0
    good_shas = [s for _, s in good_res]

    # --- operation / tree
    if state["operation"]:
        op = state["operation"]
        blockers.append({"code": "operation_in_progress", "message": f"a {op} is already in progress",
                         "suggest": (["python3 scripts/warp.py bisect status   # inspect the running bisect", "git bisect reset   # only when you are done with it"] if op == "bisect"
                                     else [f"finish or abort the {op} first (git {op} --continue / --abort)"])})
    try:
        status = git.working_tree_status(cwd)
    except git.GitError as e:
        status = []
        warnings.append(f"could not read working tree status: {clip(str(e), 120)}")
    dirty = [e for e in status if not e.untracked]
    untracked = [e for e in status if e.untracked]
    wt_path = None
    root = git.repo_root(cwd) or Path(cwd)
    wt_path = str(root.parent / f"{root.name}-bisect-{bad_sha[:8]}")
    wt_cmd = f"git worktree add --detach {shlex.quote(wt_path)} {bad_sha}"
    if dirty:
        blockers.append({
            "code": "dirty_working_tree",
            "message": f"{len(dirty)} tracked file(s) have uncommitted changes; bisect checks out other commits and would clobber or be confused by them",
            "files": [e.path for e in dirty[:10]],
            "suggest": ["git stash push -u -m 'before bisect'   # then `git stash pop` after `git bisect reset`", wt_cmd + "   # or bisect in an isolated worktree, leaving this tree untouched"]})
    if untracked:
        warnings.append(f"{len(untracked)} untracked file(s) present; they can make a test pass/fail independently of the commit (consider `git stash push -u` or a worktree)")

    # --- ancestry
    for g, gs in good_res:
        if gs == bad_sha:
            blockers.append({"code": "good_equals_bad", "message": f"good ({g}) and bad ({bad}) are the same commit"})
            continue
        anc = is_ancestor(gs, bad_sha, cwd)
        if anc is False:
            rev = is_ancestor(bad_sha, gs, cwd)
            mb = git.merge_base(gs, bad_sha, cwd)
            msg = f"good ({g}) is not an ancestor of bad ({bad}); bisect needs the bad commit to descend from the good one"
            sug = []
            if rev:
                msg += "; the refs look swapped (bad is an ancestor of good)"
                sug.append(f"python3 scripts/warp.py bisect plan --good {shlex.quote(bad)} --bad {shlex.quote(g)}")
            elif mb:
                sug.append(f"use the merge base as good: {mb}")
            else:
                msg += "; the two refs share no history" + (" (shallow clone? try `git fetch --unshallow`)" if state["shallow"] else "")
            blockers.append({"code": "good_not_ancestor_of_bad", "message": msg, "merge_base": mb, "suggest": sug})
        elif anc is None:
            warnings.append(f"could not determine whether {g} is an ancestor of {bad} (missing objects? shallow clone?)")

    # --- range
    rng = {}
    if not any(b["code"] in ("good_equals_bad", "good_not_ancestor_of_bad") for b in blockers):
        revs = [bad_sha] + ["^" + g for g in good_shas]
        cnt = git.run(["rev-list", "--count", *revs], cwd=cwd, timeout=60)
        mcnt = git.run(["rev-list", "--count", "--merges", *revs], cwd=cwd, timeout=60)
        n = int(cnt.text) if cnt.ok and cnt.text.isdigit() else None
        m = int(mcnt.text) if mcnt.ok and mcnt.text.isdigit() else 0
        if n is not None:
            steps = math.ceil(math.log2(n)) if n > 1 else 0
            merges = git.log_commits(revs, limit=10, extra=["--merges"], cwd=cwd)
            rng = {"commits_in_range": n, "expected_steps": steps,
                   "steps_note": f"about ceil(log2({n})) = {steps} test runs; skips and merges can add a few",
                   "merge_commits": m, "merge_commit_samples": [brief(c) for c in merges]}
            if m:
                warnings.append(f"{m} merge commit(s) in range: the history is not linear, so bisect may land on a merge or a side-branch commit; "
                                "add --first-parent to `git bisect start` to bisect only mainline merges, then bisect inside the found merge")
            if n > 5000:
                warnings.append("very large range; consider narrowing --good")
    if state["shallow"]:
        warnings.append("shallow clone: commits before the boundary are missing, so the real culprit may be unreachable (`git fetch --unshallow` would fix that; Git Warp did not run it)")

    # --- reproducibility risks
    risks = []
    if rng:
        d = git.run(["diff", "--name-only", "-z", "--no-renames", good_shas[0], bad_sha, "--"], cwd=cwd, timeout=60)
        names = [p for p in d.stdout.split("\x00") if p] if d.ok else []
        if len(names) > 5000:
            warnings.append("more than 5000 changed files in range; reproducibility scan limited to the first 5000")
            names = names[:5000]
        cfg = load_config(root)
        cats = {"lockfile": [], "manifest": [], "migration": [], "schema": [], "ci_infra": []}
        for p in names:
            t = classify_path(p, cfg)
            if "lockfile" in t:
                cats["lockfile"].append(p)
            elif "dependency" in t:
                cats["manifest"].append(p)
            if "migration" in t:
                cats["migration"].append(p)
            elif "schema" in t:
                cats["schema"].append(p)
            if "ci" in t or "infra" in t:
                cats["ci_infra"].append(p)
        why = {
            "lockfile": "dependency lockfiles changed across the range: the installed dependency set must be rebuilt per step or old commits will run against new libraries",
            "manifest": "dependency manifests changed across the range: reinstall dependencies inside the predicate",
            "migration": "database migrations changed across the range: persistent DB state will not match each commit; recreate the DB per step",
            "schema": "schema/API definition files changed across the range: generated clients or fixtures may need regenerating per step",
            "ci_infra": "build/CI/infrastructure files changed across the range: the build itself may differ between steps (use exit 125 for unbuildable commits)",
        }
        for k, v in cats.items():
            if v:
                risks.append({"category": k, "files": v[:10], "files_total": len(v), "why": why[k]})
        rng["files_changed_in_range"] = len(names)
    if (root / ".gitmodules").exists():
        warnings.append("submodules present: `git bisect` does not move submodules; run `git submodule update --init` inside the predicate")

    # --- test
    test_info = _validate_test(test)
    if test_info.get("provided") and not test_info.get("valid"):
        blockers.append({"code": "invalid_test_command", "message": "; ".join(test_info["issues"])})
    warnings += test_info.get("warnings", [])

    ready = not blockers
    payload = {**base, "ready": ready,
               "refs": {"bad": {"input": bad, "sha": bad_sha}, "good": [{"input": g, "sha": s} for g, s in good_res]},
               "range": rng, "blockers": blockers, "warnings": warnings, "reproducibility_risks": risks,
               "isolation": {"recommended": True, "worktree_path": wt_path, "create": wt_cmd,
                             "why": "a separate worktree keeps your working tree, index and branch untouched while bisect checks out old commits",
                             "path_exists": Path(wt_path).exists()},
               "test": test_info, "predicate_guidance": PREDICATE_GUIDANCE}
    if Path(wt_path).exists():
        warnings.append(f"suggested worktree path already exists: {wt_path}; choose another path")
    if ready:
        start = f"git bisect start {bad_sha} {' '.join(good_shas)}"
        if test_info.get("provided"):
            run_cmd = f"git bisect run sh -c {shlex.quote(test)}" if test_info["echo"] == test else "git bisect run <your-predicate-script>"
        else:
            run_cmd = "git bisect run <path-to-predicate-script>   # absolute path, outside the work tree"
        payload["commands"] = [
            {"step": 1, "run": wt_cmd, "purpose": "create an isolated worktree at the bad commit"},
            {"step": 2, "run": f"cd {shlex.quote(wt_path)}", "purpose": "work inside the worktree"},
            {"step": 3, "run": start, "purpose": "start bisect (optionally add --first-parent when merges dominate)"},
            {"step": 4, "run": run_cmd, "purpose": "let git drive the search with your deterministic predicate (YOU run this; Git Warp does not)"},
            {"step": 5, "run": "git bisect log", "purpose": "review the decisions and the first bad commit"},
            {"step": 6, "run": "git bisect reset", "purpose": "leave bisect mode"},
            {"step": 7, "run": f"cd - && git worktree remove {shlex.quote(wt_path)}", "purpose": "remove the temporary worktree (the culprit commit stays in history)"},
        ]
        payload["manual_alternative"] = ["git bisect good|bad|skip   # mark the current commit yourself when no reliable predicate exists"]
    else:
        payload["commands"] = None
    return payload, 0


# --------------------------------------------------------------------------- status

def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def status(cwd) -> tuple:
    warnings: list = []
    state = repo_state(cwd)
    base = {"command": "bisect status", "state": state, "executed": False}
    try:
        gd = git.git_dir(cwd)
    except git.GitError as e:
        return {**base, "error": clip(str(e), 200)}, 2
    log_text = _read(gd / "BISECT_LOG")
    if log_text is None:
        return {**base, "in_progress": False, "message": "no bisect in progress in this worktree",
                "hint": "python3 scripts/warp.py bisect plan --good REF --bad REF", "warnings": warnings}, 0
    terms_text = _read(gd / "BISECT_TERMS")
    bad_term, good_term = "bad", "good"
    if terms_text:
        tl = terms_text.split()
        if len(tl) >= 2:
            bad_term, good_term = tl[0], tl[1]
    marks, first_bad, skipped_only = [], None, False
    for ln in log_text.splitlines():
        m = re.match(r"^git bisect (\S+) ([0-9a-f]{40})\s*$", ln)
        if m and m.group(1) not in ("start",):
            marks.append({"term": m.group(1), "sha": m.group(2)})
        f = re.match(r"^# first (\S+) commit: \[([0-9a-f]{40})\]", ln)
        if f:
            first_bad = f.group(2)
        if "only skipped commits left" in ln:
            skipped_only = True
    started = re.search(r"^git bisect start (.*)$", log_text, re.M)
    refs = git.run(["for-each-ref", "--format=%(refname) %(objectname)", "refs/bisect"], cwd=cwd)
    bad_ref, goods, skips = None, [], []
    for ln in refs.lines:
        name, _, sha = ln.partition(" ")
        short = name[len("refs/bisect/"):]
        if short == bad_term:
            bad_ref = sha
        elif short.startswith(good_term + "-"):
            goods.append(sha)
        elif short.startswith("skip-"):
            skips.append(sha)
    head = state["head"]
    out = {**base, "in_progress": True, "terms": {"bad": bad_term, "good": good_term},
           "marked": {"bad": bad_ref, "good": goods, "skipped": skips},
           "decisions_so_far": len(marks), "start_args": started.group(1) if started else None,
           "currently_testing": brief(git.commit_metadata("HEAD", cwd)) if head and git.commit_metadata("HEAD", cwd) else None,
           "original_branch": (_read(gd / "BISECT_START") or "").strip() or None,
           "warnings": warnings}
    if not bad_ref or not goods:
        out["phase"] = "waiting"
        out["message"] = "bisect has started but needs at least one good and one bad mark"
        return out, 0
    revs = [bad_ref] + ["^" + g for g in goods]
    cnt = git.run(["rev-list", "--count", *revs], cwd=cwd, timeout=60)
    n = int(cnt.text) if cnt.ok and cnt.text.isdigit() else None
    cands = git.log_commits(revs, limit=20, cwd=cwd)
    out["remaining"] = {"candidates": n, "estimated_steps_left": (math.ceil(math.log2(n)) if n and n > 1 else 0),
                        "sample_newest_first": [brief(c) for c in cands],
                        "equivalent_to": "git bisect visualize / git rev-list <bad> ^<good>..."}
    finished = first_bad is not None or n == 1
    if finished:
        culprit = first_bad or bad_ref
        meta = git.commit_metadata(culprit, cwd)
        files, total = commit_files(culprit, cwd)
        orig_bad = next((mk["sha"] for mk in marks if mk["term"] == bad_term), culprit)
        follow = []
        if files and orig_bad != culprit:
            follow = [brief(c) for c in git.log_commits(f"{culprit}..{orig_bad}", paths=files[:30], limit=15, cwd=cwd)]
        out["phase"] = "finished"
        out["culprit"] = {**brief(meta), "body": clip(meta.body, 600), "files": files[:30], "files_total": total} if meta else {"sha": culprit}
        out["follow_up_commits_touching_same_files"] = follow
        if skips:
            out["culprit_caveat"] = "some commits were skipped; the culprit may be one of the skipped commits adjacent to it"
        if skipped_only:
            out["culprit_caveat"] = "git reports only skipped commits are left: the culprit is one of several candidates"
        out["next_steps"] = [f"git show {culprit}   # inspect the introducing diff (read-only)",
                             "git bisect reset   # leave bisect mode when done",
                             "Decide fix-forward vs revert deliberately; Git Warp never reverts automatically."]
    else:
        out["phase"] = "searching"
        out["next_steps"] = ["test the current commit, then `git bisect good|bad|skip` (or let `git bisect run <script>` continue)"]
    return out, 0
