"""SQLite schema + forward migrations for ``warp.db`` (versioned with ``PRAGMA user_version``)."""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2


class IncompatibleSchema(Exception):
    """The database was written by a newer Git Warp (or is not ours)."""


_V1 = [
    """CREATE TABLE commits (
        sha TEXT PRIMARY KEY, parents TEXT NOT NULL DEFAULT '',
        author_name TEXT, author_email TEXT, author_date TEXT,
        commit_date TEXT, commit_ts INTEGER NOT NULL DEFAULT 0,
        subject TEXT, is_merge INTEGER NOT NULL DEFAULT 0,
        is_revert INTEGER NOT NULL DEFAULT 0, reverts_sha TEXT)""",
    "CREATE INDEX idx_commits_ts ON commits(commit_ts)",
    "CREATE INDEX idx_commits_revert ON commits(is_revert)",
    "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE)",
    """CREATE TABLE commit_files (
        sha TEXT NOT NULL, file_id INTEGER NOT NULL, added INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'M', old_path TEXT,
        PRIMARY KEY (sha, file_id))""",
    "CREATE INDEX idx_cf_file ON commit_files(file_id)",
    "CREATE TABLE cochanges (file_a INTEGER NOT NULL, file_b INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (file_a, file_b))",
    "CREATE INDEX idx_cochange_b ON cochanges(file_b)",
    "CREATE TABLE authors (email TEXT PRIMARY KEY, name TEXT, commits INTEGER, first TEXT, last TEXT)",
    "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started TEXT, branch TEXT, head TEXT)",
    "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, kind TEXT, detail TEXT)",
    "CREATE TABLE refs (name TEXT PRIMARY KEY, sha TEXT, updated TEXT)",
    "CREATE TABLE index_state (key TEXT PRIMARY KEY, value TEXT)",
    # Per-file aggregate over non-merge commits; rename-source rows ('RD') carry no churn.
    """CREATE VIEW file_stats AS
       SELECT f.id AS file_id, f.path AS path, COUNT(*) AS commits,
              SUM(cf.added) AS added, SUM(cf.deleted) AS deleted, MAX(c.commit_ts) AS last_ts
       FROM commit_files cf JOIN files f ON f.id = cf.file_id JOIN commits c ON c.sha = cf.sha
       WHERE cf.status != 'RD' GROUP BY f.id""",
]

# version -> statements that upgrade (version-1) -> version.  Add future migrations here.
# v2 (privacy): v1 stored raw commit text.  Everything in the index is derived from Git, so the upgrade empties it (the next
# run rebuilds with redaction applied) and VACUUMs so no old text survives in free pages.  sessions/events hold no commit text.
_V2 = [f"DELETE FROM {t}" for t in ("cochanges", "commit_files", "commits", "files", "authors", "refs", "index_state")]
MIGRATIONS = {1: _V1, 2: _V2}


def migrate(conn: sqlite3.Connection) -> int:
    """Bring ``conn`` up to :data:`SCHEMA_VERSION`. Raises :class:`IncompatibleSchema` if newer."""
    cur = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur > SCHEMA_VERSION:
        raise IncompatibleSchema(f"warp.db schema v{cur} is newer than supported v{SCHEMA_VERSION}")
    if cur == SCHEMA_VERSION:
        return cur
    if cur == 0:
        # a non-empty db without our version stamp is not ours
        n = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        if n:
            raise IncompatibleSchema("warp.db has unknown contents")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for v in range(cur + 1, SCHEMA_VERSION + 1):
            for stmt in MIGRATIONS[v]:
                conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if cur >= 1:
        try:
            conn.execute("VACUUM")
        except sqlite3.Error:
            pass
    return SCHEMA_VERSION
