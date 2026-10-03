"""History-backed temporal review with conservative revert/reintroduction signals."""
from __future__ import annotations
from pathlib import Path
import re
from ..git import DEFAULT_TIMEOUT_SECONDS
from .common import changed_paths, read, root
from .models import Analysis, Evidence, Finding

_REINTRODUCE = re.compile(r"\b(reintroduc(?:e|es|ed|ing)|restore(?:s|d)?|reinstate(?:s|d)?|bring back)\b", re.I)
_REVERT_TARGET = re.compile(r"(?im)^This reverts commit ([0-9a-f]{7,40})\.?\s*$")
_REVERT_WORD = re.compile(r"\brevert(?:s|ed|ing)?\b", re.I)


def temporal_review(cwd: str | Path, *, paths: tuple[str, ...] | None = None, limit: int = 200, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Analysis:
    repo = root(cwd, timeout)
    selected = paths if paths is not None else changed_paths(repo, timeout=timeout)
    records = []
    for path in selected:
        raw = read(repo, ("log", f"-n{limit}", "--format=%H%x1f%ct%x1f%s", "--", path), timeout, check=False)
        history = []
        for line in raw.splitlines():
            fields = line.split("\x1f", 2)
            if len(fields) == 3 and re.fullmatch(r"[0-9a-fA-F]{7,40}", fields[0]):
                history.append((fields[0], fields[1], fields[2]))
        records.append((path, history))
    all_entries = [(path, sha, stamp, subject) for path, hist in records for sha, stamp, subject in hist]
    findings = []
    revert_signals = []
    signal_evidence = []
    for path, hist in records:
        if len(hist) >= 5:
            recent = hist[:5]
            findings.append(Finding("Recent change concentration", f"{path} appears in the latest {len(recent)} inspected commits for this path.", tuple(Evidence("commit subject", subject, sha) for sha, _, subject in recent), ("Commit count indicates recent activity, not defect likelihood or authorship intent.",), "low"))
        for sha, _, subject in hist:
            body = read(repo, ("show", "-s", "--format=%B", sha, "--"), timeout, check=False)
            target_match = _REVERT_TARGET.search(body)
            explicit = target_match.group(1) if target_match else None
            wording = bool(_REINTRODUCE.search(subject) or _REINTRODUCE.search(body))
            revert_wording = bool(_REVERT_WORD.search(subject) or explicit)
            if explicit or wording or (revert_wording and subject.lower().startswith("revert")):
                revert_signals.append({"path": path, "sha": sha, "subject": subject, "kind": "explicit-revert-target" if explicit else ("reintroduction-wording" if wording else "revert-wording"), "target_sha": explicit})
                items = [Evidence("history signal", subject, sha)]
                if explicit:
                    items.append(Evidence("reverted commit reference", f"{explicit} (declared by revert message)", explicit))
                signal_evidence.extend(items)
                signal_label = "a revert" if revert_wording else "a possible reintroduction"
                findings.append(Finding("Possible revert or reintroduction signal", f"History wording for {path} indicates {signal_label}; this does not prove that behavior was reverted or restored.", tuple(items), ("Commit messages are author-supplied; a declared revert may be partial, and reintroduction wording is not a semantic diff comparison.",), "low"))
    newest = max(all_entries, key=lambda x: int(x[2])) if all_entries else None
    evidence = [Evidence("path history", path) for path in selected]
    evidence.extend(Evidence("commit", subject, sha) for path, sha, _, subject in all_entries)
    evidence.extend(signal_evidence)
    if newest:
        evidence.append(Evidence("newest path commit", f"{newest[3]} ({newest[0]})", newest[1]))
    return Analysis("temporal-review", f"Reviewed {len(selected)} path(s) and {len(all_entries)} path-commit occurrence(s); found {len(revert_signals)} possible revert/reintroduction wording signal(s).", tuple(findings), tuple(evidence), ("History is limited to the requested commit cap and current object database; shallow clones and rewritten history can omit relevant events. Wording signals are not proof of semantic reversion or reintroduction.",), metadata={"paths": selected, "history": tuple((p, tuple(h)) for p, h in records), "newest": newest, "revert_signals": tuple(revert_signals)})
