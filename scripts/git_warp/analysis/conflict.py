"""Read-only inspection of unresolved index stages, merge bases, and history."""
from __future__ import annotations
from pathlib import Path
import re
from ..git import DEFAULT_TIMEOUT_SECONDS
from .common import read, resolve_commit, root
from .models import Analysis, Evidence, Finding


def _blame_shas(repo: Path, rev: str, path: str, timeout: float) -> tuple[str, ...]:
    raw = read(repo, ("blame", "--line-porcelain", rev, "--", path), timeout, check=False)
    found = []
    for line in raw.splitlines():
        match = re.match(r"^([0-9a-f]{40}) \d+ \d+", line)
        if match and match.group(1) not in found:
            found.append(match.group(1))
        if len(found) >= 10:
            break
    return tuple(found)


def inspect_conflicts(cwd: str | Path, *, base: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS, content_limit: int = 12000) -> Analysis:
    repo = root(cwd, timeout)
    resolved_base = resolve_commit(repo, base, timeout) if base else None
    raw = read(repo, ("ls-files", "-u", "-z"), timeout)
    stages: dict[str, dict[int, str]] = {}
    for record in raw.split("\0"):
        if not record:
            continue
        header, sep, path = record.partition("\t")
        parts = header.split()
        if sep and len(parts) == 3 and parts[2].isdigit():
            stages.setdefault(path, {})[int(parts[2])] = parts[1]
    merge_base = None
    branch_commits: dict[str, tuple[tuple[str, str], ...]] = {"ours_only": (), "base_only": ()}
    if resolved_base:
        merge_base = read(repo, ("merge-base", "--", "HEAD", resolved_base), timeout, check=False).strip() or None
        for label, rev_range in (("ours_only", f"{resolved_base}..HEAD"), ("base_only", f"HEAD..{resolved_base}")):
            lines = read(repo, ("log", "--format=%H%x1f%s", "--max-count=20", rev_range, "--"), timeout, check=False).splitlines()
            branch_commits[label] = tuple((fields[0], fields[1]) for line in lines if len((fields := line.split("\x1f", 1))) == 2)
    findings = []
    proposals = []
    evidence = []
    blame_evidence: dict[str, dict[str, tuple[str, ...]]] = {}
    for path, values in sorted(stages.items()):
        stage_text: dict[str, str] = {}
        for number in (1, 2, 3):
            if number in values:
                stage_text[str(number)] = read(repo, ("show", f":{number}:{path}"), timeout, check=False)[:content_limit]
                evidence.append(Evidence(f"index stage {number}", f"{path} ({values[number]})"))
        missing = tuple(str(s) for s in (1, 2, 3) if s not in values)
        uncertainties = (("One or more index stages are absent, as can happen for add/delete conflicts.",) if missing else ()) + ("Textual comparison cannot establish intended semantics; no automatic resolution is proposed.",)
        findings.append(Finding("Unmerged path", f"{path} has index stage(s) {', '.join(map(str, sorted(values)))}.", tuple(Evidence(f"stage {s}", values[s]) for s in sorted(values)), uncertainties, "high"))
        branch_blame: dict[str, tuple[str, ...]] = {}
        if resolved_base:
            for side, rev in (("ours", "HEAD"), ("base", resolved_base)):
                shas = _blame_shas(repo, rev, path, timeout)
                branch_blame[side] = shas
                evidence.extend(Evidence(f"{side} blame", path, sha) for sha in shas)
        blame_evidence[path] = branch_blame
        proposals.append({"path": path, "available_stages": tuple(sorted(values)), "missing_stages": missing, "base_excerpt": stage_text.get("1", ""), "ours_excerpt": stage_text.get("2", ""), "theirs_excerpt": stage_text.get("3", ""), "semantic_status": "unknown", "semantic_proposal": None, "blame": branch_blame, "action": "human review required; no index or worktree changes made"})
    if merge_base:
        evidence.append(Evidence("merge base", merge_base, merge_base))
    for side, commits in branch_commits.items():
        for sha, subject in commits:
            evidence.append(Evidence(f"{side} branch commit", subject, sha))
    uncertainty = [] if stages else ["No unmerged index entries were found; an already resolved conflict or merge intent may not be represented."]
    if resolved_base and not merge_base:
        uncertainty.append(f"Could not determine a merge base with {resolved_base}; branch-side commit comparisons may be incomplete.")
    if resolved_base:
        uncertainty.append("Blame and branch commit evidence describe history only; they do not establish which side's intent should win.")
    return Analysis("conflict", f"Found {len(stages)} unresolved path(s)." + (f" Merge base with {resolved_base}: {merge_base or 'unavailable'}." if resolved_base else ""), tuple(findings), tuple(evidence), tuple(uncertainty), tuple(proposals), {"merge_base": merge_base, "paths": tuple(sorted(stages)), "branch_commits": branch_commits, "blame": blame_evidence})
