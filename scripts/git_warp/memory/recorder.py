"""Allowlisted, redacted local flight recorder."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..git import changed_paths, current_branch, head_commit, repo_root
from ..models import GitError
from .storage import git_warp_directory, require_regular_file

_SECRET_PATTERNS = (
    re.compile(r"(?i)(\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)(https?://[^:/\s]+:)([^@/\s]+)(@)"),
)
_SENSITIVE_PATH = re.compile(r"(?i)(^|[/_.-])(\.env(?:\.|$)|secrets?|credentials?)([/_.-]|$)")
_MAX_PATHS = 100


def redact_text(value: str, *, limit: int = 512) -> str:
    """Redact common credential forms and bound arbitrary untrusted text."""
    result = value[: max(0, limit)]
    result = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", result)
    for pattern in _SECRET_PATTERNS[1:3]:
        result = pattern.sub("[REDACTED]", result)
    result = _SECRET_PATTERNS[3].sub(r"\1[REDACTED]\3", result)
    return result


def _safe_path(path: str) -> str | None:
    clean = path.replace("\x00", "")
    if not clean or _SENSITIVE_PATH.search(clean):
        return None
    return redact_text(clean, limit=256)


def _tool_category(tool_name: Any) -> str:
    name = tool_name if isinstance(tool_name, str) else ""
    if name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return "file_edit"
    if name in {"Bash", "Terminal"}:
        return "shell"
    if name in {"Read", "Grep", "Glob"}:
        return "read"
    if name in {"Task", "Agent"}:
        return "agent"
    return "other"


def _record_path(cwd: str | Path, timeout: float) -> Path:
    return git_warp_directory(cwd, timeout=timeout) / "flight-recorder.jsonl"


def record_event(
    cwd: str | Path,
    event: Mapping[str, Any],
    *,
    timeout: float = 5.0,
) -> bool:
    """Append a small metadata-only event; return false on unavailable repo."""
    try:
        root = repo_root(cwd=cwd, timeout=timeout)
        record_file = _record_path(root, timeout)
        branch = current_branch(cwd=root, timeout=timeout)
        head = head_commit(cwd=root, timeout=timeout)
        changed = changed_paths(cwd=root, timeout=timeout)
    except (GitError, OSError, RuntimeError, ValueError):
        # Recording is best-effort; read failures must not block the user's edit.
        return False

    event_name = event.get("hook_event_name")
    if not isinstance(event_name, str) or not re.fullmatch(r"[A-Za-z]{1,40}", event_name):
        event_name = "PostToolUse"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event_name,
        "tool_category": _tool_category(event.get("tool_name")),
        "branch": redact_text(branch or "(detached HEAD)", limit=160),
        "head": head,
        "changed_paths": [
            safe
            for item in changed
            for path in (item.path, item.original_path)
            if path is not None
            if (safe := _safe_path(path)) is not None
        ][:_MAX_PATHS],
    }
    encoded = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > 8192:
        payload["changed_paths"] = payload["changed_paths"][:20]
        encoded = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        require_regular_file(record_file)
        fd = os.open(record_file, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.fchmod(fd, 0o600)
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    return False
                remaining = remaining[written:]
        finally:
            os.close(fd)
    except OSError:
        return False
    return True
