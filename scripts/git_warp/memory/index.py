"""Incremental SQLite index of Git commit metadata and changed paths."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..git import head_commit, repo_root, run_git
from .recorder import redact_text, _safe_path
from .storage import git_warp_directory, require_regular_file


class RepositoryIndex:
    """Read-only history index stored below the repository's Git common dir."""

    def __init__(self, cwd: str | Path, *, timeout: float = 5.0, db_path: str | Path | None = None) -> None:
        self.root = repo_root(cwd=cwd, timeout=timeout)
        self.timeout = timeout
        data_dir = git_warp_directory(self.root, timeout=timeout)
        self.db_path = Path(db_path) if db_path else data_dir / "warp.db"
        require_regular_file(self.db_path)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        require_regular_file(self.db_path)
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS commits (
                    sha TEXT PRIMARY KEY,
                    author TEXT NOT NULL,
                    authored_at TEXT NOT NULL,
                    subject TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commit_files (
                    sha TEXT NOT NULL REFERENCES commits(sha) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    PRIMARY KEY (sha, path)
                );
                CREATE INDEX IF NOT EXISTS commit_files_by_path ON commit_files(path);
                CREATE TABLE IF NOT EXISTS index_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _state(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM index_state WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def _put_state(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO index_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _commit_shas(self, last: str | None) -> list[str]:
        if last:
            ancestor = run_git(("merge-base", "--is-ancestor", last, "HEAD"), cwd=self.root, timeout=self.timeout)
            if ancestor.returncode == 0:
                args = ("rev-list", "--reverse", "HEAD", f"^{last}")
            else:
                # Branch movement or a rewritten history requires checking the
                # reachable commit set, but the database still inserts only
                # missing SHAs and never discards prior evidence.
                args = ("rev-list", "--reverse", "HEAD")
        else:
            args = ("rev-list", "--reverse", "HEAD")
        result = run_git(args, cwd=self.root, timeout=self.timeout)
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line]

    def _indexed_shas(self) -> set[str]:
        with self._connect() as db:
            return {str(row[0]) for row in db.execute("SELECT sha FROM commits")}

    def index_history(self, *, batch_size: int = 200) -> dict[str, int | str | None]:
        """Index up to batch_size commits; repeated calls resume incrementally."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        head = head_commit(cwd=self.root, timeout=self.timeout)
        if head is None:
            return {"indexed": 0, "remaining": 0, "head": None}
        last = self._state("last_indexed_sha")
        shas = self._commit_shas(last)
        indexed = self._indexed_shas()
        pending = [sha for sha in shas if sha not in indexed]
        batch = pending[:batch_size]
        if not batch:
            self._put_state("last_indexed_sha", head)
            return {"indexed": 0, "remaining": 0, "head": head}

        with self._connect() as db:
            for sha in batch:
                meta = run_git(
                    ("show", "-s", "--format=%an%x00%aI%x00%s", "--end-of-options", sha),
                    cwd=self.root,
                    timeout=self.timeout,
                )
                if meta.returncode != 0:
                    continue
                fields = meta.stdout.rstrip("\n").split("\x00", 2)
                if len(fields) != 3:
                    continue
                author, authored_at, subject = fields
                author = redact_text(author, limit=200)
                authored_at = redact_text(authored_at, limit=80)
                subject = redact_text(subject, limit=512)
                db.execute(
                    "INSERT OR IGNORE INTO commits(sha, author, authored_at, subject) VALUES(?, ?, ?, ?)",
                    (sha, author, authored_at, subject),
                )
                files = run_git(
                    ("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", "--end-of-options", sha),
                    cwd=self.root,
                    timeout=self.timeout,
                )
                if files.returncode == 0:
                    for path in files.stdout.split("\x00"):
                        safe = _safe_path(path)
                        if safe:
                            db.execute(
                                "INSERT OR IGNORE INTO commit_files(sha, path) VALUES(?, ?)",
                                (sha, safe),
                            )
            db.execute(
                "INSERT INTO index_state(key, value) VALUES('last_indexed_sha', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (batch[-1],),
            )
        return {"indexed": len(batch), "remaining": max(0, len(pending) - len(batch)), "head": head}

    def hotspots(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT path, COUNT(*) AS changes FROM commit_files GROUP BY path "
                "ORDER BY changes DESC, path LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [{"path": row[0], "commits": row[1]} for row in rows]

    def cochanges(self, path: str, *, limit: int = 20) -> list[dict[str, Any]]:
        safe = _safe_path(path)
        if not safe:
            return []
        with self._connect() as db:
            rows = db.execute(
                """SELECT other.path, COUNT(*) AS shared_commits
                   FROM commit_files AS target
                   JOIN commit_files AS other ON other.sha = target.sha
                   WHERE target.path = ? AND other.path <> ?
                   GROUP BY other.path
                   ORDER BY shared_commits DESC, other.path
                   LIMIT ?""",
                (safe, safe, max(1, min(limit, 200))),
            ).fetchall()
        return [{"path": row[0], "shared_commits": row[1]} for row in rows]

    def commit_history(self, path: str, *, limit: int = 20) -> list[dict[str, Any]]:
        safe = _safe_path(path)
        if not safe:
            return []
        with self._connect() as db:
            rows = db.execute(
                """SELECT commits.sha, commits.author, commits.authored_at, commits.subject
                   FROM commits JOIN commit_files USING(sha)
                   WHERE commit_files.path = ?
                   ORDER BY commits.authored_at DESC LIMIT ?""",
                (safe, max(1, min(limit, 200))),
            ).fetchall()
        return [
            {"sha": row[0], "author": row[1], "authored_at": row[2], "subject": row[3]}
            for row in rows
        ]
