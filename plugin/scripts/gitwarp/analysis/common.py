"""Helpers shared by X-Ray and PR analysis (bounded, read-only, JSON-friendly)."""
from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Callable, Iterable, Optional

from gitwarp.core import git
from gitwarp.core.config import Config, load_config
from gitwarp.core.paths import classify_path

PATH_CAP = 50
CLUSTER_PATH_CAP = 25
MAX_CLUSTER_ENTRIES = 2000


class Ctx:
    """Per-run context: repo root, config and a warnings sink."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.cfg: Config = load_config(self.root)
        self.warnings: list = [f"config: {w}" for w in self.cfg.warnings]
        self._tags: dict = {}

    def safe(self, label: str, fn: Callable, default=None):
        """Run ``fn``; on Git errors/timeouts record a warning and return ``default``."""
        try:
            return fn()
        except git.GitTimeout as e:
            self.warnings.append(f"{label}: timed out ({e}); result is partial")
        except (git.GitError, ValueError, OSError) as e:
            self.warnings.append(f"{label}: {str(e)[:200]}")
        return default

    def tags(self, path: str) -> set:
        t = self._tags.get(path)
        if t is None:
            t = self._tags[path] = classify_path(path, self.cfg)
        return t


def cap(items: Iterable, n: int = PATH_CAP) -> dict:
    items = list(items)
    return {"count": len(items), "paths": items[:n], "truncated": len(items) > n}


def uniq(seq: Iterable) -> list:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


_EXAMPLE_ENV = {".env.example", ".env.sample", ".env.template", ".env.dist", ".env.defaults"}


def is_secret_file(path: str, tags: set) -> bool:
    """secret-file tag, excluding well-known example/template env files."""
    return "secret-file" in tags and posixpath.basename(path) not in _EXAMPLE_ENV


# ---------------------------------------------------------------- dependency drift
LOCK_FOR = {
    "package.json": ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock"],
    "pyproject.toml": ["poetry.lock", "uv.lock"],
    "Pipfile": ["Pipfile.lock"],
    "Cargo.toml": ["Cargo.lock"],
    "Gemfile": ["Gemfile.lock"],
    "go.mod": ["go.sum"],
    "composer.json": ["composer.lock"],
}
MANIFEST_FOR = {lock: man for man, locks in LOCK_FOR.items() for lock in locks}


def _join(d: str, name: str) -> str:
    return f"{d}/{name}" if d else name


def dependency_drift(changed: Iterable[str], tracked: set) -> dict:
    """Heuristic: manifest changed with an existing lockfile untouched, or lockfile changed without its manifest."""
    changed = set(changed)
    manifest_wo_lock, lock_wo_manifest = [], []
    for p in sorted(changed):
        d, name = posixpath.split(p)
        if name in LOCK_FOR:
            existing = [_join(d, l) for l in LOCK_FOR[name] if _join(d, l) in tracked]
            if existing and not any(l in changed for l in existing):
                manifest_wo_lock.append({"manifest": p, "lockfiles": existing})
        elif name in MANIFEST_FOR:
            manifest = _join(d, MANIFEST_FOR[name])
            if manifest not in changed and manifest in tracked:
                lock_wo_manifest.append({"lockfile": p, "manifest": manifest})
    return {
        "manifest_without_lockfile": manifest_wo_lock,
        "lockfile_without_manifest": lock_wo_manifest,
        "heuristic": True,
        "note": "A manifest edit that does not touch dependencies (e.g. scripts/metadata) can be a false positive.",
    }


# ---------------------------------------------------------------- clustering
def kind_of(tags: set) -> str:
    if "migration" in tags:
        return "migration"
    if "secret-file" in tags:
        return "config"
    if tags & {"ci", "infra"}:
        return "infra"
    if tags & {"lockfile", "dependency"}:
        return "deps"
    if "test" in tags:
        return "test"
    if "docs" in tags and "source" not in tags:
        return "docs"
    if "ui" in tags:
        return "ui"
    if "source" in tags:
        return "source"
    return "config"


def _fallback_clusters(entries: list, ctx: Ctx) -> list:
    groups: dict = {}
    for e in entries:
        kind = kind_of(ctx.tags(e["path"]))
        top = e["path"].split("/")[0] if "/" in e["path"] else "."
        key = (kind, top) if kind in ("source", "ui", "test") else (kind, "")
        g = groups.setdefault(key, {"label": (f"{kind}: {top}" if key[1] else kind), "kind": kind, "paths": [], "added": 0, "deleted": 0})
        g["paths"].append(e["path"])
        g["added"] += int(e.get("added") or 0)
        g["deleted"] += int(e.get("deleted") or 0)
    out = []
    for i, g in enumerate(sorted(groups.values(), key=lambda g: (-len(g["paths"]), g["label"])), 1):
        g["id"] = i
        g["reasons"] = ["grouped by path tag and top-level directory (built-in fallback)"]
        out.append(g)
    return out


def _norm_cluster(c) -> dict:
    get = (lambda k, d=None: c.get(k, d)) if isinstance(c, dict) else (lambda k, d=None: getattr(c, k, d))
    paths = list(get("paths", []) or [])
    return {
        "id": get("id"), "label": get("label"), "kind": get("kind"),
        "file_count": len(paths), "paths": paths[:CLUSTER_PATH_CAP], "paths_truncated": len(paths) > CLUSTER_PATH_CAP,
        "reasons": list(get("reasons", []) or [])[:5], "added": int(get("added", 0) or 0), "deleted": int(get("deleted", 0) or 0),
    }


def cluster_entries(entries: list, ctx: Ctx) -> tuple:
    """Return ``(clusters, source)``; uses semantic.cluster when importable, else a built-in grouping."""
    entries = entries[:MAX_CLUSTER_ENTRIES]
    if len(entries) >= MAX_CLUSTER_ENTRIES:
        ctx.warnings.append(f"clusters: only the first {MAX_CLUSTER_ENTRIES} files were clustered")
    if not entries:
        return [], "none"
    try:
        from gitwarp.semantic.cluster import cluster_paths  # type: ignore
    except ImportError:
        cluster_paths = None
    if cluster_paths is not None:
        try:
            return [_norm_cluster(c) for c in cluster_paths(entries, ctx.cfg, ctx.root)], "semantic.cluster"
        except Exception as e:  # noqa: BLE001 - never let a clustering bug break analysis
            ctx.warnings.append(f"clusters: semantic.cluster failed ({type(e).__name__}); used built-in fallback")
    return [_norm_cluster(c) for c in _fallback_clusters(entries, ctx)], "fallback"


def mixed_concerns(clusters: list) -> dict:
    """Heuristic: >=2 independent code clusters, a 'mixed' cluster, or CI/infra changes beside code."""
    code = [c for c in clusters if c["kind"] in ("source", "ui")]
    infra = [c for c in clusters if c["kind"] == "infra"]
    mixed = [c for c in clusters if c["kind"] == "mixed"]
    reasons = []
    if len(code) >= 2:
        reasons.append(f"{len(code)} independent code clusters: " + ", ".join(str(c["label"]) for c in code[:5]))
    if infra and code:
        reasons.append(f"infra/CI changes ({sum(c['file_count'] for c in infra)} files) alongside code changes")
    if mixed:
        reasons.append(f"{len(mixed)} cluster(s) classified as mixed")
    return {"flag": bool(reasons), "reasons": reasons, "heuristic": True}


def commit_opportunities(clusters: list) -> list:
    """Atomic-commit boundaries: one proposal per cluster when there are at least two."""
    if len(clusters) < 2:
        return []
    return [
        {"cluster_id": c["id"], "label": c["label"], "kind": c["kind"], "files": c["file_count"], "added": c["added"], "deleted": c["deleted"]}
        for c in clusters
    ]


def tests_vs_source(paths: Iterable[str], ctx: Ctx) -> dict:
    """Source files changed with no test file in the same change set -> potential_missing_tests."""
    paths = list(paths)
    src = [p for p in paths if "source" in ctx.tags(p) and "test" not in ctx.tags(p)]
    tst = [p for p in paths if "test" in ctx.tags(p)]
    flag = bool(src) and not tst
    if flag:
        reason = (f"{len(src)} source file(s) changed (e.g. {', '.join(src[:3])}) and 0 test files changed in the same change set. "
                  "Heuristic: existing tests may already cover this.")
    elif src:
        reason = f"{len(src)} source file(s) and {len(tst)} test file(s) changed together."
    else:
        reason = "No source files changed."
    return {"potential_missing_tests": flag, "source_files_changed": len(src), "test_files_changed": len(tst),
            "source_files": src[:15], "test_files": tst[:15], "reasoning": reason}


def tracked_set(root: Path, ctx: Ctx, rev: Optional[str] = None) -> set:
    """Tracked paths: HEAD tree when ``rev`` is given, else the index."""
    if rev:
        return set(ctx.safe("ls-tree", lambda: git.tracked_files(root, rev=rev), []))
    r = ctx.safe("ls-files", lambda: git.run(["ls-files", "-z"], cwd=root, timeout=30))
    return set(p for p in r.stdout.split("\x00") if p) if r is not None and r.ok else set()


def read_capped(path: Path, max_bytes: int = 200_000) -> tuple:
    """(text|None, is_binary). Skips symlinks and unreadable files."""
    try:
        if path.is_symlink() or not path.is_file():
            return None, False
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return None, False
    if b"\x00" in data[:8000]:
        return None, True
    return data.decode("utf-8", errors="replace"), False
