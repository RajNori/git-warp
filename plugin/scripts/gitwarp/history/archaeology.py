"""Archaeology: deterministic history evidence for a file, directory, symbol, regex or keyword question.

Every statement in the output is tagged ``fact`` (read directly from git data) or
``inference`` (a heuristic over facts).  ``unknown`` lists what git cannot say.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, OrderedDict
from typing import Optional

from ..core import git
from ._common import brief, clip, repo_state

_FMT = "%x1e%H%x1f%P%x1f%an%x1f%aI%x1f%s%x1f%b%x1f"
_RAW = re.compile(r"^:\d+ \d+ [0-9a-f]+ [0-9a-f]+ ([A-Z])(\d*)\t(.*)$")
_NUM = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$")
_REVERT_BODY = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
_REVERT_SUBJ = re.compile(r'^Revert "(.+)"$')
_FIX = re.compile(r"\b(fix(?:e[sd])?|bug(?:fix)?|hotfix|regression|crash(?:es)?|resolve[sd]?|repair(?:ed)?|patch(?:ed)?|correct(?:ed|s)?|workaround)\b", re.I)
_STOP = set("the a an and or of to in on for with why how when what did does do is are was were this that these those from by at as it its be been "
            "file code function change changed changes who where which there their they we you i me my our your not no yes can could should would "
            "has have had get got make made using use used happen happened exist exists".split())
MAX_WORTH = 7


def _parse_log(stdout: str) -> list:
    entries = []
    for rec in stdout.split("\x1e"):
        if not rec.strip():
            continue
        f = rec.split("\x1f", 6)
        if len(f) < 7:
            continue
        sha, parents, an, ad, subj, body, rest = f
        files, added, deleted = [], 0, 0
        for ln in rest.splitlines():
            m = _RAW.match(ln)
            if m:
                parts = m.group(3).split("\t")
                files.append({"status": m.group(1), "sim": int(m.group(2)) if m.group(2) else None,
                              "path": parts[-1], "old_path": parts[0] if len(parts) > 1 else None})
                continue
            n = _NUM.match(ln)
            if n:
                if n.group(1) != "-":
                    added += int(n.group(1))
                if n.group(2) != "-":
                    deleted += int(n.group(2))
        entries.append({"sha": sha.strip(), "parents": parents.split(), "author": an, "date": ad, "subject": subj,
                        "body": body.strip(), "files": files, "added": added, "deleted": deleted, "churn": added + deleted})
    return entries


def _log(cwd, selectors: list, pathspec: Optional[str], limit: int, since: Optional[str], follow: bool):
    args = ["--literal-pathspecs", "-c", "core.quotepath=false", "log", "--raw", "--numstat", "-M", "--no-color",
            f"-n{limit + 1}", f"--format={_FMT}"]
    if since:
        args.append(f"--since={since}")
    if follow:
        args.append("--follow")
    args += selectors + ["HEAD"]
    if pathspec:
        args += ["--", pathspec]
    return git.run(args, cwd=cwd, timeout=90)


def _keywords(question: str) -> list:
    words = [w.strip("._/-") for w in re.findall(r"[A-Za-z0-9_./-]{3,}", question)]
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw and lw not in _STOP and lw not in seen:
            seen.add(lw)
            out.append(w)
    return out[:6]


def _short(e: dict) -> dict:
    return {"sha": e["sha"], "short": e["sha"][:8], "date": e["date"], "author": e["author"], "subject": clip(e["subject"], 160)}


def _compact(e: dict, file_mode: bool) -> dict:
    d = {**_short(e), "merge": len(e["parents"]) > 1, "churn": e["churn"], "added": e["added"], "deleted": e["deleted"]}
    if file_mode and e["files"]:
        f = e["files"][0]
        d.update(status=f["status"], path=f["path"])
        if f["old_path"]:
            d["old_path"] = f["old_path"]
    else:
        d["files"] = [f["path"] for f in e["files"][:6]]
        d["files_total"] = len(e["files"])
    return d


def run(cwd, target: Optional[str] = None, symbol: Optional[str] = None, regex: Optional[str] = None,
        question: Optional[str] = None, limit: int = 200, since: Optional[str] = None) -> tuple:
    """Return ``(payload, exit_code)``."""
    warnings: list = []
    state = repo_state(cwd)
    target = target.rstrip("/") if target and target != "/" else target
    base = {"command": "archaeology", "state": state, "limit": limit, "since": since}
    if state["unborn"]:
        return {**base, "mode": "none", "found": False, "timeline": [], "statements": [], "commits_worth_reading": [],
                "unknown": ["The repository has no commits yet."], "warnings": ["repository has no commits yet (unborn branch): no history to examine"]}, 0
    if state["shallow"]:
        warnings.append("shallow clone: history stops at the shallow boundary, so the 'introduction' commit may only be where the clone was cut")

    # ---- choose mode
    selectors, follow, mode, kws = [], False, "file", []
    pathspec = target
    in_head = None
    if symbol:
        mode, selectors = "symbol", [f"-S{symbol}"]
    elif regex:
        mode, selectors = "regex", [f"-G{regex}"]
    elif question:
        kws = _keywords(question)
        if not kws:
            return {**base, "error": "no usable keywords in --question; give a symbol, path or more specific words"}, 2
        mode, selectors = "question", ["-i", "-F"] + [f"--grep={k}" for k in kws]
    elif not target:
        return {**base, "error": "give a path, or one of --symbol / --regex / --question"}, 2
    if mode == "file":
        ls = git.run(["--literal-pathspecs", "ls-tree", "-z", "HEAD", "--", target], cwd=cwd)
        in_head = None
        if ls.ok and ls.stdout.strip("\x00"):
            in_head = ls.stdout.split("\t", 1)[0].split()[1] if "\t" in ls.stdout else None
        if in_head == "tree":
            mode = "directory"
        elif in_head == "blob":
            follow = True
        else:
            follow = True   # not in HEAD: probably deleted or renamed away; --follow reaches back through history
            warnings.append(f"{target!r} does not exist at HEAD; searching history for a deleted/renamed path")

    # ---- run log
    try:
        r = _log(cwd, selectors, pathspec, limit, since, follow)
        if mode == "file" and in_head is None and r.ok and not r.stdout.strip():
            r = _log(cwd, selectors, pathspec, limit, since, False)   # deleted directory?
    except git.GitTimeout:
        return {**base, "mode": mode, "error": "git log timed out; narrow with --since or a smaller --limit"}, 2
    except (git.GitError, ValueError) as e:
        return {**base, "error": clip(str(e), 300)}, 2
    if not r.ok:
        return {**base, "mode": mode, "error": f"git log failed: {clip(r.stderr.strip(), 300)}"}, 2
    entries = _parse_log(r.stdout)
    truncated = len(entries) > limit
    entries = entries[:limit]
    if truncated:
        warnings.append(f"history truncated at {limit} commits (truncated: true); older commits, including the real introduction, were not examined")
    if not entries:
        sug = []
        if mode in ("file", "directory") and target:
            name = target.split("/")[-1]
            if name and not re.search(r"[*?\[\]]", name):
                s = git.run(["log", "--all", "--format=", "--name-only", "-n300", "--", f":(glob)**/{name}"], cwd=cwd, timeout=30)
                sug = sorted({x for x in s.lines})[:10] if s.ok else []
        return {**base, "mode": mode, "target": target, "found": False, "timeline": [], "statements": [],
                "similar_paths_in_history": sug, "commits_worth_reading": [],
                "unknown": ["No commit on HEAD's history matched. The path may be misspelled, never committed, or only exist on other branches."],
                "warnings": warnings + ["no history found"]}, 0

    file_mode = mode == "file"
    chrono = list(reversed(entries))
    by_sha = OrderedDict((e["sha"], e) for e in entries)
    statements, unknown = [], []

    def stmt(tag, text, commits=()):
        statements.append({"tag": tag, "text": text, "commits": [c[:8] for c in commits]})

    # ---- file-mode specifics
    renames, path_history, deleted_restored, currently_deleted = [], [], [], None
    intro = None
    if file_mode:
        cur_seg = None
        for e in chrono:
            f = e["files"][0] if e["files"] else None
            if not f:
                continue
            if f["status"] == "R" and f["old_path"]:
                renames.append({"tag": "fact", "commit": e["sha"][:8], "date": e["date"], "from": f["old_path"], "to": f["path"], "similarity_percent": f["sim"]})
            if cur_seg is None or cur_seg["path"] != f["path"]:
                if cur_seg:
                    cur_seg["until_commit"] = e["sha"][:8]
                cur_seg = {"tag": "fact", "path": f["path"], "from_commit": e["sha"][:8], "until_commit": None}
                path_history.append(cur_seg)
        pending_del = None
        for e in chrono:
            f = e["files"][0] if e["files"] else None
            if not f:
                continue
            if f["status"] == "D":
                pending_del = e
            elif f["status"] in ("A", "C") and pending_del is not None:
                deleted_restored.append({"tag": "fact", "deleted_in": pending_del["sha"][:8], "restored_in": e["sha"][:8],
                                         "deleted_subject": clip(pending_del["subject"], 100), "restored_subject": clip(e["subject"], 100)})
                pending_del = None
        newest = entries[0]["files"][0] if entries[0]["files"] else None
        if newest and newest["status"] == "D":
            currently_deleted = {"tag": "fact", "deleted_in": entries[0]["sha"][:8], "subject": clip(entries[0]["subject"], 100)}
    oldest = chrono[0]
    window_partial = truncated or bool(since)
    if window_partial:
        unknown.append("The introduction cannot be determined: the examined window is partial (--limit/--since). The oldest commit shown is only the oldest in the window.")
        intro = {"tag": "inference", "commit": _short(oldest), "note": "oldest commit in the examined window, not proven to be the introduction"}
    else:
        of = oldest["files"][0] if (file_mode and oldest["files"]) else None
        if file_mode:
            if of and of["status"] in ("A", "C"):
                intro = {"tag": "fact", "commit": _short(oldest), "note": "oldest commit in followed history; it adds the path"}
            else:
                intro = {"tag": "inference", "commit": _short(oldest), "note": "oldest reachable commit touching the path; it does not show an add (shallow clone or grafted history)"}
        elif mode == "directory":
            intro = {"tag": "fact", "commit": _short(oldest), "note": "oldest commit on HEAD's history touching the directory"}
        else:
            intro = {"tag": "inference", "commit": _short(oldest),
                     "note": "oldest commit whose diff matched; for -S/-G this is where the match first appeared, which usually (not always) means introduction"}
    stmt(intro["tag"], f"Oldest matching commit {oldest['sha'][:8]} ({oldest['date'][:10]}, {oldest['author']}): {clip(oldest['subject'], 100)} - {intro['note']}", [oldest["sha"]])

    # ---- reverts (both directions)
    reverts, reverted_by = [], {}
    shas = list(by_sha)
    for e in entries:
        m = _REVERT_BODY.search(e["body"])
        target_sha, via = None, None
        if m:
            cand = m.group(1)
            target_sha = next((s for s in shas if s.startswith(cand)), cand)
            via = "body"
        else:
            sm = _REVERT_SUBJ.match(e["subject"])
            if sm:
                orig = next((x for x in entries if x["subject"] == sm.group(1) and x["sha"] != e["sha"]), None)
                if orig:
                    target_sha, via = orig["sha"], "subject-match"
        if e["subject"].startswith("Revert") and (target_sha or m):
            in_hist = target_sha in by_sha
            reverts.append({"tag": "fact" if via == "body" else "inference", "revert": e["sha"], "revert_short": e["sha"][:8],
                            "reverted": target_sha, "reverted_short": (target_sha or "")[:8], "reverted_in_examined_history": in_hist,
                            "linked_via": via, "revert_subject": clip(e["subject"], 120),
                            "reverted_subject": clip(by_sha[target_sha]["subject"], 120) if in_hist else None})
            if target_sha:
                reverted_by.setdefault(target_sha, []).append(e["sha"])
    for rv in reverts:
        stmt(rv["tag"], f"{rv['revert_short']} reverts {rv['reverted_short']} (linked via {rv['linked_via']})", [rv["revert"], rv["reverted"] or ""])

    # ---- fixes / rewrites (inferences)
    revert_shas = {rv["revert"] for rv in reverts}
    fixes = [e for e in entries if e["sha"] not in revert_shas and _FIX.search(e["subject"])]
    fix_list = [{"tag": "inference", "basis": "commit message matches fix-like keywords", **_short(e), "churn": e["churn"]} for e in fixes[:15]]
    if fixes:
        stmt("inference", f"{len(fixes)} commit(s) have fix-like messages (message heuristic; says nothing about whether they actually fixed anything)", [e["sha"] for e in fixes[:5]])
    others = [e for e in entries if e is not (oldest if not window_partial else None)]
    churns = [e["churn"] for e in others if e["churn"] > 0]
    med = statistics.median(churns) if churns else 0
    thr = max(50, 2 * med)
    rewrites = sorted((e for e in others if e["churn"] >= thr), key=lambda e: -e["churn"])[:5]
    rewrite_list = [{"tag": "inference", "basis": f"churn (lines added+deleted) >= {int(thr)}: at least 50 and twice the median", **_short(e),
                     "churn": e["churn"], "added": e["added"], "deleted": e["deleted"]} for e in rewrites]
    for rw in rewrite_list[:2]:
        stmt("inference", f"{rw['short']} is a large change ({rw['added']} added / {rw['deleted']} deleted lines)", [rw["sha"]])

    # ---- merge points + first-parent (file / directory)
    first_parent, merge_points = [], []
    if mode in ("file", "directory"):
        fp = git.run(["--literal-pathspecs", "log", "--first-parent", f"-n{limit}", "--format=%H\x1f%P\x1f%aI\x1f%s"] +
                     (["--follow"] if follow else []) + (["--since=" + since] if since else []) + ["HEAD", "--", pathspec], cwd=cwd, timeout=60)
        for ln in (fp.lines if fp.ok else []):
            sha, par, dt, subj = (ln.split("\x1f") + ["", "", "", ""])[:4]
            is_merge = len(par.split()) > 1
            first_parent.append({"short": sha[:8], "date": dt, "subject": clip(subj, 100), "merge": is_merge})
            if is_merge:
                merge_points.append({"tag": "fact", "sha": sha, "short": sha[:8], "date": dt, "subject": clip(subj, 100),
                                     "note": "merge commit whose result differs from its first parent for this path (the branch work arrived here)"})
        first_parent = first_parent[:25]

    # ---- blame summary
    blame_summary = None
    survive = Counter()
    if file_mode and in_head == "blob":
        try:
            bl = git.blame(target, "HEAD", cwd=cwd)
        except (git.GitError, ValueError):
            bl = []
        if bl:
            survive = Counter(b["sha"] for b in bl)
            top = []
            for sha, n in survive.most_common(5):
                meta = next((b for b in bl if b["sha"] == sha), {})
                top.append({"tag": "fact", "short": sha[:8], "sha": sha, "surviving_lines": n, "author": meta.get("author"),
                            "summary": clip(meta.get("summary", ""), 100), "in_followed_history": sha in by_sha})
            blame_summary = {"total_lines": len(bl), "distinct_commits": len(survive), "top_commits": top}
            stmt("fact", f"{len(survive)} commit(s) account for the {len(bl)} lines currently in the file; the largest contributor is {top[0]['short']} with {top[0]['surviving_lines']} lines", [top[0]["sha"]])
        else:
            warnings.append("blame produced no output (binary file, or blame failed); surviving-line ranking skipped")

    # ---- authors
    authors = OrderedDict()
    for e in chrono:
        a = authors.setdefault(e["author"], {"author": e["author"], "commits": 0, "first": e["date"], "last": e["date"]})
        a["commits"] += 1
        a["last"] = e["date"]
    author_list = [{"tag": "fact", **a} for a in authors.values()]
    stmt("fact", "Contribution history only (not ownership): " + ", ".join(f"{a['author']} ({a['commits']})" for a in list(authors.values())[:5]))

    # ---- symbol presence
    present = None
    if mode in ("symbol",):
        g = git.run(["--literal-pathspecs", "grep", "-I", "-l", "-F", "-e", symbol, "HEAD"] + (["--", target] if target else []), cwd=cwd, timeout=30)
        files = [ln.split(":", 1)[-1] for ln in g.lines] if g.ok else []
        present = {"tag": "fact", "in_head": bool(files), "files": files[:10]}
        stmt("fact", f"The string {clip(symbol, 60)!r} {'is' if files else 'is NOT'} present at HEAD" + (f" in {len(files)} file(s)" if files else ""))
    if mode == "question":
        stmt("inference", f"Matched commit messages containing any of the keywords {kws}; message wording only, not code analysis")

    # ---- commits worth reading
    worth = OrderedDict()

    def add(sha, reason, tag):
        if sha not in worth and len(worth) >= MAX_WORTH:
            return
        e = by_sha.get(sha)
        if not e:
            return
        w = worth.setdefault(sha, {**_short(e), "reasons": []})
        if all(x["reason"] != reason for x in w["reasons"]):
            w["reasons"].append({"tag": tag, "reason": reason})

    add(oldest["sha"], "introduction" if intro["tag"] == "fact" else "oldest commit in examined window (introduction not proven)", intro["tag"])
    if rewrite_list:
        add(rewrite_list[0]["sha"], f"biggest rewrite ({rewrite_list[0]['churn']} lines changed)", "inference")
    for rv in reverts[:2]:
        add(rv["revert"], f"reverts {rv['reverted_short']}", rv["tag"])
        if rv["reverted"]:
            add(rv["reverted"], f"was reverted by {rv['revert_short']}", rv["tag"])
    ranked_fix = sorted(fixes, key=lambda e: (-survive.get(e["sha"], 0), -e["churn"]))
    for e in ranked_fix[:2]:
        n = survive.get(e["sha"], 0)
        add(e["sha"], "fix-like message" + (f"; {n} of its lines still survive in the file" if n else " (touches this history)"), "inference")
    if len(worth) < MAX_WORTH:
        add(entries[0]["sha"], "most recent commit touching this target", "fact")

    unknown += [
        "Why each change was made (motive/intent): git stores messages, not reasons; any motive is an inference from the message text.",
        "Whether commit messages are accurate or complete.",
        "History that was squashed, rebased or force-pushed away is not visible unless it is still in the reflog.",
        "Work on branches not merged into HEAD (only HEAD's history was searched).",
    ]
    if file_mode and not renames:
        unknown.append("Renames below git's similarity threshold (-M default 50%) would appear as a delete plus an add, not as a rename.")
    if mode in ("symbol", "regex"):
        unknown.append("-S/-G match diff text; moves between files or textual renames of the symbol are not tracked as the same entity.")

    out = {**base, "mode": mode, "target": target, "found": True, "truncated": truncated,
           "examined_commits": len(entries),
           "query": {"symbol": symbol, "regex": regex, "question": question, "keywords": kws or None},
           "currently_in_head": (in_head if mode in ("file", "directory") else None),
           "introduction": intro,
           "path_history": path_history if file_mode else None,
           "renames": renames if file_mode else None,
           "deleted_then_restored": deleted_restored if file_mode else None,
           "currently_deleted": currently_deleted,
           "reverts": reverts,
           "fix_like_commits": fix_list,
           "large_rewrites": rewrite_list,
           "merge_points": merge_points,
           "first_parent_history": first_parent,
           "blame_summary": blame_summary,
           "symbol_presence": present,
           "authorship_timeline": author_list,
           "timeline": [_compact(e, file_mode) for e in entries],
           "statements": statements,
           "commits_worth_reading": list(worth.values()),
           "unknown": unknown,
           "warnings": warnings}
    return out, 0
