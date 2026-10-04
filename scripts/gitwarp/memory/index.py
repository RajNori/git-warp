"""Repository memory: an incremental SQLite index of Git history (``<common-dir>/git-warp/warp.db``).

Facts only.  The index records *who touched what, when* — contribution
history — and derives co-change, churn, hotspots, revert and "introduced"
evidence from it.  Nothing here claims current ownership or causality.

Incremental model
-----------------
``index_state.head`` is the HEAD last indexed.  A new run walks
``<head>..HEAD`` when the old head is an ancestor of the new one.  If it is
not (rebase / amend / reset / switching to a diverged branch) the index is
rebuilt from scratch inside one transaction so stale commits never leak into
statistics.  A ``max_commits`` bound produces ``complete: false``; asking for
a larger bound (or none) later triggers a rebuild that extends the history.

Failure model: a corrupt or newer-schema database is quarantined
(``warp.db.corrupt``) and rebuilt; an unwritable state directory degrades to a
per-process in-memory index; every degradation is reported in ``warnings``.
"""
from __future__ import annotations

import itertools
import math
import os
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from ..core import git, storage
from ..core.config import Config, load_config
from ..core.paths import classify_path
from ..core.redact import redact
from . import schema

DB_NAME = "warp.db"
AUTO_CAP = 5000                 # default bound for automatic (query-triggered) indexing
COCHANGE_MAX_FILES = 40         # commits touching more files are not used for co-change
BATCH = 1000                    # commits per `git log` call
HOTSPOT_HALF_LIFE_DAYS = 90
NOTE_AUTHORS = "historical contribution evidence, not current ownership"
HOTSPOT_FORMULA = (
    "score = sum over non-merge commits touching the file of 0.5^(age_days/90) * (1 + log10(1 + lines_changed_in_commit)); "
    "a relative ranking signal (recent, large, repeated change), not a defect probability"
)

_LOG_FMT = "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%ct%x1f%s%x1f%b%x1f"
_REVERT_BODY = re.compile(r"This reverts commit ([0-9a-f]{40})")
_NUMSTAT = re.compile(r"^(-|\d+)\t(-|\d+)\t(.*)$", re.S)
_MEM: dict = {}  # fallback in-memory databases keyed by intended path (per process)


class WarpConnection(sqlite3.Connection):
    """sqlite3 connection that carries degradation info."""

    warnings: list
    persisted: bool
    path: str


# --------------------------------------------------------------------------- opening

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path(cwd, create: bool = False) -> Path:
    return git.state_dir(cwd, create=create) / DB_NAME


def _connect(target: str) -> WarpConnection:
    conn = sqlite3.connect(target, timeout=5.0, isolation_level=None, factory=WarpConnection)
    if target != ":memory:":
        try:
            conn.execute("PRAGMA secure_delete = ON")  # rebuilds must not leave old (possibly sensitive) text in free pages
        except sqlite3.Error:
            pass
    conn.warnings = []
    conn.persisted = target != ":memory:"
    conn.path = target
    return conn


def _quarantine(path: Path) -> Optional[str]:
    """Move an unusable db (and sidecars) aside via core/storage: private, bounded generations, never deleted.

    Returns the quarantine file name, or None when nothing could be moved (an unsafe target is left untouched).
    """
    try:
        return storage.quarantine(path.parent, path.name)
    except OSError:
        return None


def _redacted_or_same(text):
    return redact(text) if isinstance(text, str) and text else text


def open_db(cwd) -> WarpConnection:
    """Open (creating/migrating) ``warp.db``.  Never raises for storage problems: degrades to memory.

    The returned connection has ``.warnings`` (list[str]) and ``.persisted`` (bool).
    """
    warnings: list = []
    try:
        path = db_path(cwd, create=True)
        storage.prepare_file(path.parent, DB_NAME)   # private 0600 file, symlink/FIFO/socket/dir refused, BEFORE sqlite opens it
    except git.GitError:
        raise
    except OSError as e:
        return _memory_db(str(cwd), [f"state storage refused or not writable ({e}); index kept in memory for this run only"])
    key = str(path)
    with storage.file_lock(path.parent, DB_NAME):    # serialises first creation / schema init / quarantine across processes
        return _open_locked(cwd, path, key, warnings)


def _is_corruption(e: Exception) -> bool:
    """Definitive corruption only: never empty-new, locked/busy or mid-initialisation states."""
    if isinstance(e, schema.IncompatibleSchema):
        return True
    m = str(e).lower()
    return any(t in m for t in ("malformed", "not a database", "corrupt", "disk image", "encrypted", "no such table", "no such column"))


def _open_locked(cwd, path, key, warnings) -> WarpConnection:
    for attempt in (1, 2, 3):
        conn = None
        try:
            conn = _connect(key)
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            schema.migrate(conn)
            storage.tighten_sidecars(path.parent, DB_NAME)
            conn.warnings = warnings
            return conn
        except (sqlite3.DatabaseError, schema.IncompatibleSchema) as e:
            if conn is not None:
                conn.close()
            if isinstance(e, sqlite3.OperationalError) and _is_unwritable(e):
                return _memory_db(key, warnings + [f"warp.db is read-only or locked ({e}); index kept in memory for this run only"])
            if not _is_corruption(e):
                if attempt < 3:
                    time.sleep(0.05 * attempt)     # transient (busy / initialising by another process): bounded retry, no quarantine
                    continue
                return _memory_db(key, warnings + [f"warp.db busy ({e}); index kept in memory for this run only"])
            if attempt >= 2:
                return _memory_db(key, warnings + [f"warp.db unusable ({e}); index kept in memory for this run only"])
            moved = _quarantine(path)
            if moved is None:
                return _memory_db(key, warnings + [f"warp.db unusable ({e}) and could not be quarantined; index kept in memory for this run only"])
            warnings.append(f"warp.db was unreadable or incompatible ({e}); rebuilding" + f" (old file kept as {Path(moved).name})")
    return _memory_db(key, warnings)  # pragma: no cover


def _is_unwritable(e: Exception) -> bool:
    m = str(e).lower()
    return "readonly" in m or "read-only" in m or "unable to open" in m or "locked" in m or "disk i/o" in m


def _memory_db(key: str, warnings: list) -> WarpConnection:
    conn = _MEM.get(key)
    if conn is None:
        conn = _connect(":memory:")
        schema.migrate(conn)
        _MEM[key] = conn
    conn.warnings = list(warnings)
    return conn


def _quarantine_and_reopen(cwd) -> WarpConnection:
    try:
        _quarantine(db_path(cwd, create=True))
    except (OSError, git.GitError):
        pass
    conn = open_db(cwd)
    conn.warnings.append("warp.db failed during indexing; it was reset and rebuilt")
    return conn


# --------------------------------------------------------------------------- parsing

def _parse_log(stdout: str) -> list:
    out = []
    for chunk in stdout.split("\x1e")[1:]:
        f = chunk.split("\x1f", 9)
        if len(f) < 10:
            continue
        sha, parents, an, ae, ad, cd, ct, subj, body, rest = f
        files = {}
        toks = rest.lstrip("\x00\n").split("\x00")
        i, raw, nums = 0, [], {}
        while i < len(toks):
            t = toks[i]
            i += 1
            if not t:
                continue
            if t.startswith(":"):
                code = (t.split() or ["?"])[-1][:1]
                if code in "RC":
                    old, new = (toks[i] if i < len(toks) else ""), (toks[i + 1] if i + 1 < len(toks) else "")
                    i += 2
                else:
                    old, new = None, (toks[i] if i < len(toks) else "")
                    i += 1
                raw.append((code, old, new))
            else:
                m = _NUMSTAT.match(t)
                if not m:
                    continue
                a, d, p = m.groups()
                if p == "":
                    p = toks[i + 1] if i + 1 < len(toks) else ""
                    i += 2
                nums[p] = (0 if a == "-" else int(a), 0 if d == "-" else int(d))
        for code, old, new in raw:
            if not new:
                continue
            added, deleted = nums.get(new, (0, 0))
            status = {"C": "A", "T": "M"}.get(code, code if code in "AMDR" else "M")
            files[new] = (added, deleted, status, old if code in "RC" else None)
        parents_l = parents.split()
        m = _REVERT_BODY.search(body)
        is_revert = bool(m) or subj.startswith('Revert "')
        try:
            ts = int(ct)
        except ValueError:
            ts = 0
        out.append({
            "sha": sha.strip(), "parents": " ".join(parents_l), "author_name": an, "author_email": ae,
            "author_date": ad, "commit_date": cd, "commit_ts": ts, "subject": subj,
            "is_merge": 1 if len(parents_l) > 1 else 0, "is_revert": 1 if is_revert else 0,
            "reverts_sha": m.group(1) if m else None, "files": files,
        })
    return out


# --------------------------------------------------------------------------- indexing

def _state(conn) -> dict:
    return {k: v for k, v in conn.execute("SELECT key, value FROM index_state")}


def _set_state(conn, **kv) -> None:
    conn.executemany("INSERT OR REPLACE INTO index_state(key, value) VALUES (?, ?)", [(k, "" if v is None else str(v)) for k, v in kv.items()])


def _is_ancestor(old: str, new: str, cwd) -> bool:
    try:
        return git.run(["merge-base", "--is-ancestor", old, new], cwd=cwd).returncode == 0
    except git.GitError:
        return False


def _count(revs: list, cwd, timeout: float) -> int:
    r = git.run(["rev-list", "--count", *revs], cwd=cwd, timeout=timeout)
    try:
        return int(r.text) if r.ok else 0
    except ValueError:
        return 0


def _ingest(conn, cwd, revs: list, limit: Optional[int], deadline: float) -> int:
    """Index commits reachable per ``revs`` (newest first, at most ``limit``). Caller owns the transaction."""
    file_ids: dict = {}

    def fid(path: str) -> int:
        i = file_ids.get(path)
        if i is None:
            red = redact(path)
            conn.execute("INSERT OR IGNORE INTO files(path) VALUES (?)", (red,))
            i = conn.execute("SELECT id FROM files WHERE path = ?", (red,)).fetchone()[0]
            file_ids[path] = i
        return i

    total, skip = 0, 0
    while True:
        n = BATCH if limit is None else min(BATCH, limit - skip)
        if n <= 0:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise git.GitTimeout("index time budget exhausted", [], 124)
        r = git.run(
            ["log", "--no-color", "-z", "-M", "--raw", "--numstat", "--root", f"--format={_LOG_FMT}", f"-n{n}", f"--skip={skip}", *revs, "--"],
            cwd=cwd, timeout=remaining,
        )
        if not r.ok:
            raise git.GitError(f"git log failed: {r.stderr.strip()[:200]}", r.args, r.returncode, r.stderr)
        commits = _parse_log(r.stdout)
        pair_counts: dict = defaultdict(int)
        for c in commits:
            cur = conn.execute(
                "INSERT OR IGNORE INTO commits(sha, parents, author_name, author_email, author_date, commit_date, commit_ts, subject, is_merge, is_revert, reverts_sha)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (c["sha"], c["parents"], redact(c["author_name"]), c["author_email"], c["author_date"], c["commit_date"], c["commit_ts"],
                 redact(c["subject"]), c["is_merge"], c["is_revert"], c["reverts_sha"]),
            )
            if cur.rowcount != 1:
                continue
            total += 1
            rows, live = [], []
            for path, (a, d, st, old) in c["files"].items():
                rows.append((c["sha"], fid(path), a, d, st, redact(old) if old else old))
                live.append(fid(path))
                if old:  # rename/copy source: marker row (no churn) so the old path is known to be gone
                    if st == "R":
                        rows.append((c["sha"], fid(old), 0, 0, "RD", None))
            conn.executemany("INSERT OR IGNORE INTO commit_files(sha, file_id, added, deleted, status, old_path) VALUES (?,?,?,?,?,?)", rows)
            if not c["is_merge"] and 1 < len(live) <= COCHANGE_MAX_FILES:
                for a_id, b_id in itertools.combinations(sorted(set(live)), 2):
                    pair_counts[(a_id, b_id)] += 1
        if pair_counts:
            conn.executemany(
                "INSERT INTO cochanges(file_a, file_b, count) VALUES (?,?,?) ON CONFLICT(file_a, file_b) DO UPDATE SET count = count + excluded.count",
                [(a, b, n_) for (a, b), n_ in pair_counts.items()],
            )
        skip += len(commits)
        if len(commits) < n:
            break
    return total


def _refresh_authors(conn) -> None:
    agg: dict = {}
    for email, name, adate, ts in conn.execute("SELECT author_email, author_name, author_date, commit_ts FROM commits WHERE author_email != '' ORDER BY commit_ts"):
        a = agg.get(email)
        if a is None:
            agg[email] = [name, 1, adate, adate]
        else:
            a[0], a[1], a[3] = name, a[1] + 1, adate
    conn.execute("DELETE FROM authors")
    conn.executemany("INSERT INTO authors(email, name, commits, first, last) VALUES (?,?,?,?,?)", [(e, v[0], v[1], v[2], v[3]) for e, v in agg.items()])


def _refresh_refs(conn, cwd) -> None:
    r = git.run(["for-each-ref", "--format=%(refname)\x1f%(objectname)", "--count=2000", "refs/heads", "refs/tags", "refs/remotes"], cwd=cwd)
    now = _now_iso()
    rows = []
    for ln in r.lines if r.ok else []:
        name, _, sha = ln.partition("\x1f")
        rows.append((redact(name), sha, now))
    conn.execute("DELETE FROM refs")
    conn.executemany("INSERT OR REPLACE INTO refs(name, sha, updated) VALUES (?,?,?)", rows)


def ensure_indexed(cwd, max_commits: Optional[int] = None, *, time_budget: Optional[float] = None, rebuild: bool = False) -> dict:
    """Bring the index up to date with HEAD.  Never raises; problems are reported in ``warnings``.

    ``max_commits`` bounds how much history is indexed (``complete: false`` when truncated).
    ``time_budget`` (seconds) bounds the git work; on expiry nothing is changed and ``skipped`` is set.
    """
    t0 = time.monotonic()
    res: dict = {"indexed": 0, "head": None, "complete": False, "mode": "skipped", "warnings": []}
    try:
        root = git.repo_root(cwd)
    except git.GitError as e:
        res.update(error=f"git unavailable: {e}")
        return res
    if root is None:
        res["error"] = "not a git repository (or a bare repository)"
        return res
    cfg = load_config(root)
    res["warnings"].extend(cfg.warnings)
    if not cfg.memory_enabled:
        res.update(enabled=False, mode="disabled")
        res["warnings"].append("repository memory is disabled (memory_enabled: false in .claude/git-warp.local.md)")
        return res
    try:
        head = git.head_sha(cwd)
        if head is None:
            res.update(unborn=True, complete=True, mode="noop")
            res["warnings"].append("repository has no commits yet")
            return res
        res["head"] = head
        shallow = git.is_shallow(cwd)
        if max_commits is not None and max_commits <= 0:
            max_commits = None
        conn = open_db(cwd)
        res["warnings"].extend(conn.warnings)
        res["persisted"] = conn.persisted
        res["db"] = conn.path if conn.persisted else None
        deadline = time.monotonic() + (time_budget if time_budget else 600.0)
        try:
            _run(conn, cwd, head, max_commits, rebuild, shallow, deadline, res)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                res["warnings"].append(f"warp.db is locked by another process ({e}); using existing index state")
                res["mode"] = "skipped"
            elif _is_unwritable(e):
                conn.close()
                conn = _memory_db(str(db_path(cwd)), [f"warp.db is not writable ({e}); index kept in memory for this run only"])
                res["warnings"].extend(conn.warnings)
                res["persisted"], res["db"] = False, None
                _run(conn, cwd, head, max_commits, True, shallow, deadline, res)
            elif not _is_corruption(e):
                res["warnings"].append(f"warp.db changed under us ({e}); using existing index state")
                res["mode"] = "skipped"
            else:
                conn.close()
                conn = _quarantine_and_reopen(cwd)
                res["warnings"].extend(conn.warnings)
                _run(conn, cwd, head, max_commits, True, shallow, deadline, res)
        except sqlite3.DatabaseError as e:
            if not _is_corruption(e):
                res["warnings"].append(f"index database error ({e}); using existing index state")
                res["mode"] = "skipped"
            else:
                conn.close()
                conn = _quarantine_and_reopen(cwd)
                res["warnings"].extend(conn.warnings)
                res["warnings"].append(f"index database error: {e}")
                _run(conn, cwd, head, max_commits, True, shallow, deadline, res)
        res["total_commits"] = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
        st = _state(conn)
        res["complete"] = st.get("complete") == "1"
        res["shallow"] = shallow
        if shallow:
            res["warnings"].append("shallow clone: history before the shallow boundary is unavailable, so results cover only the fetched commits")
        if not res["complete"] and res["mode"] != "skipped":
            res["warnings"].append(f"index is partial: only the newest {st.get('max_commits') or '?'} commits are indexed (use `memory index --max-commits N` to go deeper)")
    except git.GitTimeout as e:
        res["mode"] = "skipped"
        res["warnings"].append(f"indexing skipped: {e}")
    except git.GitError as e:
        res["mode"] = "error"
        res["warnings"].append(f"git error during indexing: {e}")
    except sqlite3.Error as e:
        res["mode"] = "error"
        res["warnings"].append(f"index database error: {e}")
    res["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return res


def _run(conn, cwd, head, max_commits, rebuild, shallow, deadline, res) -> None:
    st = _state(conn)
    prev_head = st.get("head") or None
    prev_complete = st.get("complete") == "1"
    prev_max = int(st["max_commits"]) if st.get("max_commits", "").isdigit() else None
    left = lambda: max(1.0, deadline - time.monotonic())  # noqa: E731

    mode, reason = "incremental", None
    if rebuild:
        mode, reason = "rebuild", "requested"
    elif not prev_head:
        mode, reason = "rebuild", "first index"
    elif not prev_complete and (max_commits is None or (prev_max is not None and max_commits > prev_max)):
        mode, reason = "rebuild", "extending a partial index"
    elif prev_head == head:
        mode = "noop"
    elif not _is_ancestor(prev_head, head, cwd):
        mode, reason = "rebuild", "history rewritten or branch switched"
    elif max_commits is not None and _count([f"{prev_head}..{head}"], cwd, left()) > max_commits:
        mode, reason = "rebuild", "more new commits than the bound"

    res["mode"] = mode
    if reason:
        res["reason"] = reason
    if mode == "noop":
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        if mode == "rebuild":
            for tbl in ("cochanges", "commit_files", "commits", "files", "authors", "index_state"):
                conn.execute(f"DELETE FROM {tbl}")
            n = _ingest(conn, cwd, [head], max_commits, deadline)
            total = _count([head], cwd, left())
            complete = max_commits is None or total <= max_commits
            new_max = max_commits
        else:
            n = _ingest(conn, cwd, [head, "--not", prev_head], max_commits, deadline)
            complete, new_max = prev_complete, prev_max
        _refresh_authors(conn)
        _refresh_refs(conn, cwd)
        _set_state(conn, head=head, complete="1" if complete else "0", max_commits=new_max, indexed_at=_now_iso(),
                   shallow="1" if shallow else "0", cochange_max_files=COCHANGE_MAX_FILES, schema=schema.SCHEMA_VERSION,
                   last_mode=mode, commit_count=conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0])
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    res["indexed"] = n


def index_status(cwd) -> dict:
    """Describe the existing index without creating or modifying anything."""
    try:
        path = db_path(cwd, create=False)
    except git.GitError as e:
        return {"exists": False, "error": str(e)}
    try:
        if storage.regular_file(path.parent, DB_NAME) is None:
            return {"exists": False, "db": str(path)}
        size = os.stat(path, follow_symlinks=False).st_size
    except OSError as e:
        return {"exists": False, "db": str(path), "error": f"state storage refused: {e}"}
    out: dict = {"exists": True, "db": str(path), "size_bytes": size}
    try:
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=2.0)
        try:
            st = {k: v for k, v in conn.execute("SELECT key, value FROM index_state")}
            out.update(
                schema_version=conn.execute("PRAGMA user_version").fetchone()[0],
                head=st.get("head"), complete=st.get("complete") == "1", max_commits=st.get("max_commits") or None,
                indexed_at=st.get("indexed_at"), shallow=st.get("shallow") == "1",
                commits=conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0],
                files=conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                cochange_pairs=conn.execute("SELECT COUNT(*) FROM cochanges").fetchone()[0],
            )
            current = git.head_sha(cwd)
            out["current_head"] = current
            out["up_to_date"] = bool(current) and current == st.get("head")
        finally:
            conn.close()
    except sqlite3.Error as e:
        out["error"] = f"warp.db unreadable: {e} (run `memory index --rebuild`)"
    return out


# --------------------------------------------------------------------------- queries

def _cfg(cwd) -> Config:
    return load_config(git.repo_root(cwd))


def _file_id(conn, path: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM files WHERE path = ?", (redact(path),)).fetchone()
    return row[0] if row else None


def _alias_ids(conn, path: str) -> list:
    """File ids for ``path`` and the earlier names it was renamed from (best effort, bounded)."""
    start = _file_id(conn, path)
    if start is None:
        return []
    seen, frontier = [start], [start]
    for _ in range(25):
        nxt = []
        for fid in frontier:
            for (old,) in conn.execute("SELECT DISTINCT old_path FROM commit_files WHERE file_id = ? AND old_path IS NOT NULL", (fid,)):
                oid = _file_id(conn, old)
                if oid is not None and oid not in seen:
                    seen.append(oid)
                    nxt.append(oid)
        if not nxt:
            break
        frontier = nxt
    return seen


def _make_ignore(cfg: Config, extra_lockfiles: bool = False):
    cache: dict = {}

    def ignored(path: str) -> bool:
        v = cache.get(path)
        if v is None:
            tags = classify_path(path, cfg)
            v = "generated" in tags or (extra_lockfiles and "lockfile" in tags)
            cache[path] = v
        return v

    return ignored


def cochange(cwd, path: str, limit: int = 20) -> list:
    """Files most often changed in the same commit as ``path`` (commits touching >40 files and merges excluded)."""
    cfg = _cfg(cwd)
    conn = open_db(cwd)
    ids = _alias_ids(conn, path)
    if not ids:
        return []
    ignored = _make_ignore(cfg)
    own = {r[0] for r in conn.execute(f"SELECT path FROM files WHERE id IN ({','.join('?' * len(ids))})", ids)}
    agg: dict = defaultdict(int)
    for fid in ids:
        for other, cnt in conn.execute(
            "SELECT f.path, c.count FROM cochanges c JOIN files f ON f.id = CASE WHEN c.file_a = ?1 THEN c.file_b ELSE c.file_a END "
            "WHERE c.file_a = ?1 OR c.file_b = ?1", (fid,)
        ):
            if other not in own and not ignored(other):
                agg[other] += cnt
    total = conn.execute(
        f"SELECT COUNT(DISTINCT cf.sha) FROM commit_files cf WHERE cf.file_id IN ({','.join('?' * len(ids))}) AND cf.status != 'RD'", ids
    ).fetchone()[0] or 0
    rows = sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))[: max(0, int(limit))]
    return [{"path": p, "count": n, "ratio": round(n / total, 2) if total else None, "commits_touching_path": total} for p, n in rows]


def _aggregates(conn, cfg: Config, now: Optional[float] = None, exclude_lockfiles: bool = False) -> dict:
    """Per-live-file stats {path: {...}} over non-merge commits with decay scoring."""
    now = time.time() if now is None else now
    ignored = _make_ignore(cfg, extra_lockfiles=exclude_lockfiles)
    per: dict = {}
    q = ("SELECT f.path, c.commit_ts, cf.added + cf.deleted, cf.status, c.author_email FROM commit_files cf "
         "JOIN files f ON f.id = cf.file_id JOIN commits c ON c.sha = cf.sha WHERE c.is_merge = 0 ORDER BY c.commit_ts, c.rowid")
    for path, ts, lines, status, email in conn.execute(q):
        a = per.get(path)
        if a is None:
            if ignored(path):
                per[path] = False
                continue
            a = per[path] = {"commits": 0, "lines": 0, "score": 0.0, "recent": 0, "last_ts": 0, "gone": False, "authors": set(), "added": 0, "deleted": 0}
        elif a is False:
            continue
        a["gone"] = status in ("D", "RD")
        if status == "RD":
            continue
        age = max(0.0, (now - ts) / 86400.0)
        a["commits"] += 1
        a["lines"] += lines
        a["score"] += math.pow(0.5, age / HOTSPOT_HALF_LIFE_DAYS) * (1 + math.log10(1 + lines))
        a["recent"] += 1 if age <= HOTSPOT_HALF_LIFE_DAYS else 0
        a["last_ts"] = max(a["last_ts"], ts)
        a["authors"].add(email)
    return {p: a for p, a in per.items() if a and not a["gone"]}


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else ""


def hotspots(cwd, limit: int = 20, now: Optional[float] = None) -> list:
    """Files ranked by recency-weighted churn (see :data:`HOTSPOT_FORMULA`).  Generated/vendored/lock files and deleted files excluded."""
    cfg = _cfg(cwd)
    per = _aggregates(open_db(cwd), cfg, now, exclude_lockfiles=True)
    rows = sorted(per.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["commits"], kv[0]))[: max(0, int(limit))]
    return [{"path": p, "score": round(a["score"], 2), "commits": a["commits"], "recent_commits_90d": a["recent"],
             "lines_changed": a["lines"], "authors": len(a["authors"]), "last_changed": _fmt_ts(a["last_ts"])} for p, a in rows]


def churn(cwd, path_prefix: Optional[str] = None, limit: int = 20) -> list:
    """Files (optionally under ``path_prefix``) ranked by total lines changed over indexed history."""
    cfg = _cfg(cwd)
    per = _aggregates(open_db(cwd), cfg)
    pre = (path_prefix or "").lstrip("./") if path_prefix else ""
    items = [(p, a) for p, a in per.items() if not pre or p == pre or p.startswith(pre if pre.endswith("/") else pre + "/")]
    items.sort(key=lambda kv: (-kv[1]["lines"], -kv[1]["commits"], kv[0]))
    return [{"path": p, "commits": a["commits"], "lines_changed": a["lines"], "authors": len(a["authors"]), "last_changed": _fmt_ts(a["last_ts"])}
            for p, a in items[: max(0, int(limit))]]


def file_commits(cwd, path: str, limit: int = 50) -> list:
    """Commits that touched ``path`` (following renames best-effort), newest first."""
    conn = open_db(cwd)
    ids = _alias_ids(conn, path)
    if not ids:
        return []
    q = (f"SELECT c.sha, c.commit_date, c.author_name, c.subject, cf.added, cf.deleted, cf.status, f.path, cf.old_path, c.is_merge "
         f"FROM commit_files cf JOIN commits c ON c.sha = cf.sha JOIN files f ON f.id = cf.file_id "
         f"WHERE cf.file_id IN ({','.join('?' * len(ids))}) AND cf.status != 'RD' ORDER BY c.commit_ts DESC, c.rowid LIMIT ?")
    return [{"sha": s, "short": s[:8], "date": d, "author": an, "subject": sub, "added": a, "deleted": dl, "status": st, "path_at_commit": p, "renamed_from": old}
            for s, d, an, sub, a, dl, st, p, old, _m in conn.execute(q, [*ids, int(limit)])]


def introduced(cwd, path: str) -> dict:
    """Earliest indexed commit that added ``path`` (renames followed best-effort)."""
    conn = open_db(cwd)
    ids = _alias_ids(conn, path)
    st = _state(conn)
    base = {"path": path, "complete_history": st.get("complete") == "1" and st.get("shallow") != "1"}
    if not ids:
        return {**base, "found": False, "reason": "path not present in indexed history"}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT c.sha, c.commit_date, c.author_name, c.subject, cf.status, f.path FROM commit_files cf JOIN commits c ON c.sha = cf.sha "
        f"JOIN files f ON f.id = cf.file_id WHERE cf.file_id IN ({marks}) AND cf.status != 'RD' ORDER BY c.commit_ts ASC, c.rowid DESC", ids).fetchall()
    if not rows:
        return {**base, "found": False, "reason": "no commits recorded for path"}
    added = [r for r in rows if r[4] == "A"]
    r = added[0] if added else rows[0]
    out = {**base, "found": True, "sha": r[0], "short": r[0][:8], "date": r[1], "author": r[2], "subject": r[3],
           "path_at_commit": r[5], "evidence": "commit that added the file" if added else "earliest indexed commit touching the file (history may be truncated)",
           "renames_followed": [p for (p,) in conn.execute(f"SELECT path FROM files WHERE id IN ({marks}) AND path != ?", [*ids, path])]}
    if not base["complete_history"]:
        out["warning"] = "index is partial or shallow; an older introducing commit may exist outside it"
    return out


def reverts(cwd, limit: int = 20) -> dict:
    """Revert commits and files that appear in several reverts."""
    cfg = _cfg(cwd)
    conn = open_db(cwd)
    ignored = _make_ignore(cfg)
    items = []
    for sha, date, subj, target in conn.execute("SELECT sha, commit_date, subject, reverts_sha FROM commits WHERE is_revert = 1 ORDER BY commit_ts DESC LIMIT ?", (int(limit),)):
        tsub = None
        if target:
            row = conn.execute("SELECT subject FROM commits WHERE sha = ?", (target,)).fetchone()
            tsub = row[0] if row else None
        items.append({"sha": sha, "short": sha[:8], "date": date, "subject": subj, "reverts": target, "reverted_subject": tsub})
    total = conn.execute("SELECT COUNT(*) FROM commits WHERE is_revert = 1").fetchone()[0]
    rep = []
    for path, n, last in conn.execute(
        "SELECT f.path, COUNT(DISTINCT cf.sha), MAX(c.commit_ts) FROM commit_files cf JOIN commits c ON c.sha = cf.sha JOIN files f ON f.id = cf.file_id "
        "WHERE c.is_revert = 1 AND cf.status != 'RD' GROUP BY f.id HAVING COUNT(DISTINCT cf.sha) >= 2 ORDER BY 2 DESC, f.path LIMIT 200"):
        if not ignored(path):
            rep.append({"path": path, "revert_commits": n, "last_revert": _fmt_ts(last)})
    return {"revert_commits_total": total, "reverts": items, "repeatedly_reverted_files": rep[: int(limit)],
            "note": "detected from 'Revert \"...\"' subjects and 'This reverts commit <sha>' bodies; reverts made by other wording are not seen"}


def authors(cwd, path: str) -> dict:
    """Who contributed to ``path`` (file, or directory prefix).  Contribution history only."""
    conn = open_db(cwd)
    ids = _alias_ids(conn, path)
    scope = "file"
    if not ids:
        pre = path.strip("/") + "/"
        ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE substr(path, 1, ?) = ?", (len(pre), pre))]
        scope = "directory"
    out = {"path": path, "scope": scope, "note": NOTE_AUTHORS, "authors": [], "total_commits": 0}
    if not ids:
        out["found"] = False
        return out
    rows = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows += conn.execute(
            f"SELECT c.author_email, c.author_name, c.sha, cf.added + cf.deleted, c.commit_ts FROM commit_files cf JOIN commits c ON c.sha = cf.sha "
            f"WHERE cf.file_id IN ({','.join('?' * len(chunk))}) AND cf.status != 'RD' AND c.is_merge = 0", chunk).fetchall()
    agg: dict = {}
    seen_all = set()
    for email, name, sha, lines, ts in rows:
        seen_all.add(sha)
        a = agg.setdefault(email, {"name": name, "email": email, "shas": set(), "lines": 0, "first": ts, "last": ts})
        a["name"] = name
        a["shas"].add(sha)
        a["lines"] += lines
        a["first"], a["last"] = min(a["first"], ts), max(a["last"], ts)
    out["found"] = True
    out["total_commits"] = len(seen_all)
    out["authors"] = [{"name": a["name"], "email": a["email"], "commits": len(a["shas"]), "lines_changed": a["lines"],
                       "first": _fmt_ts(a["first"]), "last": _fmt_ts(a["last"])}
                      for a in sorted(agg.values(), key=lambda a: (-len(a["shas"]), a["email"]))[:20]]
    return out


# --------------------------------------------------------------------------- sessions

def record_session(cwd, session_id: str, branch: Optional[str], head: Optional[str]) -> bool:
    """Insert a session row (idempotent). Returns False when the DB is not persistent/available."""
    if not session_id:
        return False
    try:
        conn = open_db(cwd)
        if not conn.persisted:
            return False
        conn.execute("INSERT OR IGNORE INTO sessions(session_id, started, branch, head) VALUES (?,?,?,?)", (redact(str(session_id)[:128]), _now_iso(), _redacted_or_same(branch), head))
        conn.execute("INSERT INTO events(ts, session_id, kind, detail) VALUES (?,?,?,?)", (_now_iso(), redact(str(session_id)[:128]), "session_start", _redacted_or_same(branch) or "detached"))
        conn.close()
        return True
    except (sqlite3.Error, git.GitError, OSError):
        return False


def list_sessions(cwd, limit: int = 20) -> list:
    try:
        path = db_path(cwd, create=False)
        if storage.regular_file(path.parent, DB_NAME) is None:
            return []
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=2.0)
        try:
            return [{"session_id": s, "started": st, "branch": b, "head": h}
                    for s, st, b, h in conn.execute("SELECT session_id, started, branch, head FROM sessions ORDER BY started DESC LIMIT ?", (int(limit),))]
        finally:
            conn.close()
    except (sqlite3.Error, git.GitError, OSError):
        return []
