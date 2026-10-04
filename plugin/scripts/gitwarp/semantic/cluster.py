"""Deterministic change clustering (producer of the ``semantic.cluster`` contract).

``cluster_paths(entries, cfg, repo=None) -> list[Cluster]``

Heuristic, in order:
 1. generated files (``generated`` tag) and secret-looking files go to their own clusters;
 2. every remaining file gets a *concern* (deps / migration / infra / docs / test / ui / config / source);
 3. deps group by manifest directory (manifest + lockfile together), migrations+schema together,
    CI and other infra together per kind, docs together, config together;
 4. source/ui files group by module (directory prefix after skipping generic dirs such as src/lib/app);
    large modules are split one directory deeper;
 5. tests join the cluster of the source they test (name conventions), else form a ``tests`` cluster;
 6. with ``repo`` given, source clusters whose files import each other are merged (bounded, cheap).
Output order: size desc, then label.  Labels are suggestions only.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from ..core.config import Config
from ..core.paths import classify_path

GENERIC_DIRS = {"src", "lib", "app", "apps", "source", "sources", "pkg", "internal", "packages", "main", "java", "python", "js", "ts", "scripts", "code"}
SPLIT_THRESHOLD = 8
IMPORT_SCAN_FILES = 300
IMPORT_MERGE_MAX = 12


@dataclass
class Cluster:
    id: int
    label: str
    kind: str
    paths: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    added: int = 0
    deleted: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "kind": self.kind, "paths": list(self.paths),
                "reasons": list(self.reasons), "added": self.added, "deleted": self.deleted}


# ---------------------------------------------------------------------------------------------- helpers

def concern_of(tags: set) -> str:
    """Primary concern of a path from its tags (single, deterministic)."""
    if "generated" in tags:
        return "generated"
    if "secret-file" in tags:
        return "secret"
    if "dependency" in tags or "lockfile" in tags:
        return "deps"
    if "migration" in tags:
        return "migration"
    if "ci" in tags:
        return "ci"
    if "infra" in tags:
        return "infra"
    if "test" in tags:
        return "test"
    if "docs" in tags:
        return "docs"
    if "schema" in tags and "source" not in tags and "ui" not in tags:
        return "migration"
    if "ui" in tags:
        return "ui"
    if "source" in tags:
        return "source"
    if "schema" in tags:
        return "migration"
    return "config"


def _norm(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def module_key(path: str, depth: int = 1) -> str:
    """Directory-prefix module for ``path``: first ``depth`` meaningful directories (generic dirs skipped)."""
    parts = PurePosixPath(path).parts[:-1]
    meaningful = [d for d in parts if d.lower() not in GENERIC_DIRS and d not in ("tests", "test", "__tests__", "spec")]
    if not meaningful:
        return "(root)"
    return "/".join(meaningful[:depth])


_TEST_STEM_RES = (
    (re.compile(r"^test_(.+)$", re.I), 1),
    (re.compile(r"^(.+)_test$", re.I), 1),
    (re.compile(r"^(.+)\.(?:test|spec)$", re.I), 1),
    (re.compile(r"^(.+)(?:Test|Tests|Spec)$"), 1),
    (re.compile(r"^(.+)_spec$", re.I), 1),
)


def _stem_key(s: str) -> str:
    return re.sub(r"[-_.\s]", "", s).lower()


def test_target_stems(path: str) -> list:
    """Candidate source stems a test file is named after (normalised)."""
    stem = PurePosixPath(path).stem
    out = []
    for rx, g in _TEST_STEM_RES:
        m = rx.match(stem)
        if m:
            out.append(_stem_key(m.group(g)))
    if not out and "__tests__" in path.split("/"):
        out.append(_stem_key(stem))
    if not out and PurePosixPath(path).parent.name in ("tests", "test", "spec", "e2e") and stem.lower() not in ("conftest", "__init__", "setup"):
        out.append(_stem_key(stem))
    return [o for o in out if o]


def _dir_affinity(a: str, b: str) -> int:
    pa, pb = PurePosixPath(a).parts[:-1], PurePosixPath(b).parts[:-1]
    n = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        n += 1
    return n


def pair_candidates(paths_by_concern: dict) -> dict:
    """Map test path -> sorted candidate source paths (name-convention match). Exposed for ambiguity warnings."""
    by_stem: dict = {}
    for p in paths_by_concern.get("source", []) + paths_by_concern.get("ui", []):
        by_stem.setdefault(_stem_key(PurePosixPath(p).stem), []).append(p)
    out = {}
    for t in paths_by_concern.get("test", []):
        cands = []
        for st in test_target_stems(t):
            cands += by_stem.get(st, [])
        if cands:
            # best = most directory affinity, then shortest path, then lexical (stable)
            out[t] = sorted(set(cands), key=lambda s: (-_dir_affinity(t, s), len(s), s))
    return out


class _UF:
    def __init__(self):
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


# ---------------------------------------------------------------------------------------------- main

def cluster_paths(entries: list, cfg: Optional[Config], repo: Optional[Path] = None) -> list:
    cfg = cfg or Config()
    meta: dict = {}
    for e in entries or []:
        try:
            p = _norm(str(e["path"]))
        except (KeyError, TypeError):
            continue
        if not p:
            continue
        cur = meta.setdefault(p, {"added": 0, "deleted": 0, "status": "M"})
        cur["added"] += int(e.get("added") or 0)
        cur["deleted"] += int(e.get("deleted") or 0)
        cur["status"] = e.get("status") or cur["status"]
    if not meta:
        return []

    tags = {p: classify_path(p, cfg) for p in meta}
    concern = {p: concern_of(t) for p, t in tags.items()}
    by_concern: dict = {}
    for p in sorted(meta):
        by_concern.setdefault(concern[p], []).append(p)

    groups: dict = {}   # key -> {"kind", "label", "paths", "reasons"}

    def put(key, kind, label, path, reason=None):
        g = groups.setdefault(key, {"kind": kind, "label": label, "paths": [], "reasons": []})
        g["paths"].append(path)
        if reason and reason not in g["reasons"]:
            g["reasons"].append(reason)

    for p in by_concern.get("generated", []):
        put(("generated",), "generated", "generated/ignored", p, "generated, vendored or build output (tagged generated); excluded from commit grouping")
    for p in by_concern.get("secret", []):
        put(("secret",), "config", "secret-looking files", p, "path looks like a secret/credential file (never propose committing these)")

    # deps: per manifest directory
    for p in by_concern.get("deps", []):
        d = posixpath.dirname(p) or "."
        put(("deps", d), "deps", "dependencies" if d == "." else f"dependencies ({d})", p,
            "dependency manifest and/or lockfile" + ("" if d == "." else f" in {d}/") + "; kept together")
    for p in by_concern.get("migration", []):
        put(("migration",), "migration", "db schema/migrations", p, "migration or schema definition; grouped so schema changes land together")
    for p in by_concern.get("ci", []):
        put(("ci",), "infra", "ci", p, "CI/CD pipeline definition")
    for p in by_concern.get("infra", []):
        put(("infra",), "infra", "infra", p, "infrastructure/deploy configuration")
    for p in by_concern.get("docs", []):
        put(("docs",), "docs", "docs", p, "documentation files")
    for p in by_concern.get("config", []):
        put(("config",), "config", "config", p, "configuration files")

    # source/ui by module
    code_paths = by_concern.get("source", []) + by_concern.get("ui", [])
    depth1: dict = {}
    for p in code_paths:
        depth1.setdefault((concern[p], module_key(p, 1)), []).append(p)
    for (kind, mod), plist in depth1.items():
        if len(plist) > SPLIT_THRESHOLD:
            sub: dict = {}
            for p in plist:
                sub.setdefault(module_key(p, 2), []).append(p)
            if len(sub) > 1:
                for m2, pl2 in sub.items():
                    for p in pl2:
                        put((kind, m2), kind, _code_label(kind, m2), p, f"files under module '{m2}' (module split one level deeper: {len(plist)} files in '{mod}')")
                continue
        for p in plist:
            put((kind, mod), kind, _code_label(kind, mod), p, f"files under module '{mod}'" if mod != "(root)" else "files at the repository root")

    # tests: pair with sources
    pairs = pair_candidates(by_concern)
    owner: dict = {}
    for key, g in groups.items():
        for p in g["paths"]:
            owner[p] = key
    unpaired = []
    for t in by_concern.get("test", []):
        cands = pairs.get(t)
        if cands:
            src = cands[0]
            key = owner[src]
            groups[key]["paths"].append(t)
            r = f"test '{t}' paired with source '{src}' by naming convention"
            groups[key]["reasons"].append(r)
        else:
            unpaired.append(t)
    for t in unpaired:
        put(("test",), "test", "tests", t, "test file with no matching changed source file")

    # import affinity (cheap, bounded)
    if repo is not None:
        _merge_by_imports(groups, repo, cfg)

    clusters = []
    for key, g in groups.items():
        paths = sorted(set(g["paths"]))
        reasons = list(dict.fromkeys(g["reasons"]))
        clusters.append(Cluster(0, g["label"], g["kind"], paths, reasons,
                                sum(meta[p]["added"] for p in paths), sum(meta[p]["deleted"] for p in paths)))
    clusters.sort(key=lambda c: (-len(c.paths), c.label, c.kind, c.paths[0]))
    for i, c in enumerate(clusters, 1):
        c.id = i
    return clusters


def _code_label(kind: str, mod: str) -> str:
    if mod == "(root)":
        return "root source" if kind == "source" else "root ui"
    return mod if kind == "source" else (mod if mod.startswith("ui") else f"ui/{mod}")


def _merge_by_imports(groups: dict, repo: Path, cfg: Config) -> None:
    """Merge small same-kind code clusters when one's files import the other's (via adapters)."""
    try:
        from .adapters import adapter_for, build_adapters
        from .common import read_text
    except ImportError:
        return
    code_keys = [k for k, g in groups.items() if g["kind"] in ("source", "ui") and k[0] in ("source", "ui")]
    if len(code_keys) < 2:
        return
    files = {p: k for k in code_keys for p in groups[k]["paths"]}
    if len(files) > IMPORT_SCAN_FILES:
        return
    adapters = build_adapters(repo)
    fileset = set(files)
    uf = _UF()
    reasons: dict = {}
    for p, k in sorted(files.items()):
        ad = adapter_for(p, adapters)
        if ad is None:
            continue
        text = read_text(Path(repo) / p)
        if text is None:
            continue
        for spec in ad.imports(p, text):
            tgt = ad.resolve(spec, p, fileset)
            if tgt and tgt != p and files[tgt] != k and files[tgt][0] == k[0]:
                uf.union(k, files[tgt])
                reasons.setdefault(k, []).append(f"'{p}' imports '{tgt}'")
    comps: dict = {}
    for k in code_keys:
        comps.setdefault(uf.find(k), []).append(k)
    for root_key, keys in comps.items():
        if len(keys) < 2:
            continue
        total = sum(len(groups[k]["paths"]) for k in keys)
        if total > IMPORT_MERGE_MAX:
            continue
        keys = sorted(keys)
        target = keys[0]
        for k in keys[1:]:
            groups[target]["paths"] += groups[k]["paths"]
            groups[target]["reasons"] += groups[k]["reasons"]
            del groups[k]
        for k in keys:
            groups[target]["reasons"] += reasons.get(k, [])[:3]
        groups[target]["reasons"].append("merged by import affinity (changed files import each other)")
