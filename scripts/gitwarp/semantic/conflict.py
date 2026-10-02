"""``warp.py conflict``: read-only analysis of unmerged paths during merge / rebase / cherry-pick / revert.

Never edits files, never stages, never checks out a side.  ``relationship_hint`` values are deterministic
HINTS only; the judgement (compatible / contradictory / independent / unclear) belongs to Claude.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..core import git
from .common import repo_state, state_warnings

MAX_REGIONS = 25
MAX_TEXT_LINES = 60
MAX_BLAME_RANGE = 60
HINT_NOTE = "deterministic hint only; Claude must judge compatible / contradictory / independent / unclear by reading both sides"

_OPS = {
    "merge": ("MERGE_HEAD", "HEAD (the branch you are on)", "MERGE_HEAD (the branch being merged in)"),
    "cherry-pick": ("CHERRY_PICK_HEAD", "HEAD (the branch you are on)", "CHERRY_PICK_HEAD (the commit being cherry-picked)"),
    "revert": ("REVERT_HEAD", "HEAD (the branch you are on)", "REVERT_HEAD (the commit being reverted; its changes are being undone)"),
    "rebase": ("REBASE_HEAD", "HEAD (the upstream/onto branch plus commits already replayed)", "REBASE_HEAD (YOUR commit currently being replayed)"),
}
_MARK = re.compile(r"^(<{7,}|\|{7,}|={7,}|>{7,})(?:\s(.*))?$")


def parse_markers(text: str) -> tuple:
    """Parse conflict regions from file text.  Returns ``(regions, warnings)``; tolerant of CRLF and malformed input."""
    lines = text.split("\n")
    regions, warns = [], []
    state, cur = None, None
    for i, raw in enumerate(lines, 1):
        ln = raw[:-1] if raw.endswith("\r") else raw
        m = _MARK.match(ln)
        kind = m.group(1)[0] if m else None
        if kind == "<" and state is None:
            cur = {"start_line": i, "ours_label": (m.group(2) or "").strip(), "ours": [], "base": None, "theirs": [], "theirs_label": "",
                   "_o": i + 1, "_b": None, "_t": None}
            state = "ours"
        elif kind == "|" and state == "ours":
            cur["base"], cur["_b"], state = [], i + 1, "base"
        elif kind == "=" and state in ("ours", "base"):
            cur["_t"], state = i + 1, "theirs"
        elif kind == ">" and state == "theirs":
            cur["theirs_label"] = (m.group(2) or "").strip()
            cur["end_line"] = i
            cur["ours_range"] = [cur["_o"], (cur["_b"] or cur["_t"]) - 2] if cur["ours"] else None
            cur["base_range"] = [cur["_b"], cur["_t"] - 2] if cur["base"] else None
            cur["theirs_range"] = [cur["_t"], i - 1] if cur["theirs"] else None
            for k in ("_o", "_b", "_t"):
                cur.pop(k)
            regions.append(cur)
            state, cur = None, None
        elif state:
            cur[state].append(ln)
    if state is not None:
        warns.append(f"unterminated conflict region starting at line {cur['start_line']}")
    return regions, warns


def _ws(s: list) -> str:
    return re.sub(r"\s+", "", "\n".join(s))


def region_hint(r: dict) -> dict:
    o, t, b = r["ours"], r["theirs"], r["base"]
    if o == t:
        return {"hint": "identical-change", "basis": "both sides are textually identical inside this region"}
    if _ws(o) == _ws(t):
        return {"hint": "whitespace-only-difference", "basis": "sides differ only in whitespace"}
    if b is not None:
        if o == b:
            return {"hint": "only-theirs-changed", "basis": "ours equals the base text; only theirs modified it"}
        if t == b:
            return {"hint": "only-ours-changed", "basis": "theirs equals the base text; only ours modified it"}
        if _ws(o) == _ws(b):
            return {"hint": "one-side-only-whitespace", "basis": "ours differs from base only in whitespace (so theirs holds the real change)"}
        if _ws(t) == _ws(b):
            return {"hint": "one-side-only-whitespace", "basis": "theirs differs from base only in whitespace (so ours holds the real change)"}
        if not b and o and t:
            return {"hint": "independent-hunks", "basis": "both sides ADDED different text at the same spot (base empty): often both can be kept"}
    if not o or not t:
        return {"hint": "delete-vs-modify", "basis": "one side has no lines here (deleted) while the other has text"}
    return {"hint": "overlapping", "basis": "both sides changed the same lines differently"}


def _lines_text(lines: list) -> dict:
    d = {"text": "\n".join(lines[:MAX_TEXT_LINES]), "line_count": len(lines)}
    if len(lines) > MAX_TEXT_LINES:
        d["truncated"] = True
    return d


def _find_block(blob_lines: list, block: list, expect: int) -> Optional[int]:
    """1-based start of ``block`` in ``blob_lines`` closest to ``expect``; None if absent."""
    if not block:
        return None
    n = len(block)
    best = None
    for i in range(0, len(blob_lines) - n + 1):
        if blob_lines[i:i + n] == block:
            if best is None or abs(i + 1 - expect) < abs(best - expect):
                best = i + 1
    return best


def _blame_summary(root: Path, path: str, rev: str, start: int, n: int, cache: dict) -> list:
    n = min(n, MAX_BLAME_RANGE)
    bl = git.blame(path, rev, (start, start + n - 1), cwd=root)
    by: dict = {}
    for b in bl:
        e = by.setdefault(b["sha"], {"sha": b["sha"], "summary": b.get("summary", ""), "author": b.get("author", ""), "lines": 0})
        e["lines"] += 1
    return sorted(by.values(), key=lambda e: (-e["lines"], e["sha"]))[:5]


def _side_commits(root: Path, rng: str, path: str, limit: int = 10) -> list:
    out = []
    for c in git.log_commits(rng, paths=[path], limit=limit, cwd=root):
        out.append({"sha": c.sha, "short": c.short, "subject": c.subject, "author": c.author_name, "date": c.author_date})
    return out


def _renames(root: Path, a: str, b: str) -> dict:
    """new_path -> old_path for renames between two commits."""
    r = git.run(["diff", "--name-status", "-M", "-z", a, b], cwd=root, timeout=30)
    parts = r.stdout.split("\x00") if r.ok else []
    out, i = {}, 0
    while i < len(parts):
        code = parts[i]
        i += 1
        if code[:1] == "R" and i + 1 < len(parts):
            out[parts[i + 1]] = parts[i]
            i += 2
        elif code:
            i += 1
    return out


def _blob_lines(root: Path, stage: int, path: str) -> list:
    t = git.show_file(f":{stage}", path, root)
    return t.replace("\r\n", "\n").split("\n") if t is not None else []


def _ws_only_equal(root: Path, a: str, b: str) -> bool:
    r = git.run(["diff", "--no-color", "-w", "--quiet", a, b], cwd=root, timeout=20)
    return r.returncode == 0


_TYPE = {
    (1, 2, 3): "both-modified", (2, 3): "both-added", (1, 2): "deleted-by-theirs", (1, 3): "deleted-by-ours",
    (1,): "both-deleted", (2,): "added-by-us", (3,): "added-by-them",
}


def analyze(root: Path) -> dict:
    warnings: list = []
    st = repo_state(root)
    warnings += state_warnings(st)
    op = st["operation"]
    unmerged = git.ls_files_stage(root, unmerged_only=True)
    if not unmerged:
        msg = "no unmerged paths: not currently in a conflicted state" + (f" ({op} in progress without conflicts)" if op in _OPS else "")
        return {"repo": str(root), "state": st, "conflicts": [], "message": msg, "warnings": warnings}
    by_path: dict = {}
    for e in unmerged:
        by_path.setdefault(e["path"], {})[e["stage"]] = e

    gd = git.git_dir(root)
    head = st["head"]
    ref_name, ours_desc, theirs_desc = _OPS.get(op or "", ("MERGE_HEAD", "HEAD", "the other side"))
    theirs_sha = git.rev_parse(ref_name, root) if op in _OPS else None
    if op == "rebase" and theirs_sha is None:
        warnings.append("REBASE_HEAD not found (rebase may be between steps); 'theirs' history is unavailable")
    if op is None:
        warnings.append("unmerged paths exist but no merge/rebase/cherry-pick/revert marker was found; sides are labelled by stage only")
    swap_note = None
    if op == "rebase":
        swap_note = ("REBASE: ours and theirs are SWAPPED relative to a merge. 'ours' (stage 2) is the branch you are rebasing ONTO "
                     "(upstream plus already-replayed commits); 'theirs' (stage 3) is YOUR commit being replayed. So `--ours` keeps the upstream "
                     "version and `--theirs` keeps your own work. Never pick a side without reading both.")
    elif op == "revert":
        swap_note = "REVERT: 'theirs' is the inverse of the reverted commit's change (the parent version), so theirs = the state before that commit."
    elif op == "cherry-pick":
        swap_note = "CHERRY-PICK: 'ours' is your current branch; 'theirs' is the picked commit's version."
    base_sha = None
    pick_base = None
    if theirs_sha and head:
        base_sha = git.merge_base(head, theirs_sha, root)
        if op in ("rebase", "cherry-pick", "revert"):
            pb = git.rev_parse(theirs_sha + "^", root) if not theirs_sha.startswith("-") else None
            pick_base = pb
    side_base = pick_base if op in ("rebase", "cherry-pick", "revert") and pick_base else base_sha
    rebase_info = {}
    if op == "rebase":
        for d in ("rebase-merge", "rebase-apply"):
            for key, fn in (("onto", "onto"), ("branch_being_rebased", "head-name")):
                f = gd / d / fn
                try:
                    if f.is_file():
                        rebase_info[key] = f.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    pass

    ours_renames = theirs_renames = None
    conflicts = []
    for path in sorted(by_path):
        stages = by_path[path]
        key = tuple(sorted(stages))
        ctype = _TYPE.get(key, "unknown")
        item: dict = {"path": path, "conflict_type": ctype,
                      "stages": {str(s): {"sha": stages[s]["sha"], "mode": stages[s]["mode"]} for s in sorted(stages)},
                      "stage_meaning": {"1": "base (merge-base version)", "2": "ours", "3": "theirs"}}
        if any(e["mode"] == "160000" for e in stages.values()):
            item["submodule"] = True
            item["relationship_hint"] = {"hint": "submodule-pointer-conflict", "basis": "gitlink entries", "note": HINT_NOTE}
            conflicts.append(item)
            continue
        # worktree file
        f = root / path
        regions, text = [], None
        try:
            if f.is_file():
                data = f.read_bytes()
                if b"\x00" in data[:8192]:
                    item["binary"] = True
                else:
                    text = data.decode("utf-8", errors="replace")
                    item["line_endings"] = "crlf" if "\r\n" in text else "lf"
            else:
                item["worktree_missing"] = True
        except OSError as e:
            item["worktree_error"] = str(e)
        item["ours_label"], item["theirs_label"] = ours_desc, theirs_desc
        if text is not None:
            regions, w = parse_markers(text)
            item["parse_warnings"] = w
            if not regions and "<<<<<<<" not in text:
                item["note"] = "no conflict markers in the working-tree file (may already be edited, but the path is still unmerged until `git add`)"

        # ours/theirs blobs for line lookup
        blob_o = stages.get(2, {}).get("sha")
        blob_t = stages.get(3, {}).get("sha")
        lines_o = _blob_lines(root, 2, path) if 2 in stages and not item.get("binary") else []
        lines_t = _blob_lines(root, 3, path) if 3 in stages and not item.get("binary") else []
        out_regions = []
        for idx, r in enumerate(regions[:MAX_REGIONS], 1):
            d = {"index": idx, "start_line": r["start_line"], "end_line": r["end_line"], "ours_label": r["ours_label"],
                 "theirs_label": r["theirs_label"], "ours_range": r["ours_range"], "theirs_range": r["theirs_range"],
                 "base_range": r["base_range"], "ours": _lines_text(r["ours"]), "theirs": _lines_text(r["theirs"]),
                 "base": _lines_text(r["base"]) if r["base"] is not None else None,
                 "has_diff3_base": r["base"] is not None}
            h = region_hint(r)
            d["relationship_hint"] = {**h, "note": HINT_NOTE}
            blame = {}
            if head:
                so = _find_block(lines_o, r["ours"], r["start_line"]) if r["ours"] else None
                if so:
                    blame["ours"] = _blame_summary(root, path, "HEAD", so, len(r["ours"]), {})
                if theirs_sha and r["theirs"]:
                    stt = _find_block(lines_t, r["theirs"], r["start_line"])
                    if stt:
                        blame["theirs"] = _blame_summary(root, path, theirs_sha, stt, len(r["theirs"]), {})
            d["blame"] = blame
            out_regions.append(d)
        if len(regions) > MAX_REGIONS:
            item["regions_truncated"] = len(regions) - MAX_REGIONS
        item["regions"] = out_regions
        item["region_count"] = len(regions)

        # file-level hint
        hint = None
        if item.get("binary"):
            hint = ("binary-choose-a-side", "binary file: cannot be merged textually")
        elif ctype in ("deleted-by-ours", "deleted-by-theirs"):
            hint = ("delete-vs-modify", f"{'ours' if ctype == 'deleted-by-ours' else 'theirs'} deleted the file; the other side modified it")
        elif ctype == "both-deleted":
            hint = ("both-deleted", "both sides deleted the file")
        elif ctype == "both-added":
            if blob_o == blob_t:
                hint = ("identical-change", "both sides added identical content")
            else:
                hint = ("add-add-different", "both sides added the file with different content")
        elif ctype == "both-modified":
            if blob_o == blob_t:
                hint = ("identical-change", "both sides modified the file identically (conflict may be only mode/line-ending)")
            elif 1 in stages:
                b1 = stages[1]["sha"]
                if _ws_only_equal(root, b1, blob_o):
                    hint = ("one-side-only-whitespace", "ours differs from base only in whitespace")
                elif _ws_only_equal(root, b1, blob_t):
                    hint = ("one-side-only-whitespace", "theirs differs from base only in whitespace")
            if hint is None and regions:
                hs = {o["relationship_hint"]["hint"] for o in out_regions}
                if len(regions) > 1 and hs <= {"independent-hunks", "only-ours-changed", "only-theirs-changed", "identical-change"}:
                    hint = ("independent-hunks", f"{len(regions)} separate regions that look individually resolvable")
                else:
                    hint = ("overlapping", f"{len(regions)} region(s) where both sides changed the same lines" if len(regions) else "")
            if hint is None:
                hint = ("unclear", "could not derive a hint")
        else:
            hint = ("unclear", "unusual stage combination")
        item["relationship_hint"] = {"hint": hint[0], "basis": hint[1], "note": HINT_NOTE}

        # rename hints for non-modify/modify conflicts
        if ctype != "both-modified" and head and theirs_sha and side_base:
            try:
                if ours_renames is None:
                    ours_renames = _renames(root, side_base, head)
                    theirs_renames = _renames(root, side_base, theirs_sha)
                rn = []
                for side, rmap in (("ours", ours_renames), ("theirs", theirs_renames)):
                    for new, old in rmap.items():
                        if old == path:
                            rn.append(f"{side} renamed {old} -> {new}")
                        elif new == path:
                            rn.append(f"{side} renamed {old} -> {new} (this path)")
                if rn:
                    item["rename_hints"] = rn
                    item["relationship_hint"]["basis"] += "; possible rename conflict: " + "; ".join(rn)
            except git.GitError:
                pass

        # history per side
        if head and theirs_sha and side_base:
            item["history"] = {"ours": _side_commits(root, f"{base_sha or side_base}..{head}", path),
                               "theirs": _side_commits(root, f"{side_base}..{theirs_sha}", path)}
        conflicts.append(item)

    return {
        "repo": str(root), "state": st, "operation": op, "swap_note": swap_note,
        "ours": {"ref": "HEAD", "sha": head, "meaning": ours_desc},
        "theirs": {"ref": ref_name if op in _OPS else None, "sha": theirs_sha, "meaning": theirs_desc},
        "merge_base": base_sha, "pick_base": pick_base, "rebase": rebase_info or None,
        "conflicts": conflicts, "summary": {"files": len(conflicts), "regions": sum(c.get("region_count", 0) for c in conflicts)},
        "read_only": True,
        "notes": ["Analysis only: nothing was edited, staged or checked out. Resolving means editing the file and `git add`, which only happens if the user asks."],
        "warnings": warnings,
    }
