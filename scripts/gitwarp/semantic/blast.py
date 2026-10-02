"""``warp.py blast``: explainable blast-radius graph for a change set.  Read-only and time/size bounded.

Level rules (deterministic, no scores):
  risk drivers are explicit facts, each tagged major or minor.
    major: webhook handler touched; schema/migration touched; widely used module (>=10 direct dependants in >=3 modules);
           sensitive path changed with no tests found; deleted file still referenced.
    minor: route/handler touched; config/infra/CI touched; no tests found for changed source; lockfile changed;
           public module (>=3 direct dependants in >=2 modules, or >=8 dependants); sensitive path changed.
  level = HIGH   if (>=2 major) or (>=1 major and >=2 drivers) or (>=4 drivers)
          MEDIUM if >=1 driver
          LOW    otherwise
"""
from __future__ import annotations

import posixpath
import re
import time
from pathlib import Path, PurePosixPath
from typing import Optional

from ..core import git
from ..core.config import load_config
from ..core.paths import classify_path
from . import cluster as cl
from .adapters import GenericAdapter, adapter_for, build_adapters
from .adapters.generic import search_token
from .common import MAX_FILE_BYTES, read_text, repo_state, state_warnings, status_letter, top_dir

LEVEL_RULES = [
    "major drivers: webhook handler touched; schema/migration touched; widely used module (>=10 direct dependants across >=3 modules); sensitive path changed with no tests found; deleted file still referenced",
    "minor drivers: route/handler touched; config/infra/CI touched; no tests found for changed source; lockfile changed; public module (>=3 dependants across >=2 modules, or >=8); sensitive path changed",
    "HIGH if >=2 major drivers, or >=1 major and >=2 drivers total, or >=4 drivers total; MEDIUM if >=1 driver; LOW otherwise",
]
MAX_UNIVERSE = 30000
MAX_REF_SCAN = 400

_ROUTE_PATH = re.compile(r"(^|/)(routes?|handlers?|controllers?|endpoints?|api|webhooks?|resolvers?)(/|\.|$)", re.I)
_ROUTE_CODE = (
    re.compile(r"\b(?:app|router|server|api|fastify|bp|blueprint)\.(?:get|post|put|delete|patch|all|use|route)\s*\(", re.I),
    re.compile(r"@(?:Get|Post|Put|Delete|Patch|Controller|RequestMapping|GetMapping|PostMapping)\b"),
    re.compile(r"export\s+(?:async\s+)?function\s+(?:GET|POST|PUT|DELETE|PATCH)\b"),
    re.compile(r"@\w+\.(?:route|get|post|put|delete|patch)\s*\("),
    re.compile(r"\bAPIRouter\s*\(|\burlpatterns\b|\bhttp\.HandleFunc\b|\br\.(?:GET|POST|PUT|DELETE)\("),
)
_WEBHOOK = re.compile(r"webhook", re.I)


def interface_signals(path: str, text: Optional[str]) -> list:
    sig = []
    low = path.lower()
    if re.search(r"\.(proto|graphql|gql)$", low) or PurePosixPath(low).name in ("openapi.yaml", "openapi.yml", "openapi.json", "swagger.json"):
        sig.append("api-schema")
    if _ROUTE_PATH.search(path):
        sig.append("route-or-handler-path")
    if text and any(rx.search(text) for rx in _ROUTE_CODE):
        sig.append("route-definitions")
    if _WEBHOOK.search(path) or (text and re.search(r"def\s+\w*webhook|webhook\w*\s*[=(]|/webhooks?\b", text, re.I)):
        sig.append("webhook")
    return sig


def _normalise_args(root: Path, paths: list) -> list:
    out = []
    for p in paths:
        pp = Path(p)
        try:
            rel = pp.resolve().relative_to(root.resolve()) if pp.is_absolute() else Path(p)
        except ValueError:
            out.append(p)
            continue
        s = rel.as_posix()
        while s.startswith("./"):
            s = s[2:]
        out.append(s)
    return out


def analyze(root: Path, paths: Optional[list] = None, base: Optional[str] = None, max_depth: int = 3,
            max_nodes: int = 200, timeout: float = 15.0) -> dict:
    t0 = time.monotonic()
    deadline = t0 + timeout
    warnings: list = []
    cfg = load_config(root)
    warnings += cfg.warnings
    st = repo_state(root)
    warnings += state_warnings(st)
    max_depth = max(1, min(int(max_depth), 6))

    # ---- changed set
    statuses: dict = {}
    mode = "paths"
    if paths:
        changed = _normalise_args(root, paths)
        for p in changed:
            statuses[p] = "M" if (root / p).exists() else "D"
    elif base:
        mode = f"base:{base}"
        try:
            git.check_ref(base)
        except ValueError as e:
            return {"error": str(e), "warnings": warnings}
        if git.rev_parse(base, root) is None:
            return {"error": f"unknown base ref: {base}", "warnings": warnings}
        r = git.run(["diff", "--name-status", "-z", f"{base}...HEAD"], cwd=root, timeout=30)
        parts = r.stdout.split("\x00") if r.ok else []
        i, changed = 0, []
        while i < len(parts):
            code = parts[i]
            i += 1
            if not code:
                continue
            n = 2 if code[0] in "RC" else 1
            names = parts[i:i + n]
            i += n
            if names:
                changed.append(names[-1])
                statuses[names[-1]] = code[0]
    else:
        mode = "working-tree"
        changed = []
        try:
            for s in git.working_tree_status(root, untracked="all"):
                if s.conflicted:
                    warnings.append(f"unresolved conflict in {s.path}")
                changed.append(s.path)
                statuses[s.path] = status_letter(s.xy)
        except git.GitError as e:
            warnings.append(f"git status failed: {e}")
    changed = list(dict.fromkeys(changed))
    if not changed:
        return {"repo": str(root), "mode": mode, "state": st, "changed": [], "nodes": [], "tree": [], "risk_drivers": [],
                "level": "LOW", "level_rules": LEVEL_RULES, "truncated": {"nodes": False, "depth": False, "scan": False},
                "message": "no changed files to analyse", "warnings": warnings}

    tagcache: dict = {}

    def tags_of(p):
        t = tagcache.get(p)
        if t is None:
            t = tagcache[p] = classify_path(p, cfg)
        return t

    ignored_changed = [p for p in changed if "generated" in tags_of(p)]
    if ignored_changed:
        warnings.append(f"{len(ignored_changed)} generated/vendored changed file(s) skipped: {', '.join(ignored_changed[:5])}")
    changed = [p for p in changed if p not in ignored_changed]
    if len(changed) > 300:
        warnings.append(f"{len(changed)} changed files: analysing the first 300 only")
        changed = changed[:300]

    # ---- universe
    r = git.run(["ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root, timeout=30)
    universe_all = sorted(set(p for p in r.stdout.split("\x00") if p)) if r.ok else []
    scan_truncated = False
    if len(universe_all) > MAX_UNIVERSE:
        scan_truncated = True
        warnings.append(f"repository has {len(universe_all)} files; scanning only the first {MAX_UNIVERSE}")
        universe_all = universe_all[:MAX_UNIVERSE]
    universe = [p for p in universe_all if "generated" not in tags_of(p) and (root / p).exists()]
    fileset = set(universe) | set(changed)

    # ---- import graph (reverse edges)
    adapters = build_adapters(root)
    rev: dict = {}
    scanned = edges = 0
    scan_deadline = t0 + timeout * 0.6
    for p in universe:
        ad = adapter_for(p, adapters)
        if ad is None:
            continue
        if time.monotonic() > scan_deadline:
            scan_truncated = True
            warnings.append("import scan stopped at its time budget; the graph may be incomplete")
            break
        text = read_text(root / p)
        if text is None:
            continue
        scanned += 1
        seen_targets = set()
        for spec in ad.imports(p, text):
            tgt = ad.resolve(spec, p, fileset)
            if tgt and tgt != p and tgt not in seen_targets:
                seen_targets.add(tgt)
                rev.setdefault(tgt, []).append((p, spec))
                edges += 1

    # ---- node bookkeeping
    nodes: dict = {}
    order: list = []
    truncated_nodes = False
    omitted = 0

    def add_node(path, depth, via, reason, confidence, root_changed):
        nonlocal truncated_nodes, omitted
        if path in nodes:
            return nodes[path]
        if len(nodes) - len(changed) >= max_nodes:
            truncated_nodes = True
            omitted += 1
            return None
        nodes[path] = {"path": path, "kind": "dependant", "reason": reason, "depth": depth, "via": via,
                       "confidence": confidence, "for": root_changed}
        order.append(path)
        return nodes[path]

    for p in changed:
        nodes[p] = {"path": p, "kind": "changed", "reason": f"changed ({mode}; status {statuses.get(p, 'M')})", "depth": 0, "via": None,
                    "confidence": "changed", "for": p}
        order.append(p)

    # BFS reverse imports
    frontier = list(changed)
    depth_truncated = False
    for depth in range(1, max_depth + 1):
        nxt = []
        for n in frontier:
            for imp, spec in sorted(rev.get(n, [])):
                if imp in nodes:
                    continue
                nd = add_node(imp, depth, n, f"imports {spec}", "import-graph", nodes[n]["for"])
                if nd is not None:
                    nxt.append(imp)
        frontier = nxt
        if not frontier:
            break
    else:
        for n in frontier:
            if any(imp not in nodes for imp, _ in rev.get(n, [])):
                depth_truncated = True
                break

    # naming-convention tests
    stems: dict = {}
    for c in changed:
        if "test" in tags_of(c):
            continue
        stems.setdefault(cl._stem_key(PurePosixPath(c).stem), []).append(c)
    for u in universe:
        if "test" not in tags_of(u) or u in nodes:
            continue
        for st_ in cl.test_target_stems(u):
            targets = stems.get(st_)
            if targets:
                tgt = sorted(targets, key=lambda s: (-cl._dir_affinity(u, s), s))[0]
                add_node(u, 1, tgt, f"test named after {tgt} (naming convention)", "naming", tgt)
                break

    # generic text search for changed files no adapter understands
    searches = 0
    for c in changed:
        if time.monotonic() > deadline:
            break
        tg = tags_of(c)
        if adapter_for(c, adapters) is not None or tg & {"docs", "lockfile", "dependency", "generated", "secret-file", "ci"} or searches >= 20:
            continue
        tok = GenericAdapter.stem_token(c)
        if not tok:
            continue
        searches += 1
        hits, method = search_token(root, tok, universe, deadline, exclude=set(changed))
        for h in hits:
            if h in fileset and "generated" not in tags_of(h):
                add_node(h, 1, c, f"mentions '{tok}' (text match via {method}; heuristic)", "text-match", c)

    # config / infra files that reference changed paths
    refs = {}
    for c in changed[:50]:
        refs[c] = c
    ref_files = [u for u in universe if tags_of(u) & {"ci", "infra", "config"} and not tags_of(u) & {"lockfile"} and u not in changed][:MAX_REF_SCAN]
    for u in ref_files:
        if time.monotonic() > deadline:
            break
        text = read_text(root / u, 200_000)
        if not text:
            continue
        for ref in refs:
            if ref in text:
                add_node(u, 1, ref, f"mentions '{ref}' (text match in config/infra)", "text-match", ref)
                break

    # classify nodes
    iface: dict = {}
    for p in order:
        n = nodes[p]
        tg = tags_of(p)
        text = read_text(root / p, 400_000) if "test" not in tg and "secret-file" not in tg and (root / p).exists() else None
        sig = interface_signals(p, text) if "test" not in tg else []
        iface[p] = sig
        n["tags"] = sorted(tg)
        if n["kind"] == "changed":
            n["role"] = _role(tg, sig)
            continue
        if "test" in tg:
            n["kind"] = "test"
        elif sig and set(sig) != {"route-or-handler-path"} or (sig and "source" in tg):
            n["kind"] = "interface"
        elif tg & {"schema", "migration"}:
            n["kind"] = "schema"
        elif tg & {"infra", "ci"}:
            n["kind"] = "infra"
        elif "config" in tg and "source" not in tg:
            n["kind"] = "config"
        n["interface_signals"] = sig if sig else []

    # per changed file info
    info = []
    drivers: list = []
    no_test_src, locks, deleted_ref = [], [], []
    for c in changed:
        tg = tags_of(c)
        text = read_text(root / c) if (root / c).exists() and "secret-file" not in tg else None
        ad = adapter_for(c, adapters)
        direct = [imp for imp, _ in rev.get(c, [])]
        direct_nt = [d for d in direct if "test" not in tags_of(d)]
        mods = sorted({cl.module_key(d, 1) if "/" in d else "(root)" for d in direct_nt})
        tests = sorted(p for p in order if nodes[p]["kind"] == "test" and nodes[p]["for"] == c)
        is_test = "test" in tg
        info.append({"path": c, "status": statuses.get(c, "M"), "tags": sorted(tg), "role": nodes[c].get("role"),
                     "exports": ad.exports(c, text)[:30] if (ad and text) else [],
                     "direct_dependants": len(direct_nt), "dependant_modules": mods, "tests": tests,
                     "interface_signals": iface.get(c, [])})
        if "source" in tg and not is_test and not tests and not (tags_of(c) & {"config"} and "source" not in tg):
            no_test_src.append(c)
        if "lockfile" in tg:
            locks.append(c)
        if statuses.get(c) == "D" and direct_nt:
            deleted_ref.append((c, len(direct_nt)))

    def drv(id_, weight, text):
        drivers.append({"id": id_, "weight": weight, "text": text})

    wide, public = [], []
    for i in info:
        n, m = i["direct_dependants"], len(i["dependant_modules"])
        if n >= 10 and m >= 3:
            wide.append(f"{i['path']} ({n} dependants across {m} modules)")
        elif (n >= 3 and m >= 2) or n >= 8:
            public.append(f"{i['path']} ({n} dependants across {m} module{'s' if m != 1 else ''})")
    if wide:
        drv("widely-used-module", "major", "widely used module changed: " + "; ".join(wide[:4]))
    if public:
        drv("public-module", "minor", "public module with many dependants: " + "; ".join(public[:4]))
    sch = [c for c in changed if tags_of(c) & {"schema", "migration"}]
    if sch:
        drv("schema", "major", "schema/migration touched: " + ", ".join(sch[:5]))
    web = [c for c in changed if "webhook" in iface.get(c, [])]
    if web:
        drv("webhook", "major", "webhook handler touched: " + ", ".join(web[:5]))
    routes = [c for c in changed if set(iface.get(c, [])) & {"route-definitions", "route-or-handler-path", "api-schema"} and c not in web]
    if routes:
        drv("route", "minor", "route/handler/API definition touched: " + ", ".join(routes[:5]))
    ci = [c for c in changed if tags_of(c) & {"infra", "ci", "config"} and not tags_of(c) & {"lockfile", "dependency", "docs", "schema", "migration", "test"} and "source" not in tags_of(c)]
    if ci:
        drv("config-infra", "minor", "config/infra touched: " + ", ".join(ci[:5]))
    sens = [c for c in changed if "sensitive" in tags_of(c) and "test" not in tags_of(c) and "docs" not in tags_of(c)]
    sens_nt = [c for c in sens if c in no_test_src]
    if sens_nt:
        drv("sensitive-no-tests", "major", "sensitive path changed with no tests found: " + ", ".join(sens_nt[:5]))
    elif sens:
        drv("sensitive", "minor", "sensitive path changed (auth/payment/security-like name): " + ", ".join(sens[:5]))
    rest_nt = [c for c in no_test_src if c not in sens_nt]
    if rest_nt:
        drv("no-tests", "minor", f"no tests found for changed source: {', '.join(rest_nt[:6])}{' ...' if len(rest_nt) > 6 else ''}")
    if locks:
        drv("lockfile", "minor", "lockfile changed: " + ", ".join(locks))
    if deleted_ref:
        drv("deleted-still-referenced", "major", "deleted file still imported: " + "; ".join(f"{c} ({n} importers)" for c, n in deleted_ref[:4]))

    majors = sum(1 for d in drivers if d["weight"] == "major")
    total = len(drivers)
    level = "HIGH" if (majors >= 2 or (majors >= 1 and total >= 2) or total >= 4) else ("MEDIUM" if total >= 1 else "LOW")

    # tree
    children: dict = {}
    for p in order:
        v = nodes[p]["via"]
        if v is not None:
            children.setdefault(v, []).append(p)

    def build(p):
        n = nodes[p]
        d = {k: n[k] for k in ("path", "kind", "reason", "depth", "confidence")}
        d["children"] = [build(ch) for ch in children.get(p, [])]
        return d

    flat = [{k: v for k, v in nodes[p].items()} for p in order if nodes[p]["kind"] != "changed"]
    counts: dict = {}
    for n in flat:
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    return {
        "repo": str(root), "mode": mode, "base": base, "state": st, "level": level,
        "level_note": "deterministic rule outcome, not a probability; see level_rules",
        "level_rules": LEVEL_RULES,
        "risk_drivers": [d["text"] for d in drivers], "risk_driver_details": drivers,
        "changed": info, "nodes": flat, "tree": [build(c) for c in changed], "counts": counts,
        "truncated": {"nodes": truncated_nodes, "omitted_nodes": omitted, "depth": depth_truncated, "scan": scan_truncated,
                      "max_depth": max_depth, "max_nodes": max_nodes},
        "stats": {"files_in_universe": len(universe), "files_parsed": scanned, "import_edges": edges,
                  "seconds": round(time.monotonic() - t0, 2)},
        "caveats": ["Edges from imports are static and best effort (no dynamic dispatch, no runtime config); text-match edges are heuristic."],
        "warnings": warnings + [n for a in adapters for n in getattr(a, "notes", [])],
    }


def _role(tg: set, sig: list) -> str:
    if "test" in tg:
        return "test"
    if "webhook" in sig:
        return "webhook-handler"
    if sig:
        return "interface"
    for t in ("migration", "schema", "ci", "infra", "lockfile", "dependency", "docs", "config", "ui", "source"):
        if t in tg:
            return t
    return "other"
