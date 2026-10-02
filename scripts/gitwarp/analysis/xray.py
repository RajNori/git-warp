"""``warp.py xray``: read-only repository health snapshot (state, working tree, signals, clusters, risk)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from gitwarp.core import git
from gitwarp.core.redact import redact

from . import risk as risk_mod
from . import secrets
from .common import (Ctx, PATH_CAP, cap, cluster_entries, commit_opportunities, dependency_drift, is_secret_file, mixed_concerns,
                     read_capped, tests_vs_source, tracked_set, uniq)
from .diffscan import parse_diff

CHURN_COMMITS = 200
CHURN_DAYS = 30
MAX_DIFF_BYTES = 3_000_000
MAX_UNTRACKED = 5000
MAX_UNTRACKED_SCAN = 200


# ------------------------------------------------------------------ state
def _operation_detail(ctx: Ctx, op: Optional[str]) -> Optional[dict]:
    if not op:
        return None
    detail = {"type": op}
    if op == "rebase":
        gd = ctx.safe("git-dir", lambda: git.git_dir(ctx.root))
        if gd:
            for sub in ("rebase-merge", "rebase-apply"):
                f = Path(gd) / sub / "head-name"
                try:
                    if f.is_file():
                        name = f.read_text(encoding="utf-8", errors="replace").strip()
                        detail["branch"] = name[len("refs/heads/"):] if name.startswith("refs/heads/") else name
                except OSError:
                    pass
    return detail


def build_state(ctx: Ctx) -> dict:
    root = ctx.root
    branch = ctx.safe("branch", lambda: git.current_branch(root))
    head = ctx.safe("head", lambda: git.head_sha(root))
    unborn = head is None
    detached = branch is None and head is not None
    up = ctx.safe("upstream", lambda: git.upstream(root)) if branch else None
    ab = ctx.safe("ahead/behind", lambda: git.ahead_behind(root, up)) if up else None
    upstream_gone = False
    if branch and not up:
        r = ctx.safe("branch-config", lambda: git.run(["config", "--get", f"branch.{branch}.merge"], cwd=root))
        upstream_gone = bool(r is not None and r.ok and r.text)
        if upstream_gone:
            ctx.warnings.append("upstream is configured but its remote ref no longer resolves (deleted or not fetched)")
    op = ctx.safe("operation", lambda: git.repo_operation(root))
    stashes = ctx.safe("stashes", lambda: git.stashes(root), [])
    wts = ctx.safe("worktrees", lambda: git.worktrees(root), [])
    reflog = ctx.safe("reflog", lambda: git.reflog("HEAD", limit=200, cwd=root), []) if not unborn else []
    shallow = bool(ctx.safe("shallow", lambda: git.is_shallow(root), False))
    if shallow:
        ctx.warnings.append("shallow clone: history-based signals (churn, merge-base, ahead/behind) may be incomplete")
    if unborn:
        ctx.warnings.append("unborn branch: no commits yet; HEAD-relative comparisons are skipped")
    here = str(root.resolve())
    wt_out = []
    for w in wts[:20]:
        p = w.get("path", "")
        try:
            cur = str(Path(p).resolve()) == here
        except OSError:
            cur = False
        wt_out.append({"path": p, "branch": (w.get("branch") or "").replace("refs/heads/", "") or None,
                       "head": (w.get("head") or "")[:8] or None, "detached": bool(w.get("detached")),
                       "locked": bool(w.get("locked")), "prunable": bool(w.get("prunable")), "bare": bool(w.get("bare")), "current": cur})
    return {
        "branch": branch, "detached": detached, "unborn": unborn,
        "head": head, "head_short": head[:8] if head else None,
        "upstream": up, "upstream_gone": upstream_gone,
        "ahead": ab[0] if ab else None, "behind": ab[1] if ab else None,
        "operation": _operation_detail(ctx, op),
        "shallow": shallow,
        "worktrees": {"count": len(wts), "items": wt_out, "truncated": len(wts) > 20},
        "stashes": {"count": len(stashes), "items": [{"ref": s["ref"], "subject": redact(s["subject"])[:100], "date": s["date"]} for s in stashes[:10]],
                    "truncated": len(stashes) > 10},
        "reflog": {"available": bool(reflog), "count": len(reflog), "count_capped": len(reflog) >= 200},
        "tracked_ahead_of_base": None,
    }


def _ahead_of_base(ctx: Ctx, state: dict) -> Optional[dict]:
    if state["upstream"] or state["unborn"]:
        return None
    base = ctx.safe("default-branch", lambda: git.default_branch(ctx.root))
    if not base:
        return None
    if state["branch"] and base.split("/")[-1] == state["branch"]:
        return {"base": base, "ahead": 0}
    r = ctx.safe("ahead-of-base", lambda: git.run(["rev-list", "--count", f"{git.check_ref(base)}..HEAD"], cwd=ctx.root))
    if r is None or not r.ok:
        return None
    try:
        return {"base": base, "ahead": int(r.text)}
    except ValueError:
        return None


# ------------------------------------------------------------------ working tree
def _status_char(e) -> str:
    if e.untracked:
        return "?"
    c = e.xy[0] if e.staged and e.xy[0] not in "U" else e.xy[1]
    if c in "UT" or c == " ":
        return "M"
    return "A" if c == "C" else c


def build_working_tree(ctx: Ctx, state: dict, untracked_all: bool) -> tuple:
    root = ctx.root
    entries = ctx.safe("status", lambda: git.working_tree_status(root, untracked="all" if untracked_all else "normal"), [])
    conflicted = [e for e in entries if e.conflicted]
    staged = [e for e in entries if e.staged and not e.conflicted]
    unstaged = [e for e in entries if e.unstaged and not e.conflicted]
    untracked = [e for e in entries if e.untracked]
    dirty_paths = uniq(e.path for e in entries if not e.untracked)

    # numstat for tracked changes (staged+unstaged vs HEAD; index vs empty tree when unborn)
    ns_args = ["HEAD"] if not state["unborn"] else ["--cached"]
    ns = ctx.safe("numstat", lambda: git.numstat(root, args=ns_args), [])
    added = sum(n["added"] for n in ns)
    deleted = sum(n["deleted"] for n in ns)
    ranked = sorted((n for n in ns if not n["binary"]), key=lambda n: n["added"] + n["deleted"], reverse=True)
    binaries = [n["path"] for n in ns if n["binary"]]

    # expand collapsed untracked directories (bounded) so signals/secret checks see real files
    untracked_files = [e.path for e in untracked if not e.path.endswith("/")]
    collapsed = [e.path for e in untracked if e.path.endswith("/")]
    expanded_truncated = False
    if collapsed:
        r = ctx.safe("untracked-expand", lambda: git.run(["ls-files", "--others", "--exclude-standard", "-z"], cwd=root, timeout=30))
        if r is not None and r.ok:
            allu = [p for p in r.stdout.split("\x00") if p]
            expanded_truncated = len(allu) > MAX_UNTRACKED
            untracked_files = allu[:MAX_UNTRACKED]
            if expanded_truncated:
                ctx.warnings.append(f"untracked: more than {MAX_UNTRACKED} untracked files; analysis uses the first {MAX_UNTRACKED}")
    wt = {
        "clean": not entries,
        "staged": cap(e.path for e in staged),
        "unstaged": cap(e.path for e in unstaged),
        "untracked": {**cap((e.path for e in untracked)), "files_total": len(untracked_files), "collapsed_dirs": len(collapsed),
                      "untracked_all": untracked_all},
        "conflicted": cap(e.path for e in conflicted),
        "numstat_totals": {"files": len(ns), "added": added, "deleted": deleted, "scope": "tracked changes vs HEAD (staged+unstaged); untracked files excluded"},
        "largest_changes": [{"path": n["path"], "added": n["added"], "deleted": n["deleted"]} for n in ranked[:5]],
        "binary_files": cap(binaries),
    }
    internal = {"entries": entries, "ns": ns, "untracked_files": untracked_files, "dirty_paths": dirty_paths,
                "conflicted": [e.path for e in conflicted], "binaries": binaries}
    return wt, internal


# ------------------------------------------------------------------ content scan
def scan_change_set(ctx: Ctx, state: dict, internal: dict) -> dict:
    """Secret-looking content in staged/unstaged added lines and untracked text files (values never echoed)."""
    root = ctx.root
    skip = lambda p: bool(ctx.tags(p) & {"lockfile", "generated"})  # noqa: E731
    findings, total, truncated = [], 0, False
    diff_args = ["HEAD"] if not state["unborn"] else ["--cached"]
    r = ctx.safe("diff-scan", lambda: git.run(["-c", "core.quotepath=off", "diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", *diff_args],
                                              cwd=root, timeout=45))
    if r is not None and r.ok:
        text = r.stdout
        if len(text) > MAX_DIFF_BYTES:
            text, truncated = text[:MAX_DIFF_BYTES], True
            ctx.warnings.append("content scan: diff larger than 3MB; only the first 3MB was scanned for secrets")
        records, trunc_files = parse_diff(text)
        if trunc_files:
            truncated = True
        res = secrets.scan_records(records, skip)
        findings += res["findings"]
        total += res["count"]
    scanned, bin_untracked = 0, []
    for p in internal["untracked_files"][:MAX_UNTRACKED_SCAN]:
        if skip(p):
            continue
        text, is_bin = read_capped(root / p)
        if is_bin:
            bin_untracked.append(p)
        if text is None:
            continue
        scanned += 1
        res = secrets.scan_text(p, text)
        total += len(res)
        findings += res[:10]
    if len(internal["untracked_files"]) > MAX_UNTRACKED_SCAN:
        truncated = True
        ctx.warnings.append(f"content scan: only the first {MAX_UNTRACKED_SCAN} untracked files were read")
    findings = findings[:50]
    for f in findings:
        f["in_test_file"] = "test" in ctx.tags(f["path"])
    return {"count": total, "findings": findings, "truncated": truncated or total > len(findings), "untracked_files_scanned": scanned,
            "binary_untracked": bin_untracked[:20]}


# ------------------------------------------------------------------ history signals
def build_recent_and_churn(ctx: Ctx, state: dict, changed_paths: set) -> tuple:
    if state["unborn"]:
        return [], {"window": {"commits": 0, "days": CHURN_DAYS}, "files": [], "source": "none"}
    root = ctx.root
    recent = ctx.safe("recent-commits", lambda: git.log_commits("HEAD", limit=10, cwd=root), [])
    recent_out = [{"sha": c.short, "subject": redact(c.subject)[:120], "author": c.author_name, "date": c.author_date, "merge": c.is_merge} for c in recent]

    # optional memory index (only when the DB already exists: xray must not create state)
    try:
        sd = git.state_dir(root)
        if (sd / "warp.db").is_file():
            from gitwarp.memory.index import hotspots  # type: ignore
            hs = hotspots(root, limit=10)
            if hs and all(isinstance(h, dict) and h.get("path") for h in hs):
                files = [{"path": h["path"], "commits": h.get("commits") or h.get("count") or h.get("changes"),
                          "touched_in_change_set": h["path"] in changed_paths} for h in hs]
                return recent_out, {"window": {"source": "memory index"}, "files": files, "source": "memory.index"}
    except Exception:  # noqa: BLE001 - optional accelerator; fall through to local computation
        pass

    commits = ctx.safe("churn-log", lambda: git.log_commits("HEAD", limit=CHURN_COMMITS, with_files=True, extra=["--no-merges"], cwd=root, timeout=30), [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=CHURN_DAYS)
    counts: dict = {}
    recent30: dict = {}
    for c in commits:
        try:
            recent_flag = datetime.fromisoformat(c.commit_date) >= cutoff
        except ValueError:
            recent_flag = False
        for f in set(c.files):
            counts[f] = counts.get(f, 0) + 1
            if recent_flag:
                recent30[f] = recent30.get(f, 0) + 1
    top = sorted(((p, n) for p, n in counts.items() if n >= 2), key=lambda x: (-x[1], x[0]))[:10]
    files = [{"path": p, "commits": n, "commits_last_30d": recent30.get(p, 0), "touched_in_change_set": p in changed_paths} for p, n in top]
    return recent_out, {"window": {"commits": len(commits), "days": CHURN_DAYS, "note": f"last {CHURN_COMMITS} non-merge commits; commits_last_30d counts those within {CHURN_DAYS} days"},
                        "files": files, "source": "git-log"}


# ------------------------------------------------------------------ assemble
def run_xray(root: Path, untracked_all: bool = False) -> dict:
    ctx = Ctx(root)
    state = build_state(ctx)
    wt, internal = build_working_tree(ctx, state, untracked_all)
    aob = _ahead_of_base(ctx, state)
    state["tracked_ahead_of_base"] = aob

    entries = internal["entries"]
    all_paths = uniq([e.path for e in entries if not e.untracked] + internal["untracked_files"])
    ns_by_path = {n["path"]: n for n in internal["ns"]}
    by_path = {e.path: e for e in entries}
    cluster_in = []
    for p in all_paths:
        n = ns_by_path.get(p, {})
        e = by_path.get(p)
        cluster_in.append({"path": p, "added": n.get("added", 0), "deleted": n.get("deleted", 0), "status": _status_char(e) if e else ("M" if n else "?")})

    scan = scan_change_set(ctx, state, internal)
    recent, churn = build_recent_and_churn(ctx, state, set(all_paths))

    tag = lambda t: [p for p in all_paths if t in ctx.tags(p)]  # noqa: E731
    secret_files = [p for p in all_paths if is_secret_file(p, ctx.tags(p))]
    sensitive = [p for p in all_paths if "sensitive" in ctx.tags(p) and not is_secret_file(p, ctx.tags(p))]
    manifests_changed = any(("dependency" in ctx.tags(p)) for p in all_paths)
    tracked = tracked_set(root, ctx) if manifests_changed else set()
    drift = dependency_drift(all_paths, tracked)
    lock_drift = len(drift["manifest_without_lockfile"]) + len(drift["lockfile_without_manifest"])
    tests = tests_vs_source(all_paths, ctx)
    clusters, csource = cluster_entries(cluster_in, ctx)
    mixed = mixed_concerns(clusters)
    total_lines = wt["numstat_totals"]["added"] + wt["numstat_totals"]["deleted"]
    large = [{"path": n["path"], "changed_lines": n["added"] + n["deleted"]} for n in internal["ns"] if n["added"] + n["deleted"] > 500]

    signals = {
        "migrations": cap(tag("migration")), "schema": cap(tag("schema")), "config": cap(tag("config")),
        "infra": cap(tag("infra")), "ci": cap(tag("ci")),
        "dependency_manifests": cap(p for p in tag("dependency") if "lockfile" not in ctx.tags(p)),
        "lockfiles": cap(tag("lockfile")),
        "dependency_drift": drift,
        "tests": tests,
        "secrets": {"secret_file_paths": cap(secret_files), "content_findings": scan,
                    "note": "Values are never printed. Content scan covers added lines of staged/unstaged changes and untracked text files."},
        "generated_files": cap(tag("generated")),
        "large_changes": large[:10],
        "binary_files": cap(uniq(internal["binaries"] + scan["binary_untracked"])),
        "mixed_concerns": mixed,
    }

    facts = {
        "conflicted": len(internal["conflicted"]), "operation": (state["operation"] or {}).get("type"),
        "dirty": len(internal["dirty_paths"]), "secret_files": secret_files, "secret_findings": scan["count"],
        "files_changed": len(all_paths), "lines_changed": total_lines, "sensitive": len(sensitive),
        "migrations": len(tag("migration")), "ahead": state["ahead"], "behind": state["behind"],
        "has_upstream": bool(state["upstream"]), "ahead_of_base": (aob or {}).get("ahead"), "base": (aob or {}).get("base"),
        "lock_drift": lock_drift, "detached": state["detached"], "mixed": mixed["flag"], "missing_tests": tests["potential_missing_tests"],
    }
    risk = risk_mod.evaluate(facts)
    rec = risk_mod.recommend(facts, risk)

    return {
        "command": "xray",
        "repo": str(root),
        "state": state,
        "working_tree": wt,
        "recent_commits": recent,
        "high_churn": churn,
        "sensitive_paths": {**cap(sensitive), "secret_files": cap(secret_files)},
        "signals": signals,
        "clusters": {"source": csource, "items": clusters, "atomic_commit_opportunities": commit_opportunities(clusters)},
        "recovery": {
            "reflog_available": state["reflog"]["available"], "reflog_entries": state["reflog"]["count"],
            "stashes": state["stashes"]["count"], "worktrees": state["worktrees"]["count"],
            "dangling_commits": "not checked (xray never runs fsck); use `warp.py rescue scan` if work looks lost",
            "orig_head": _exists_git_file(ctx, "ORIG_HEAD"),
        },
        "risk": risk,
        "recommendation": rec,
        "warnings": ctx.warnings,
    }


def _exists_git_file(ctx: Ctx, name: str) -> bool:
    gd = ctx.safe("git-dir", lambda: git.git_dir(ctx.root))
    return bool(gd and (Path(gd) / name).exists())
