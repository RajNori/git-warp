"""Read-only Git recovery, archaeology, and bisect preflight services.

These helpers gather evidence without changing refs, the index, or worktree
contents. Recovery commands are recommendations for a human to review.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal, Sequence

from ..git import DEFAULT_TIMEOUT_SECONDS, git_dir, run_git

Certainty = Literal["FACT", "INFERENCE", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class Evidence:
    """A bounded claim and the exact Git output that supports it."""

    level: str
    claim: str
    evidence: tuple[str, ...]
    certainty: Certainty


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    """One observed commit, ref, stash, or reflog entry."""

    kind: Literal["reflog", "unreachable", "stash", "branch"]
    oid: str
    name: str | None
    subject: str
    date: str | None
    evidence: str


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    candidates: tuple[RecoveryCandidate, ...]
    findings: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class HistoryCommit:
    oid: str
    date: str
    subject: str
    evidence: str


@dataclass(frozen=True, slots=True)
class ArchaeologyReport:
    commits: tuple[HistoryCommit, ...]
    findings: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class BisectPreflight:
    ready: bool
    good_oid: str | None
    bad_oid: str | None
    operation_state: tuple[str, ...]
    findings: tuple[Evidence, ...]
    predicate_guidance: tuple[str, ...]


_OID = re.compile(r"^[0-9a-fA-F]{40,64}$")
_COMMIT_FORMAT = "%H%x00%aI%x00%s"


def _records(output: str, fields: int) -> list[tuple[str, ...]]:
    """Parse NUL-separated records; ignore malformed/truncated trailing data."""
    parsed: list[tuple[str, ...]] = []
    for line in output.splitlines():
        parts = tuple(line.split("\x00"))
        if len(parts) == fields and parts[0]:
            parsed.append(parts)
    return parsed


def _subject(cwd: str | Path, oid: str, timeout: float) -> str:
    result = run_git(
        ("show", "-s", "--format=%s", "--no-show-signature", oid),
        cwd=cwd,
        timeout=timeout,
    )
    return result.stdout.rstrip("\r\n") if result.returncode == 0 else "(commit subject unavailable)"


def discover_recovery(
    cwd: str | Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RecoveryReport:
    """Find commit candidates in reflogs, unreachable objects, stashes, and refs.

    All Git invocations are inspection commands. No candidate branch or other
    ref is created, and untrusted reflog text is only returned as evidence.
    """
    candidates: list[RecoveryCandidate] = []
    findings: list[Evidence] = []

    reflog = run_git(
        ("reflog", "show", "--all", "--date=iso-strict", "--format=%H%x00%gD%x00%gs"),
        cwd=cwd,
        timeout=timeout,
    )
    if reflog.returncode == 0:
        for oid, selector, subject in _records(reflog.stdout, 3):
            if not _OID.fullmatch(oid):
                continue
            line = f"{oid} {selector}: {subject}"
            candidates.append(RecoveryCandidate("reflog", oid.lower(), selector, subject, None, line))
    else:
        findings.append(Evidence("warning", "Reflog entries could not be read.", (reflog.stderr.strip(),), "UNKNOWN"))

    fsck = run_git(
        ("fsck", "--full", "--no-reflogs", "--unreachable"),
        cwd=cwd,
        timeout=timeout,
    )
    if fsck.returncode in (0, 1):
        for line in fsck.stdout.splitlines():
            match = re.match(r"^(?:unreachable|dangling) commit ([0-9a-fA-F]{40,64})$", line.strip())
            if not match:
                continue
            oid = match.group(1).lower()
            candidates.append(RecoveryCandidate("unreachable", oid, None, _subject(cwd, oid, timeout), None, line.strip()))
    else:
        findings.append(Evidence("warning", "Unreachable objects could not be inspected.", (fsck.stderr.strip(),), "UNKNOWN"))

    stashes = run_git(
        ("stash", "list", "--date=iso-strict", "--format=%H%x00%gd%x00%gs"),
        cwd=cwd,
        timeout=timeout,
    )
    if stashes.returncode == 0:
        for oid, selector, subject in _records(stashes.stdout, 3):
            if _OID.fullmatch(oid):
                candidates.append(RecoveryCandidate("stash", oid.lower(), selector, subject, None, f"{oid} {selector}: {subject}"))

    refs = run_git(
        ("for-each-ref", "--format=%(objectname)%00%(refname)%00%(committerdate:iso-strict)%00%(subject)", "refs/heads", "refs/remotes"),
        cwd=cwd,
        timeout=timeout,
    )
    if refs.returncode == 0:
        for oid, name, date, subject in _records(refs.stdout, 4):
            if _OID.fullmatch(oid):
                candidates.append(RecoveryCandidate("branch", oid.lower(), name, subject, date or None, f"{name} -> {oid}: {subject}"))

    # Preserve source order while removing duplicate rows from overlapping
    # reflogs and refs. The same OID in distinct sources remains informative.
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[RecoveryCandidate] = []
    for item in candidates:
        key = (item.kind, item.oid, item.name)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    findings.append(Evidence(
        "observation",
        f"Found {len(unique)} recovery candidates across reflogs, unreachable commits, stashes, and branch refs.",
        tuple(f"{c.kind}: {c.oid} {c.name or ''}".strip() for c in unique[:100]),
        "FACT",
    ))
    findings.append(Evidence(
        "next_step",
        "Inspect candidate commits and their trees before suggesting a recovery command; no refs were changed.",
        ("git show --stat <candidate>", "git show <candidate>"),
        "INFERENCE",
    ))
    return RecoveryReport(tuple(unique), tuple(findings))


def analyze_history(
    cwd: str | Path,
    *,
    path: str | None = None,
    query: str | None = None,
    limit: int = 100,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ArchaeologyReport:
    """Return a factual commit timeline for a path and/or commit-message query."""
    if path is not None and ("\x00" in path or path.startswith("-")):
        raise ValueError("path must be a repository-relative path, not an option")
    if query is not None and "\x00" in query:
        raise ValueError("query cannot contain NUL bytes")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")

    args: list[str] = ["log", "--all", f"--format={_COMMIT_FORMAT}", "--date=iso-strict", f"-n{limit}"]
    if query:
        args.extend(("--regexp-ignore-case", "--grep", query))
    args.append("--")
    if path:
        args.append(path)
    result = run_git(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        return ArchaeologyReport((), (Evidence("error", "Git history query failed.", (result.stderr.strip(),), "UNKNOWN"),))

    commits: list[HistoryCommit] = []
    for oid, date, subject in _records(result.stdout, 3):
        if not _OID.fullmatch(oid):
            continue
        commit_evidence = f"git log: {oid} {date} {subject}"
        commits.append(HistoryCommit(oid.lower(), date, subject, commit_evidence))

    findings: list[Evidence] = []
    if commits:
        findings.append(Evidence(
            "timeline",
            f"Git history returned {len(commits)} commits for the requested scope.",
            tuple(commit.evidence for commit in commits),
            "FACT",
        ))
        findings.append(Evidence(
            "interpretation",
            "Commit subjects and order can suggest the sequence of changes, but do not establish author intent by themselves.",
            tuple(commit.evidence for commit in commits),
            "INFERENCE",
        ))
    else:
        findings.append(Evidence("unknown", "No matching commit evidence was returned; the reason or intent is unknown.", ("git log returned no matching commits",), "UNKNOWN"))
    return ArchaeologyReport(tuple(commits), tuple(findings))


def bisect_preflight(
    cwd: str | Path,
    *,
    good: str,
    bad: str,
    predicate: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> BisectPreflight:
    """Check bisect endpoints and tree safety without starting a bisect."""
    findings: list[Evidence] = []
    guidance = (
        "Use a predicate that gives the same result for the same commit and does not depend on network, clock, randomness, or ambient user state.",
        "Run the exact command manually on the known-good and known-bad commits before using `git bisect run`.",
        "For `git bisect run`, exit 0 means good, exit 1–124 means bad, exit 125 skips a commit, and 126/127 or 128+ abort the run.",
        "This preflight does not execute the predicate or start/alter a bisect session.",
    )
    if not good.strip() or not bad.strip():
        return BisectPreflight(False, None, None, (), (Evidence("error", "Both good and bad revisions are required.", (), "UNKNOWN"),), guidance)

    resolved: list[str | None] = []
    for label, revision in (("good", good), ("bad", bad)):
        result = run_git(("rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"), cwd=cwd, timeout=timeout)
        oid = result.stdout.strip().lower() if result.returncode == 0 else None
        if oid is None or not _OID.fullmatch(oid):
            findings.append(Evidence("error", f"The {label} revision did not resolve to a commit.", (revision,), "UNKNOWN"))
        else:
            findings.append(Evidence("endpoint", f"The {label} revision resolves to {oid}.", (f"{revision} -> {oid}",), "FACT"))
        resolved.append(oid)
    good_oid, bad_oid = resolved

    status = run_git(("status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd=cwd, timeout=timeout)
    clean = status.returncode == 0 and not status.stdout
    findings.append(Evidence("worktree", "Working tree and index are clean." if clean else "Working tree or index has changes, or status could not be read.", ("git status --porcelain=v1 -z",), "FACT" if clean else "UNKNOWN"))

    # A clean status does not mean a repository is idle: a bisect session can
    # be active between steps, and a stopped rebase can have unmerged state.
    # Inspect only Git's per-worktree administrative markers; never read or
    # modify their contents.
    administrative_dir = git_dir(cwd=cwd, timeout=timeout)
    marker_paths = (
        ("bisect", "BISECT_START"),
        ("bisect", "BISECT_LOG"),
        ("rebase", "rebase-merge"),
        ("rebase", "rebase-apply"),
        ("rebase", "REBASE_HEAD"),
    )
    operation_state = tuple(
        marker for kind, marker in marker_paths
        if (administrative_dir / marker).exists()
    )
    if operation_state:
        label = "Active bisect state" if any(name.startswith("BISECT_") for name in operation_state) else "Interrupted rebase state"
        findings.append(Evidence(
            "repository_operation",
            f"{label} was detected; do not start a new bisect until the existing operation is reviewed and resolved.",
            operation_state,
            "FACT",
        ))
    else:
        findings.append(Evidence("repository_operation", "No active bisect or rebase state was detected.", (), "FACT"))

    ordered = False
    if good_oid and bad_oid:
        if good_oid == bad_oid:
            findings.append(Evidence("error", "Good and bad endpoints resolve to the same commit.", (good_oid,), "FACT"))
        else:
            ancestor = run_git(("merge-base", "--is-ancestor", good_oid, bad_oid), cwd=cwd, timeout=timeout)
            ordered = ancestor.returncode == 0
            certainty: Certainty = "FACT" if ancestor.returncode in (0, 1) else "UNKNOWN"
            findings.append(Evidence("ancestry", "Good is an ancestor of bad." if ordered else "Good was not verified as an ancestor of bad.", (f"git merge-base --is-ancestor {good_oid} {bad_oid}",), certainty))
    predicate_ok = bool(predicate.strip())
    findings.append(Evidence("predicate", "A predicate was supplied for review." if predicate_ok else "No test predicate was supplied.", (predicate.strip()[:500],) if predicate_ok else (), "FACT" if predicate_ok else "UNKNOWN"))
    ready = bool(good_oid and bad_oid and good_oid != bad_oid and clean and not operation_state and ordered and predicate_ok)
    findings.append(Evidence("preflight", "Endpoints and predicate are ready for a human-reviewed bisect." if ready else "Resolve the preflight findings before starting a bisect.", (), "INFERENCE" if ready else "UNKNOWN"))
    return BisectPreflight(ready, good_oid, bad_oid, operation_state, tuple(findings), guidance)


__all__ = [
    "ArchaeologyReport", "BisectPreflight", "Evidence", "HistoryCommit",
    "RecoveryCandidate", "RecoveryReport", "analyze_history",
    "bisect_preflight", "discover_recovery",
]
