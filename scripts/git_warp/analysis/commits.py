"""Proposal-only grouping of current diff hunks or historical commits."""
from __future__ import annotations
from pathlib import Path
import re

from ..git import DEFAULT_TIMEOUT_SECONDS
from .common import log_records, read, root
from .models import Analysis, Evidence

_STOP = {"a", "an", "and", "as", "at", "by", "fix", "for", "from", "in", "of", "on", "the", "to", "with", "update", "updated", "add", "change", "changes"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1 and t not in _STOP}


def _revision_groups(cwd: Path, revs: str, limit: int, timeout: float) -> Analysis:
    records = log_records(cwd, revs, limit, timeout)
    items = []
    for sha, subject, author in records:
        raw = read(cwd, ("show", "--format=", "--name-only", "-z", sha, "--"), timeout, check=False)
        paths = frozenset(p for p in raw.split("\0") if p)
        items.append({"sha": sha, "subject": subject, "author": author, "paths": paths, "tokens": _tokens(subject)})
    groups: list[dict] = []
    for item in items:
        best = None
        best_score = 0.0
        for group in groups:
            token_score = len(item["tokens"] & group["tokens"]) / max(1, len(item["tokens"] | group["tokens"]))
            path_score = len(item["paths"] & group["paths"]) / max(1, len(item["paths"] | group["paths"]))
            score = .6 * token_score + .4 * path_score
            if score > best_score:
                best, best_score = group, score
        if best is not None and best_score >= .18:
            best["commits"].append(item)
            best["tokens"].update(item["tokens"])
            best["paths"].update(item["paths"])
            best["scores"].append(round(best_score, 3))
        else:
            groups.append({"commits": [item], "tokens": set(item["tokens"]), "paths": set(item["paths"]), "scores": []})
    proposals = tuple({"suggested_theme": " / ".join(x["subject"] for x in g["commits"][:2]), "commits": tuple(x["sha"] for x in g["commits"]), "subjects": tuple(x["subject"] for x in g["commits"]), "paths": tuple(sorted(g["paths"])), "heuristic_scores": tuple(g["scores"]), "action": "proposal for human review; no commit operation performed"} for g in groups if len(g["commits"]) > 1)
    evidence = tuple(Evidence("commit subject", x["subject"], x["sha"]) for x in items)
    return Analysis("commit-clusters", f"Reviewed {len(items)} commits; proposed {len(proposals)} multi-commit group(s).", evidence=evidence, uncertainty=("Grouping uses subject token overlap and changed-path overlap; it is a review proposal, not proof of semantic equivalence or safe squashing.",), proposals=proposals, metadata={"mode": "revision", "revision": revs, "commits_reviewed": len(items), "singletons": sum(1 for g in groups if len(g["commits"]) == 1)})


def _parse_hunks(patch: str, scope: str) -> list[dict]:
    path = None
    hunk = None
    units: list[dict] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if hunk is not None:
                units.append(hunk)
            hunk = {"scope": scope, "path": path, "range": (int(match.group(3)), int(match.group(4) or 1)) if match else None, "lines": [], "header": line}
        elif hunk is not None and line[:1] in {"+", "-"} and not line.startswith(("+++", "---")):
            hunk["lines"].append(line[1:])
    if hunk is not None:
        units.append(hunk)
    for unit in units:
        unit["tokens"] = _tokens(" ".join(unit["lines"]) + " " + str(unit["path"]))
    return units


def _working_tree_groups(cwd: Path, timeout: float) -> Analysis:
    staged = read(cwd, ("diff", "--cached", "--no-ext-diff", "--unified=3", "--"), timeout, check=False)
    unstaged = read(cwd, ("diff", "--no-ext-diff", "--unified=3", "--"), timeout, check=False)
    units = _parse_hunks(staged, "staged") + _parse_hunks(unstaged, "unstaged")
    groups: list[dict] = []
    for unit in units:
        best, best_score = None, 0.0
        for group in groups:
            union = unit["tokens"] | group["tokens"]
            lexical = len(unit["tokens"] & group["tokens"]) / max(1, len(union))
            same_path = any(unit["path"] == existing["path"] for existing in group["units"])
            score = .75 * lexical + (.25 if same_path else 0)
            if score > best_score:
                best, best_score = group, score
        if best is not None and best_score >= .16:
            best["units"].append(unit)
            best["tokens"].update(unit["tokens"])
            best["scores"].append(round(best_score, 3))
        else:
            groups.append({"units": [unit], "tokens": set(unit["tokens"]), "scores": []})
    proposals = []
    for group in groups:
        us = group["units"]
        if len(us) < 2:
            continue
        proposals.append({"suggested_theme": " / ".join(sorted({Path(u["path"] or "unknown").name for u in us})), "changes": tuple({"scope": u["scope"], "path": u["path"], "new_line_range": u["range"], "hunk": u["header"]} for u in us), "heuristic_scores": tuple(group["scores"]), "action": "proposal for human review; no staging or commit operation performed"})
    evidence = tuple(Evidence("diff hunk", f"{u['scope']} {u['path']} {u['header']}") for u in units)
    untracked = read(cwd, ("ls-files", "--others", "--exclude-standard", "-z"), timeout, check=False)
    unknown = tuple(f"Untracked file has no index diff hunks: {p}" for p in untracked.split("\0") if p)
    return Analysis("commit-clusters", f"Reviewed {len(units)} staged/unstaged diff hunk(s); proposed {len(proposals)} related group(s).", evidence=evidence, uncertainty=("Hunk token and path overlap is a weak semantic signal; empty diffs produce no proposal and untracked content is not clustered.", *unknown), proposals=tuple(proposals), metadata={"mode": "working-tree", "hunks_reviewed": len(units), "staged_hunks": sum(u["scope"] == "staged" for u in units), "unstaged_hunks": sum(u["scope"] == "unstaged" for u in units)})


def propose_commit_groups(cwd: str | Path, *, revs: str | None = None, limit: int = 100, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Analysis:
    """Cluster existing commits when ``revs`` is supplied; otherwise analyze current diff hunks."""
    repo = root(cwd, timeout)
    if revs is not None:
        return _revision_groups(repo, revs, limit, timeout)
    return _working_tree_groups(repo, timeout)
