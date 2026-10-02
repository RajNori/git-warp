"""Recovery evidence: reflogs, dangling commits, deleted branches, dropped stashes.

Everything here is read-only except :func:`preserve`, the single mutator in the
product, which only *creates* a new branch ref and never overwrites one.

Recovery principle: Preserve first. Investigate second. Mutate last.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

from ..core import git
from ._common import (brief, clip, commit_files, is_ancestor, is_hexish, refs_containing, repo_state)

REFLOG_HEAD_LIMIT = 400
REFLOG_REF_LIMIT = 100
MAX_REFS = 150
MAX_CANDIDATES = 120
CHAIN_LIMIT = 200
FILES_SHOWN = 20

STRONG_KINDS = {
    "reset-abandoned", "amend-original", "rebase-original", "deleted-branch-tip",
    "detached-head-work", "force-push-overwritten", "ref-rewound",
}
_STASH_PREFIXES = ("WIP on ", "On ", "index on ", "untracked files on ")
_RANK = {"high": 3, "medium": 2, "low": 1}
NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_NOT_REFS = ["--not", "--branches", "--tags", "--remotes", "--glob=refs/stash"]

PRINCIPLE = "Preserve first. Investigate second. Mutate last."


# --------------------------------------------------------------------------- helpers

def _iso(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")


def _parse_ts(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except (ValueError, TypeError):
        return 0


def _read_reflog(ref: str, limit: int, cwd) -> list:
    """Entries newest first: ``{ref, sha, message, ts}`` (ts = when the reflog entry was written)."""
    try:
        r = git.run(["reflog", "show", "--date=unix", f"-n{int(limit)}", "--format=%H\x1f%gs\x1f%gd", git.check_ref(ref)],
                    cwd=cwd, timeout=30)
    except (git.GitError, ValueError):
        return []
    out = []
    if not r.ok:
        return out
    for ln in r.lines:
        sha, msg, sel = (ln.split("\x1f") + ["", "", ""])[:3]
        m = re.search(r"@\{(\d+)\}$", sel)
        out.append({"ref": ref, "sha": sha, "message": msg, "ts": int(m.group(1)) if m else 0})
    return out


def _existing_refs(cwd) -> dict:
    r = git.run(["for-each-ref", "--format=%(refname)"], cwd=cwd, timeout=30)
    refs = r.lines if r.ok else []
    return {
        "heads": {x[len("refs/heads/"):] for x in refs if x.startswith("refs/heads/")},
        "tags": {x[len("refs/tags/"):] for x in refs if x.startswith("refs/tags/")},
        "remotes": {x[len("refs/remotes/"):] for x in refs if x.startswith("refs/remotes/")},
        "all": refs,
    }


def _commit_types(shas: list, cwd) -> dict:
    """sha -> 'commit' | other type | 'missing'."""
    if not shas:
        return {}
    r = git.run(["cat-file", "--batch-check"], cwd=cwd, input="\n".join(shas) + "\n", timeout=30)
    out = {}
    for ln in r.lines:
        parts = ln.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def _chain(sha: str, cwd) -> list:
    """Commits reachable from ``sha`` but from no branch/tag/remote/stash (what would be lost)."""
    r = git.run(["rev-list", f"--max-count={CHAIN_LIMIT}", sha, *_NOT_REFS], cwd=cwd, timeout=30)
    return r.lines if r.ok else []


def _chain_matches(sha: str, extra: list, pathspec: Optional[str], cwd) -> bool:
    args = ["rev-list", "-n1", *extra, sha, *_NOT_REFS]
    if pathspec:
        args += ["--", pathspec]
    r = git.run(args, cwd=cwd, timeout=30)
    return bool(r.ok and r.text)


class _Registry:
    def __init__(self):
        self.items = {}

    def add(self, sha: str, kind: str, evidence: str, ts: int = 0, **extra):
        it = self.items.setdefault(sha, {"sha": sha, "kinds": [], "evidence": [], "last_seen": 0, "extra": {}})
        if kind not in it["kinds"]:
            it["kinds"].append(kind)
        if evidence not in it["evidence"] and len(it["evidence"]) < 8:
            it["evidence"].append(evidence)
        it["last_seen"] = max(it["last_seen"], ts or 0)
        it["extra"].update(extra)


# --------------------------------------------------------------------------- scan

def _analyse_head_reflog(H: list, refs: dict, reg: _Registry, signals: list) -> None:
    for i, e in enumerate(H):
        m, older = e["message"], (H[i + 1] if i + 1 < len(H) else None)
        tag = f"HEAD@{{{i}}}"
        if m.startswith("reset: moving to") and older and older["sha"] != e["sha"]:
            reg.add(older["sha"], "reset-abandoned",
                    f"{tag} '{m}': HEAD moved from {older['sha'][:8]} to {e['sha'][:8]}, leaving {older['sha'][:8]} behind", e["ts"])
            signals.append({"kind": "reset", "message": m, "from": older["sha"], "to": e["sha"], "when": _iso(e["ts"]), "reflog": tag})
        elif m.startswith("commit (amend)") and older and older["sha"] != e["sha"]:
            reg.add(older["sha"], "amend-original", f"{tag} '{m}': replaced {older['sha'][:8]} with {e['sha'][:8]}", e["ts"])
            signals.append({"kind": "amend", "message": m, "from": older["sha"], "to": e["sha"], "when": _iso(e["ts"]), "reflog": tag})
        elif "rebase" in m and "(start)" in m and older:
            reg.add(older["sha"], "rebase-original", f"{tag} '{m}': HEAD was {older['sha'][:8]} before the rebase started", e["ts"])
            signals.append({"kind": "rebase-start", "message": m, "from": older["sha"], "to": e["sha"], "when": _iso(e["ts"]), "reflog": tag})
        elif m.startswith("rebase") and "(finish)" in m:
            signals.append({"kind": "rebase-finish", "message": m, "from": None, "to": e["sha"], "when": _iso(e["ts"]), "reflog": tag})
        elif m.startswith("checkout: moving from ") and older:
            mm = re.match(r"^checkout: moving from (.+) to (.+)$", m)
            if not mm:
                continue
            src = mm.group(1)
            if is_hexish(src) and older["sha"].startswith(src.lower()) and src not in refs["heads"]:
                reg.add(older["sha"], "detached-head-work",
                        f"{tag} '{m}': left detached HEAD at {older['sha'][:8]}", e["ts"])
                signals.append({"kind": "left-detached-head", "message": m, "from": older["sha"], "to": e["sha"], "when": _iso(e["ts"]), "reflog": tag})
            elif src not in refs["heads"] and src not in refs["tags"] and src not in refs["remotes"] and not is_hexish(src):
                reg.add(older["sha"], "deleted-branch-tip",
                        f"{tag} '{m}': branch '{src}' was checked out at {older['sha'][:8]} but no branch named '{src}' exists now",
                        e["ts"], deleted_branch=src)
    for i, e in enumerate(H):
        reg.add(e["sha"], "reflog-only", f"HEAD@{{{i}}}: {e['message']}", e["ts"])


def _ref_log_transitions(ref: str, cwd, logs_dir) -> list:
    """Newest-first ``{old, new, message, ts}`` for a ref.

    Prefers the raw reflog file (it records the *old* value of the oldest entry, which
    ``git reflog show`` cannot print); falls back to pairing ``git reflog show`` entries.
    """
    zero = "0" * 40
    out = []
    if logs_dir is not None:
        f = logs_dir / ref
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-REFLOG_REF_LIMIT:]
        except OSError:
            lines = None
        if lines is not None:
            for ln in reversed(lines):
                head, _, msg = ln.partition("\t")
                parts = head.split()
                if len(parts) < 3 or len(parts[0]) != 40 or len(parts[1]) != 40:
                    continue
                m = re.search(r"(\d{9,})\s+[+-]\d{4}$", head)
                out.append({"old": None if parts[0] == zero else parts[0], "new": parts[1], "message": msg.strip(), "ts": int(m.group(1)) if m else 0})
            return out
    log = _read_reflog(ref, REFLOG_REF_LIMIT, cwd)
    for i, e in enumerate(log):
        older = log[i + 1] if i + 1 < len(log) else None
        out.append({"old": older["sha"] if older else None, "new": e["sha"], "message": e["message"], "ts": e["ts"]})
    return out


def _analyse_ref_reflogs(cwd, refs: dict, reg: _Registry, warnings: list) -> int:
    names = [r for r in refs["all"] if r.startswith(("refs/heads/", "refs/remotes/"))]
    if len(names) > MAX_REFS:
        warnings.append(f"{len(names)} refs: only the first {MAX_REFS} reflogs were examined")
        names = names[:MAX_REFS]
    try:
        logs_dir = git.common_dir(cwd) / "logs"
    except git.GitError:
        logs_dir = None
    n = 0
    for ref in names:
        n += 1
        for t in _ref_log_transitions(ref, cwd, logs_dir):
            old, new, msg = t["old"], t["new"], t["message"]
            if not old or old == new:
                continue
            forced = "forced-update" in msg or "forced update" in msg
            if forced or is_ancestor(old, new, cwd) is False:
                if msg.startswith("commit (amend)"):
                    kind = "amend-original"
                else:
                    kind = "force-push-overwritten" if (forced or ref.startswith("refs/remotes/")) else "ref-rewound"
                reg.add(old, kind, f"reflog of {ref}: '{msg}' moved it from {old[:8]} to {new[:8]} (not a fast-forward)", t["ts"])
    return n


def _fsck(cwd, timeout: float, warnings: list) -> dict:
    out = {"ran": True, "commits": [], "blobs": [], "trees": [], "timed_out": False}
    try:
        r = git.run(["fsck", "--connectivity-only", "--no-progress", "--dangling", "--no-reflogs"], cwd=cwd, timeout=timeout)
    except git.GitTimeout:
        out["timed_out"] = True
        warnings.append(f"git fsck timed out after {timeout:g}s: dangling-object evidence is incomplete "
                        "(re-run with a larger --fsck-timeout, or use reflog evidence only)")
        return out
    except git.GitError as e:
        out["ran"] = False
        warnings.append(f"git fsck could not run: {clip(str(e), 200)}")
        return out
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) == 3 and parts[0] == "dangling" and parts[1] in ("commit", "blob", "tree"):
            out[parts[1] + "s"].append(parts[2])
    if r.returncode != 0 and not out["commits"]:
        warnings.append(f"git fsck exited {r.returncode}: {clip(r.stderr.strip() or r.stdout.strip(), 200)}")
    return out


def _confidence(it: dict, meta, containing: list, head_reachable: bool, in_reflog: bool, orig_head: Optional[str]) -> tuple:
    kinds, reasons = set(it["kinds"]), []
    exists = f"commit object {it['sha'][:8]} exists and was read successfully"
    if containing:
        return "low", [exists, f"already reachable from {', '.join(containing[:3])}: not lost, shown only because --include-reachable was used"]
    unreachable = "not reachable from any branch, tag, remote-tracking ref or stash"
    if head_reachable:
        return "medium", [exists, unreachable,
                          "currently reachable only through the checked-out detached HEAD; it will lose its last reference if you switch away"]
    reasons += [exists, unreachable]
    sk = kinds & STRONG_KINDS
    if "dropped-stash" in kinds:
        reasons.append("dangling commit whose subject and parent layout match what `git stash` creates")
        reasons.append("may already have been applied via `git stash pop`; compare with the working tree before assuming it is lost")
        return "high", reasons
    if sk:
        k = sorted(sk)[0]
        if sk == {"amend-original"}:
            reasons.append("an amend replaced this commit; usually intentional, so rated medium")
            return "medium", reasons
        reasons.append(f"reflog shows a ref/HEAD explicitly moved away from it ({', '.join(sorted(sk))})")
        if "rebase-original" in sk and orig_head != it["sha"] and not (sk - {"rebase-original"}):
            reasons.append("ORIG_HEAD does not point here, so the rebase-start record is the only corroboration")
            return "medium", reasons
        if orig_head == it["sha"]:
            reasons.append("corroborated by ORIG_HEAD")
        return "high", reasons
    if "orig-head" in kinds:
        reasons.append("ORIG_HEAD points here (saved by reset/rebase/merge/pull), but the reflog shows no explicit abandon marker")
        return "medium", reasons
    if in_reflog:
        reasons.append("recorded in a reflog (so not yet expired), but nothing shows it was deliberately abandoned")
        return "medium", reasons
    reasons.append("only evidence is that git fsck reports it dangling; no reflog entry explains it (may be old garbage from an amend, a failed rebase or a deleted branch)")
    return "low", reasons


def scan(cwd, since: Optional[str] = None, grep: Optional[str] = None, path: Optional[str] = None,
         limit: int = 25, run_fsck: bool = True, fsck_timeout: float = 45.0, include_reachable: bool = False) -> dict:
    warnings: list = []
    state = repo_state(cwd)
    refs = _existing_refs(cwd)
    reg = _Registry()
    signals: list = []
    orig_head = git.rev_parse("ORIG_HEAD", cwd)
    state["orig_head"] = orig_head
    if state["shallow"]:
        warnings.append("shallow clone: commits before the shallow boundary are not present, so recovery evidence for older work is unavailable")
    if state["unborn"]:
        warnings.append("repository has no commits yet (unborn branch): there is no HEAD reflog; only dangling objects can be reported")
    if state["operation"]:
        warnings.append(f"a {state['operation']} is in progress; finish or abort it deliberately before judging what is lost")

    # --- reflogs
    H = _read_reflog("HEAD", REFLOG_HEAD_LIMIT, cwd)
    _analyse_head_reflog(H, refs, reg, signals)
    nref = _analyse_ref_reflogs(cwd, refs, reg, warnings)
    in_reflog = {e["sha"] for e in H}
    if orig_head:
        reg.add(orig_head, "orig-head", "ORIG_HEAD points here (saved by reset/rebase/merge/pull before they moved HEAD)")

    # --- fsck
    fs = {"ran": False, "commits": [], "blobs": [], "trees": [], "timed_out": False}
    if run_fsck:
        fs = _fsck(cwd, fsck_timeout, warnings)
    else:
        warnings.append("fsck was skipped (--no-fsck): dropped stashes and unreferenced commits will not be found")
    for sha in fs["commits"]:
        txt = "git fsck: dangling commit (unreachable from refs"
        txt += "; also present in a reflog)" if sha in in_reflog else " and in no reflog)"
        reg.add(sha, "dangling", txt)

    # --- validate objects, load metadata
    shas = list(reg.items)
    types = _commit_types(shas, cwd)
    missing = [s for s in shas if types.get(s) != "commit"]
    if missing:
        warnings.append(f"{len(missing)} reflog/ORIG_HEAD entries point at objects that no longer exist or are not commits (already pruned); ignored")
    shas = [s for s in shas if types.get(s) == "commit"]
    metas = {}
    for i in range(0, len(shas), 100):
        for c in git.log_commits(shas[i:i + 100], limit=100, extra=["--no-walk=unsorted"], cwd=cwd):
            metas[c.sha] = c

    # stash-shaped dangling commits
    for sha in fs["commits"]:
        c = metas.get(sha)
        if c and c.subject.startswith(_STASH_PREFIXES):
            need = 1 if c.subject.startswith(("index on ", "untracked files on ")) else 2
            if len(c.parents) >= need:
                reg.add(sha, "dropped-stash",
                        f"subject '{clip(c.subject, 80)}' and {len(c.parents)} parents match the commit shape produced by `git stash`; no ref or stash entry refers to it")

    # --- reachability + confidence
    head = state["head"]
    cands = []
    ordered = sorted((s for s in shas if s in metas), key=lambda s: (-(reg.items[s]["last_seen"] or _parse_ts(metas[s].commit_date))))
    if len(ordered) > MAX_CANDIDATES * 3:
        warnings.append(f"{len(ordered)} candidate commits; only the {MAX_CANDIDATES * 3} most recent were checked")
        ordered = ordered[:MAX_CANDIDATES * 3]
    for sha in ordered:
        it = reg.items[sha]
        containing = refs_containing(sha, cwd)
        head_reach = bool(head) and not containing and is_ancestor(sha, "HEAD", cwd) is True
        if containing and not include_reachable:
            continue
        # a purely weak ("reflog-only") candidate that HEAD itself reaches is just normal history
        if head_reach and set(it["kinds"]) <= {"reflog-only", "orig-head"} and not state["detached"]:
            continue
        conf, reasons = _confidence(it, metas[sha], containing, head_reach, sha in in_reflog, orig_head)
        cands.append({"it": it, "meta": metas[sha], "containing": containing, "head_reach": head_reach, "conf": conf, "reasons": reasons})
        if len(cands) >= MAX_CANDIDATES:
            warnings.append(f"more than {MAX_CANDIDATES} candidates; list truncated to the most recent")
            break

    deleted = [{"branch": c["it"]["extra"]["deleted_branch"], "tip": c["it"]["sha"], "short": c["it"]["sha"][:8],
                "subject": clip(c["meta"].subject, 120)}
               for c in cands if "deleted_branch" in c["it"]["extra"]]

    # --- collapse chains (an abandoned tip covers its unreachable ancestors)
    chains = {c["it"]["sha"]: (_chain(c["it"]["sha"], cwd) if not c["containing"] else [c["it"]["sha"]]) for c in cands}
    keep = []
    for c in cands:
        s = c["it"]["sha"]
        covered = any(o is not c and s in chains[o["it"]["sha"]] and s != o["it"]["sha"] and _RANK[o["conf"]] >= _RANK[c["conf"]]
                      for o in cands)
        if not covered:
            keep.append(c)

    # --- filters
    cutoff = None
    if since:
        try:
            rp = git.run(["rev-parse", f"--since={since}"], cwd=cwd)
            m = re.search(r"--max-age=(\d+)", rp.stdout)
            cutoff = int(m.group(1)) if m else None
        except (git.GitError, ValueError):
            cutoff = None
        if cutoff is None:
            warnings.append(f"could not parse --since {since!r}; filter ignored")
    final = []
    for c in keep:
        s, meta = c["it"]["sha"], c["meta"]
        files, total = commit_files(s, cwd)
        c["files"], c["files_total"] = files, total
        if cutoff is not None and max(c["it"]["last_seen"], _parse_ts(meta.commit_date)) < cutoff:
            continue
        if grep:
            low = grep.lower()
            hit = low in (meta.subject + "\n" + meta.body).lower()
            if not hit and not c["containing"]:
                hit = _chain_matches(s, ["-i", "-F", f"--grep={grep}"], None, cwd)
            if not hit:
                continue
        if path:
            p = path.rstrip("/")
            hit = any(f == p or f.startswith(p + "/") for f in files)
            if not hit and not c["containing"]:
                hit = _chain_matches(s, [], path, cwd)
            if not hit:
                continue
        final.append(c)
    final.sort(key=lambda c: (-_RANK[c["conf"]], -(c["it"]["last_seen"] or _parse_ts(c["meta"].commit_date))))
    truncated = len(final) > limit
    final = final[:limit]

    today = datetime.now().strftime("%Y-%m-%d")
    out_c = []
    for c in final:
        s, meta, it = c["it"]["sha"], c["meta"], c["it"]
        chain = chains.get(s, [s])
        incl = []
        if len(chain) > 1:
            for cs in chain[1:11]:
                cm = metas.get(cs) or git.commit_metadata(cs, cwd)
                if cm:
                    incl.append({"short": cm.short, "subject": clip(cm.subject, 100)})
        reach = list(c["containing"])
        if c["head_reach"]:
            reach.append("HEAD (detached)" if state["detached"] else "HEAD")
        out_c.append({
            "sha": s, "short": s[:8],
            "timestamp": meta.commit_date,
            "last_seen_in_reflog": _iso(it["last_seen"]),
            "author": meta.author_name,
            "subject": clip(meta.subject, 200),
            "parents": list(meta.parents),
            "files": c["files"][:FILES_SHOWN], "files_total": c["files_total"],
            "kinds": it["kinds"],
            "why_candidate": it["evidence"],
            "reachable_from": reach,
            "confidence": c["conf"],
            "confidence_reasons": c["reasons"],
            "unreachable_commit_count": len(chain) if not c["containing"] else 0,
            "also_unreachable": incl,
            "safe_action": f"git branch rescue/{today}-{s[:8]} {s}",
            "safe_action_via_warp": f"python3 scripts/warp.py rescue preserve {s}",
            **({"deleted_branch": it["extra"]["deleted_branch"]} if "deleted_branch" in it["extra"] else {}),
        })

    blob_info = []
    if fs["blobs"]:
        types2 = git.run(["cat-file", "--batch-check"], cwd=cwd, input="\n".join(fs["blobs"][:10]) + "\n")
        for ln in types2.lines:
            p = ln.split()
            if len(p) == 3 and p[2].isdigit():
                blob_info.append({"sha": p[0], "size_bytes": int(p[2])})

    summary = {k: sum(1 for c in out_c if k in c["kinds"]) for k in
               ("reset-abandoned", "amend-original", "rebase-original", "deleted-branch-tip", "detached-head-work", "dropped-stash", "force-push-overwritten")}
    return {
        "command": "rescue scan",
        "principle": PRINCIPLE,
        "state": state,
        "filters": {"since": since, "grep": grep, "path": path, "include_reachable": include_reachable},
        "candidates": out_c,
        "candidate_summary": {k: v for k, v in summary.items() if v},
        "deleted_branch_candidates": deleted,
        "reflog_signals": signals[:25],
        "stashes": [{"ref": s["ref"], "short": s["sha"][:8], "date": s["date"], "subject": clip(s["subject"], 120)} for s in git.stashes(cwd)],
        "worktrees": [{"path": w.get("path"), "head": (w.get("head") or "")[:8], "branch": w.get("branch"), "detached": bool(w.get("detached")),
                       "prunable": bool(w.get("prunable"))} for w in git.worktrees(cwd)],
        "fsck": {"ran": fs["ran"], "timed_out": fs["timed_out"]},
        "dangling_objects": {"commits": len(fs["commits"]), "trees": len(fs["trees"]), "blobs": len(fs["blobs"]), "blob_samples": blob_info,
                             "note": "Dangling blobs can be staged-but-never-committed file contents; inspect with `git cat-file -p <sha>` (read-only)."},
        "refs_examined": nref,
        "truncated": truncated,
        "unknown": [
            "Edits that were never committed or staged are not stored by git and cannot be recovered from it.",
            "Reflog entries and unreferenced objects can be removed by gc/prune; absence of a candidate does not prove the work never existed.",
            "Evidence shows where commits went, not whether the user meant to discard them.",
        ],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- inspect

def inspect(cwd, rev: str) -> dict:
    warnings: list = []
    try:
        sha = git.rev_parse(rev, cwd)
    except ValueError:
        return {"error": f"unsafe revision: {rev!r}"}
    if not sha:
        return {"error": f"{rev!r} does not resolve to a commit in this repository (it may be a blob/tree, abbreviated ambiguously, or already pruned)"}
    meta = git.commit_metadata(sha, cwd)
    state = repo_state(cwd)
    files, total = commit_files(sha, cwd)
    containing = refs_containing(sha, cwd, max_names=20)
    head = state["head"]
    rel = {"head": head}
    if head:
        rel["is_ancestor_of_head"] = is_ancestor(sha, head, cwd)
        rel["head_is_ancestor_of_commit"] = is_ancestor(head, sha, cwd)
        mb = git.merge_base(sha, head, cwd)
        rel["merge_base"] = mb
        ab = git.run(["rev-list", "--left-right", "--count", f"HEAD...{sha}"], cwd=cwd)
        if ab.ok and len(ab.text.split()) == 2:
            rel["commits_only_in_head"], rel["commits_only_in_candidate"] = (int(x) for x in ab.text.split())
        only = git.log_commits(f"HEAD..{sha}", limit=10, cwd=cwd)
        rel["candidate_only_commits"] = [{"short": c.short, "subject": clip(c.subject, 100)} for c in only]
        if mb is None:
            warnings.append("no common ancestor with HEAD (unrelated history, or shallow boundary)")
    else:
        warnings.append("HEAD is unborn: no ancestry comparison possible")
    in_reflog = [e for e in _read_reflog("HEAD", REFLOG_HEAD_LIMIT, cwd) if e["sha"] == sha]
    return {
        "command": "rescue inspect",
        "commit": {**brief(meta), "body": clip(meta.body, 600), "parents": list(meta.parents), "author_date": meta.author_date},
        "files": files[:100], "files_total": total,
        "stat": clip(git.show_commit(sha, cwd, stat=True, patch=False, max_bytes=20000), 6000),
        "reachable_from": containing,
        "unreachable": not containing,
        "ancestry_vs_head": rel,
        "head_reflog_mentions": [{"message": clip(e["message"], 100), "when": _iso(e["ts"])} for e in in_reflog[:5]],
        "safe_action": f"git branch rescue/{datetime.now().strftime('%Y-%m-%d')}-{sha[:8]} {sha}",
        "state": state,
        "warnings": warnings,
        "principle": PRINCIPLE,
    }


# --------------------------------------------------------------------------- preserve (the ONLY mutator)

def validate_branch_name(name: str) -> Optional[str]:
    """Return an error message, or None when ``name`` is acceptable."""
    if not name:
        return "name is empty"
    if name.startswith("-"):
        return "name must not start with '-'"
    if not NAME_RE.match(name):
        return "name may only contain letters, digits, '.', '_', '/' and '-'"
    if ".." in name or "//" in name:
        return "name must not contain '..' or '//'"
    if name.startswith("/") or name.endswith("/") or name.endswith(".") or name.endswith(".lock"):
        return "name must not start/end with '/', end with '.', or end with '.lock'"
    if any(part.startswith(".") or part.endswith(".lock") or part == "" for part in name.split("/")):
        return "no path component may start with '.' or end with '.lock'"
    if name in ("HEAD",) or name.upper() == "HEAD":
        return "name must not be HEAD"
    return None


def preserve(cwd, rev: str, name: Optional[str] = None, dry_run: bool = False) -> tuple:
    """Create ``refs/heads/<name>`` pointing at commit ``rev``. Returns ``(payload, exit_code)``."""
    base = {"command": "rescue preserve", "dry_run": dry_run, "principle": PRINCIPLE}
    try:
        sha = git.rev_parse(rev, cwd)
    except ValueError:
        return {**base, "error": f"unsafe revision: {rev!r}"}, 2
    if not sha:
        return {**base, "error": f"{rev!r} is not a commit in this repository; refusing to create a branch (objects that are blobs/trees, unknown or ambiguous ids are rejected)"}, 2
    if name is None:
        name = f"rescue/{datetime.now().strftime('%Y-%m-%d')}-{sha[:8]}"
    bad = validate_branch_name(name)
    if bad:
        return {**base, "error": f"invalid branch name {name!r}: {bad}"}, 2
    chk = git.run(["check-ref-format", f"refs/heads/{name}"], cwd=cwd)
    if not chk.ok:
        return {**base, "error": f"git rejects {name!r} as a branch name"}, 2
    full = f"refs/heads/{name}"
    existing = git.run(["rev-parse", "--verify", "--quiet", full], cwd=cwd)
    cmd = f"git branch {name} {sha}"
    verify = [f"git rev-parse {full}", f"git log --oneline -5 {name}", "git status --short   # unchanged: preserve never touches the working tree"]
    head_before = git.head_sha(cwd)
    if existing.ok and existing.text:
        if existing.text == sha:
            return {**base, "status": "already_preserved", "branch": name, "sha": sha, "changed": False,
                    "message": f"{name} already points at {sha[:8]}; nothing to do", "verify": verify}, 0
        return {**base, "error": f"branch {name!r} already exists at {existing.text[:8]}; refusing to overwrite or move it (choose another --name)",
                "existing": existing.text}, 2
    # D/F conflicts (e.g. branch 'rescue' exists and we want 'rescue/x') are caught by git itself below.
    if dry_run:
        return {**base, "status": "dry_run", "would_run": cmd, "branch": name, "sha": sha, "changed": False,
                "would_not_touch": ["HEAD", "index", "working tree", "any existing ref"], "verify": verify}, 0
    r = git.run(["branch", name, sha], cwd=cwd)
    if not r.ok:
        return {**base, "error": f"git branch failed: {clip(r.stderr.strip(), 300)}", "command": cmd}, 2
    created = git.run(["rev-parse", "--verify", "--quiet", full], cwd=cwd).text
    ok = created == sha
    return {**base, "status": "created" if ok else "created_unverified", "ran": cmd, "branch": name, "sha": sha, "changed": True,
            "verified": ok, "head_unchanged": git.head_sha(cwd) == head_before, "verify": verify,
            "next": [f"git show --stat {name}", f"git switch {name}   # only when you are ready; this changes your working tree",
                     f"git cherry-pick {sha[:8]}   # or copy individual commits onto your current branch"]}, 0
