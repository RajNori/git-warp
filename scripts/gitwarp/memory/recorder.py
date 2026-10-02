"""Flight recorder: a small, redacted, append-only JSONL log of what a session did.

Stored only under ``<common-dir>/git-warp/flight-recorder.jsonl``.  Records
metadata (timestamps, tool name/category, file *paths*, redacted+truncated
shell commands, branch, head).  It never stores file contents, edit strings,
prompts, tool responses or environment.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..core import git
from ..core.config import load_config
from ..core.redact import redact, redact_obj
from . import state

try:  # POSIX advisory locking; absent on Windows (we then rely on O_APPEND alone)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

RECORDER_FILE = "flight-recorder.jsonl"
MAX_BYTES = 5 * 1024 * 1024
COMMAND_MAX = 300
COMPACT_INTERVAL_S = 24 * 3600
OUTSIDE = "<outside-repo>"

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash"}
_GIT_CMD = re.compile(r"(^|[\s;&|(])git(\s|$)")
_TEST_OUTCOMES = {"pass", "passed", "fail", "failed", "error", "skipped"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def categorize(tool: str, command: Optional[str] = None) -> str:
    if tool in EDIT_TOOLS:
        return "edit"
    if tool in SHELL_TOOLS:
        return "git" if _GIT_CMD.search(command or "") else "shell"
    return "other"


def repo_context(cwd) -> Optional[dict]:
    """One cheap git call for (root, common dir) plus branch/head.  None when not in a work tree."""
    try:
        r = git.run(["rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir"], cwd=cwd, timeout=5)
        lines = r.text.splitlines()
        if not r.ok or len(lines) < 2:
            return None
        root, common = lines[0], lines[1]
        b = git.run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd, timeout=5)
        h = git.run(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=cwd, timeout=5)
    except git.GitError:
        return None
    return {"root": root, "state_dir": Path(common) / "git-warp", "branch": b.text if b.ok and b.text else None,
            "head": h.text[:12] if h.ok and h.text else None}


def _rel(path_str, root: str, cwd) -> str:
    """Repo-relative POSIX path, or ``<outside-repo>``."""
    try:
        p = Path(os.path.expanduser(str(path_str)))
        if not p.is_absolute():
            p = Path(cwd or root) / p
        real, rroot = Path(os.path.realpath(p)), Path(os.path.realpath(root))
        return real.relative_to(rroot).as_posix()
    except (ValueError, OSError):
        return OUTSIDE


def _paths_of(tool_input: dict, root: str, cwd) -> list:
    out = []
    for key in ("file_path", "notebook_path", "path"):
        v = tool_input.get(key)
        if isinstance(v, str) and v:
            out.append(_rel(v, root, cwd))
            break
    return out


def build_record(event: dict, ctx: dict, cwd) -> dict:
    """Pure transformation of a hook event into a recorder record (no I/O)."""
    tool = str(event.get("tool_name") or "")
    tin = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    command = tin.get("command") if isinstance(tin.get("command"), str) else None
    rec: dict = {
        "ts": _iso(_now()),
        "session_id": str(event.get("session_id") or "")[:128],
        "hook_event": str(event.get("hook_event_name") or event.get("hook_event") or "")[:40],
    }
    if tool:
        rec["tool"] = {"name": tool[:60], "category": categorize(tool, command)}
    if tool in EDIT_TOOLS:
        rec["files"] = _paths_of(tin, ctx["root"], cwd)
    if tool in SHELL_TOOLS and command is not None:
        c = redact(command).replace("\n", " ⏎ ")
        rec["command"] = c if len(c) <= COMMAND_MAX else c[:COMMAND_MAX] + "…"
    if event.get("source") in ("startup", "resume", "clear", "compact"):
        rec["source"] = event["source"]
    rec["branch"] = ctx.get("branch")
    rec["head"] = ctx.get("head")
    if isinstance(event.get("dirty_count"), int) and not isinstance(event.get("dirty_count"), bool):
        rec["dirty_count"] = event["dirty_count"]
    outcome = event.get("test_outcome")
    if isinstance(outcome, str) and outcome.lower() in _TEST_OUTCOMES:
        rec["test_outcome"] = outcome.lower()
    return redact_obj(rec, max_str=COMMAND_MAX + 8)


# --------------------------------------------------------------------------- file I/O

def _open_locked(path: Path):
    """Open for append and take an exclusive lock on the *current* file (retry if it was rotated meanwhile)."""
    for _ in range(4):
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        if fcntl is None:
            return fd
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.fstat(fd).st_ino == os.stat(str(path)).st_ino:
                return fd
        except OSError:
            pass
        os.close(fd)
    return os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)


def _append(path: Path, line: bytes) -> None:
    fd = _open_locked(path)
    try:
        if os.fstat(fd).st_size + len(line) > MAX_BYTES:
            os.replace(str(path), str(path) + ".1")  # rotate: keep exactly one previous generation
            os.close(fd)
            fd = _open_locked(path)
        os.write(fd, line)  # single write on an O_APPEND fd
    finally:
        try:
            os.close(fd)  # releases flock
        except OSError:
            pass


def _parse_ts(s) -> Optional[datetime]:
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def compact(sdir: Path, retention_days: int, now: Optional[datetime] = None) -> int:
    """Drop records older than the retention window (and unparseable lines).  Returns lines removed."""
    path = sdir / RECORDER_FILE
    cutoff = (now or _now()) - timedelta(days=retention_days)
    removed = 0
    fd = _open_locked(path)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        keep = []
        for ln in data.splitlines():
            try:
                ts = _parse_ts(json.loads(ln).get("ts"))
            except ValueError:
                ts = None
            if ts is not None and ts >= cutoff:
                keep.append(ln)
            else:
                removed += 1
        if removed:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(b"".join(k + b"\n" for k in keep))
            os.replace(str(tmp), str(path))
    finally:
        os.close(fd)
    old = Path(str(path) + ".1")
    try:
        if old.exists() and datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
            old.unlink()
    except OSError:
        pass
    return removed


def _maybe_compact(sdir: Path, retention_days: int) -> None:
    st = state.read(sdir)
    last = st.get("last_compaction")
    if isinstance(last, (int, float)) and time.time() - last < COMPACT_INTERVAL_S:
        return
    state.update(sdir, last_compaction=time.time())  # claim first so concurrent hooks skip
    compact(sdir, retention_days)


# --------------------------------------------------------------------------- public API

def record(cwd, event: dict) -> bool:
    """Append one redacted record. Returns True if written.  Honours ``recorder_enabled``; never raises."""
    try:
        if not isinstance(event, dict):
            return False
        ctx = repo_context(cwd)
        if ctx is None:
            return False
        cfg = load_config(ctx["root"])
        if not cfg.recorder_enabled or cfg.recorder_retention_days <= 0:
            return False
        sdir: Path = ctx["state_dir"]
        sdir.mkdir(parents=True, exist_ok=True)
        rec = build_record(event, ctx, cwd)
        line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        _append(sdir / RECORDER_FILE, line)
        sid = rec.get("session_id")
        if sid:
            st = state.read(sdir)
            if st.get("last_session") != sid:
                state.update(sdir, last_session=sid)
        _maybe_compact(sdir, cfg.recorder_retention_days)
        return True
    except Exception:  # hooks must never crash Claude
        return False


def _read_lines(sdir: Path) -> list:
    out = []
    for name in (RECORDER_FILE + ".1", RECORDER_FILE):
        try:
            data = (sdir / name).read_bytes()
        except OSError:
            continue
        for ln in data.splitlines():
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def tail(cwd, n: int = 50) -> list:
    """The last ``n`` records (oldest first)."""
    try:
        recs = _read_lines(git.state_dir(cwd))
    except git.GitError:
        return []
    return recs[-max(0, int(n)):] if n else []


def summarize_sessions(cwd, limit: int = 10, session_id: Optional[str] = None) -> list:
    """Per-session activity summaries, most recent first."""
    try:
        recs = _read_lines(git.state_dir(cwd))
    except git.GitError:
        return []
    groups: dict = {}
    for r in recs:
        sid = r.get("session_id") or "(unknown)"
        if session_id and sid != session_id:
            continue
        g = groups.setdefault(sid, {"session_id": sid, "first": r.get("ts"), "last": r.get("ts"), "events": 0, "edits": 0, "shell": 0, "git_commands": 0,
                                    "files": {}, "branches": [], "heads": [], "tests": {}, "git_command_samples": []})
        g["events"] += 1
        g["last"] = r.get("ts") or g["last"]
        cat = (r.get("tool") or {}).get("category")
        if cat == "edit":
            g["edits"] += 1
            for f in r.get("files") or []:
                g["files"][f] = g["files"].get(f, 0) + 1
        elif cat == "shell":
            g["shell"] += 1
        elif cat == "git":
            g["git_commands"] += 1
            if len(g["git_command_samples"]) < 5 and r.get("command"):
                g["git_command_samples"].append(r["command"][:120])
        b = r.get("branch")
        if b and b not in g["branches"]:
            g["branches"].append(b)
        h = r.get("head")
        if h and h not in g["heads"]:
            g["heads"].append(h)
        if r.get("test_outcome"):
            g["tests"][r["test_outcome"]] = g["tests"].get(r["test_outcome"], 0) + 1
    out = []
    for g in groups.values():
        files = sorted(g["files"].items(), key=lambda kv: (-kv[1], kv[0]))
        g["files_touched"] = len(files)
        g["top_files"] = [{"path": p, "edits": n} for p, n in files[:8]]
        del g["files"]
        out.append(g)
    out.sort(key=lambda g: g["last"] or "", reverse=True)
    return out[: max(0, int(limit))]


def stats(cwd) -> dict:
    try:
        sdir = git.state_dir(cwd)
    except git.GitError:
        return {"exists": False}
    p = sdir / RECORDER_FILE
    if not p.exists():
        return {"exists": False, "path": str(p)}
    recs = _read_lines(sdir)
    return {"exists": True, "path": str(p), "size_bytes": p.stat().st_size, "records": len(recs),
            "oldest": recs[0].get("ts") if recs else None, "newest": recs[-1].get("ts") if recs else None}
