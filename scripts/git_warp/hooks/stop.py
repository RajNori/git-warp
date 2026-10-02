"""Terse end-of-turn status summary with named path signals."""

from __future__ import annotations

from pathlib import Path
import json

from ..git import changed_paths, current_branch, repo_root

_RISK_HINTS = (
    "migration", "schema", "auth", "permission", "payment", "billing",
    "terraform", "cloudformation", "docker", "deploy", ".github/workflows",
    "package-lock", "pnpm-lock", "yarn.lock",
)


def end_report(cwd: str | Path, *, timeout: float = 8.0) -> str:
    root = repo_root(cwd=cwd, timeout=timeout)
    branch = current_branch(cwd=root, timeout=timeout) or "(detached HEAD)"
    paths = [entry.path for entry in changed_paths(cwd=root, timeout=timeout)]
    if not paths:
        return ""
    flags = [path for path in paths if any(hint in path.lower() for hint in _RISK_HINTS)]
    lines = ["Git Warp — working tree notice", f"Branch: {json.dumps(branch, ensure_ascii=True)}", f"Changed paths: {len(paths)}"]
    if flags:
        lines.append("Paths with higher-impact naming signals: " + ", ".join(json.dumps(path, ensure_ascii=True) for path in flags[:12]))
        lines.append("These are path heuristics, not a complete risk assessment.")
    lines.append("Review the diff and keep unrelated concerns in separate commits.")
    return "\n".join(lines)[:4000]
