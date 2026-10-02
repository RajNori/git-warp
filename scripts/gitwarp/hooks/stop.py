"""Stop hook: concise end-of-turn change report (``systemMessage``), quiet when nothing changed.

Never blocks the stop (no ``decision: block``) and returns immediately when
``stop_hook_active`` is set, so it cannot loop.
"""
from __future__ import annotations

import hashlib
import os
import sys

from ..core import git
from ..core.config import load_config
from ..core.output import read_hook_event, write_hook
from ..memory import recorder, snapshot, state


def build_report(cwd, cfg, st: dict) -> str:
    branch = git.current_branch(cwd)
    head = git.head_sha(cwd)
    stats = snapshot.numstat(cwd)
    op = git.repo_operation(cwd)
    risk = snapshot.assess_risk(st["entries"], stats, cfg, op)
    untracked = [e.path for e in st["entries"] if e.untracked]
    where = f"branch {snapshot.clean(branch, 60)}" if branch else "detached HEAD"
    counts = f"{st['staged']} staged, {st['unstaged']} unstaged, {st['untracked']} untracked" + (f", {st['conflicted']} conflicted" if st["conflicted"] else "")
    lines = [
        f"Git Warp: {where} @ {head[:8] if head else 'no commits'}",
        f"Changed: {st['total']} path(s) ({counts})",
        f"Risk: {risk['level']}" + (" - " + "; ".join(risk["drivers"]) if risk["drivers"] else " - no risk drivers detected (small, ordinary change)"),
    ]
    sl = snapshot.stat_lines(stats, untracked)
    if sl:
        lines.append("Diffstat vs HEAD:")
        lines += sl
    lines.append(snapshot.next_step(risk["level"], st))
    return "\n".join(lines)


def main() -> int:
    try:
        event = read_hook_event()
        if not event:  # malformed/empty stdin: no side effects, no output
            return 0
        if event.get("stop_hook_active"):
            return 0
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and event.get("cwd") else os.getcwd()
        root = git.repo_root(cwd)
        if root is None:
            return 0
        cfg = load_config(root)
        sdir = git.state_dir(cwd)
        st = snapshot.status(cwd, timeout=8.0, untracked="all")
        if st is None:
            return 0
        prior = state.read(sdir)
        if st["total"] == 0:
            if prior.get("last_report_hash"):
                state.update(sdir, last_report_hash="")
            return 0
        report = build_report(cwd, cfg, st)
        digest = hashlib.sha256(f"{root}\n{report}".encode("utf-8", "replace")).hexdigest()[:16]
        if prior.get("last_report_hash") == digest:
            return 0
        state.update(sdir, last_report_hash=digest)
        write_hook({"systemMessage": report})
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
