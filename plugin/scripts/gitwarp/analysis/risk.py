"""Deterministic risk rules for X-Ray and PR analysis.  No numeric scores: only LOW / MEDIUM / HIGH.

X-Ray rules (level = highest triggered; each trigger yields a driver with its evidence count)
  HIGH
    conflicted-paths     >=1 conflicted (unmerged) path
    rebase-dirty         rebase in progress AND >=1 staged/unstaged/conflicted path
    secret-material      >=1 secret-file path (.env, *.pem, id_rsa, ...) OR >=1 secret-looking added line
    large-sensitive-migration  more than HIGH_FILES files changed AND sensitive paths AND migrations all present
  MEDIUM
    operation-in-progress  merge/rebase/cherry-pick/revert/bisect underway (not already HIGH-explained)
    diverged             ahead>0 AND behind>0 versus upstream
    sensitive-paths      >=1 changed path tagged sensitive (auth/payment/crypto/... or configured)
    migration            >=1 migration file changed
    lockfile-drift       lockfile changed without manifest, or manifest changed with an untouched lockfile
    no-upstream-ahead    no upstream and HEAD has commits not in the default base
    large-diff           more than LARGE_FILES files or LARGE_LINES changed lines
    detached-dirty       detached HEAD with uncommitted changes
  LOW otherwise.
PR rules use the same vocabulary on the merge-base range (see ``evaluate_pr``).
"""
from __future__ import annotations

HIGH_FILES = 25
LARGE_FILES = 40
LARGE_LINES = 1000
RULES_VERSION = 1


def _level(drivers: list) -> str:
    levels = {d["level"] for d in drivers}
    return "HIGH" if "HIGH" in levels else "MEDIUM" if "MEDIUM" in levels else "LOW"


def _finish(drivers: list, low_text: str) -> dict:
    level = _level(drivers)
    order = {"HIGH": 0, "MEDIUM": 1}
    drivers = sorted(drivers, key=lambda d: order.get(d["level"], 2))
    return {
        "level": level,
        "drivers": [f"{d['level']}: {d['reason']}" for d in drivers] or [low_text],
        "driver_details": drivers,
        "rules_version": RULES_VERSION,
    }


def evaluate(f: dict) -> dict:
    """Evaluate working-tree risk from a flat ``facts`` dict (see xray.build_risk_facts)."""
    d: list = []

    def add(level, id_, count, reason):
        d.append({"id": id_, "level": level, "count": count, "reason": reason})

    if f.get("conflicted", 0) > 0:
        add("HIGH", "conflicted-paths", f["conflicted"], f"{f['conflicted']} conflicted path(s) need resolution")
    if f.get("operation") == "rebase" and f.get("dirty", 0) > 0:
        add("HIGH", "rebase-dirty", f["dirty"], f"rebase in progress with {f['dirty']} dirty path(s)")
    n_secret = len(f.get("secret_files", [])) + f.get("secret_findings", 0)
    if n_secret:
        add("HIGH", "secret-material", n_secret,
            f"{len(f.get('secret_files', []))} secret-file path(s) and {f.get('secret_findings', 0)} secret-looking added line(s) in the change set")
    if f.get("files_changed", 0) > HIGH_FILES and f.get("sensitive", 0) and f.get("migrations", 0):
        add("HIGH", "large-sensitive-migration", f["files_changed"],
            f"{f['files_changed']} files changed (> {HIGH_FILES}) including {f['sensitive']} sensitive path(s) and {f['migrations']} migration file(s)")
    op = f.get("operation")
    if op and not (op == "rebase" and f.get("dirty", 0) > 0) and not (f.get("conflicted", 0) > 0):
        add("MEDIUM", "operation-in-progress", 1, f"{op} in progress")
    if (f.get("ahead") or 0) > 0 and (f.get("behind") or 0) > 0:
        add("MEDIUM", "diverged", f["ahead"] + f["behind"], f"branch diverged from upstream: {f['ahead']} ahead, {f['behind']} behind")
    if f.get("sensitive", 0):
        add("MEDIUM", "sensitive-paths", f["sensitive"], f"{f['sensitive']} sensitive path(s) touched")
    if f.get("migrations", 0):
        add("MEDIUM", "migration", f["migrations"], f"{f['migrations']} migration file(s) changed")
    if f.get("lock_drift", 0):
        add("MEDIUM", "lockfile-drift", f["lock_drift"], f"{f['lock_drift']} lockfile/manifest pair(s) changed out of sync")
    if not f.get("has_upstream") and (f.get("ahead_of_base") or 0) > 0:
        add("MEDIUM", "no-upstream-ahead", f["ahead_of_base"], f"no upstream configured and {f['ahead_of_base']} commit(s) ahead of {f.get('base') or 'base'}")
    if f.get("files_changed", 0) > LARGE_FILES or f.get("lines_changed", 0) > LARGE_LINES:
        add("MEDIUM", "large-diff", f.get("files_changed", 0), f"large change set: {f.get('files_changed', 0)} files, {f.get('lines_changed', 0)} changed lines")
    if f.get("detached") and f.get("dirty", 0) > 0:
        add("MEDIUM", "detached-dirty", f["dirty"], f"detached HEAD with {f['dirty']} uncommitted path(s); new commits would not be on a branch")
    return _finish(d, "No risk rule triggered (no conflicts, secrets, sensitive paths, migrations, divergence or oversized diff)")


def recommend(f: dict, risk: dict) -> list:
    """Ordered recommendation skeletons: ``{id, why, inspect}`` (``inspect`` is a read-only command)."""
    r: list = []

    def add(id_, why, inspect):
        r.append({"id": id_, "why": why, "inspect": inspect})

    if f.get("conflicted", 0):
        add("resolve-conflicts-first", f"{f['conflicted']} conflicted path(s)", "git diff --name-only --diff-filter=U")
    elif f.get("operation"):
        add("finish-or-abort-operation", f"{f['operation']} in progress", "git status")
    if f.get("secret_files") or f.get("secret_findings"):
        add("review-secrets", "secret-file paths or secret-looking added lines present; do not commit or push them", "git status --short")
    if (f.get("behind") or 0) > 0:
        add("rebase-or-merge-upstream", f"{f['behind']} upstream commit(s) not in branch" + (f"; also {f['ahead']} local" if f.get("ahead") else ""),
            "git log --oneline HEAD..@{upstream}")
    if not f.get("has_upstream") and (f.get("ahead_of_base") or 0) > 0:
        add("set-upstream-before-push", "branch has local commits and no upstream", "git branch -vv")
    if f.get("mixed"):
        add("split-commits", "change set spans multiple independent concerns", "git diff --stat")
    if f.get("missing_tests"):
        add("add-tests", "source changed with no test change", "git diff --stat")
    if f.get("lock_drift"):
        add("sync-lockfile", "manifest/lockfile changed out of sync", "git diff --stat")
    if f.get("migrations"):
        add("review-migration", "migration files changed", "git diff --stat")
    if f.get("sensitive"):
        add("review-sensitive-changes", "sensitive paths touched", "git diff --stat")
    if f.get("detached"):
        add("create-branch-for-detached-work", "HEAD is detached", "git branch --show-current")
    if not r:
        add("no-action-needed", "no risk rule triggered", "git status")
    return r


def evaluate_pr(f: dict) -> dict:
    """PR-range risk areas. ``f`` keys: secret_files, secret_findings, migrations, destructive_migration_stmts,
    lock_drift, sensitive, behind_base, files_changed, lines_changed, test_only_markers, missing_tests, breaking_api."""
    d: list = []

    def add(level, id_, count, reason):
        d.append({"id": id_, "level": level, "count": count, "reason": reason})

    n_secret = len(f.get("secret_files", [])) + f.get("secret_findings", 0)
    if n_secret:
        add("HIGH", "secret-material", n_secret, f"{len(f.get('secret_files', []))} secret-file path(s) and {f.get('secret_findings', 0)} secret-looking added line(s) in the range")
    if f.get("destructive_migration_stmts", 0):
        add("HIGH", "destructive-migration", f["destructive_migration_stmts"], f"{f['destructive_migration_stmts']} destructive statement(s) (DROP/TRUNCATE/DELETE) added in migration files")
    if f.get("migrations", 0):
        add("MEDIUM", "migration", f["migrations"], f"{f['migrations']} migration file(s) changed")
    if f.get("lock_drift", 0):
        add("MEDIUM", "lockfile-drift", f["lock_drift"], f"{f['lock_drift']} lockfile/manifest pair(s) changed out of sync")
    if f.get("sensitive", 0):
        add("MEDIUM", "sensitive-paths", f["sensitive"], f"{f['sensitive']} sensitive path(s) touched")
    if f.get("breaking_api", 0):
        add("MEDIUM", "api-surface", f["breaking_api"], f"{f['breaking_api']} exported symbol(s) removed or with changed signature (heuristic)")
    if f.get("test_only_markers", 0):
        add("MEDIUM", "tests-disabled", f["test_only_markers"], f"{f['test_only_markers']} added .only/skip marker(s) can silently disable tests")
    if f.get("missing_tests"):
        add("MEDIUM", "missing-tests", f.get("source_files_changed", 1), "source changed with no test files changed in the range")
    if f.get("behind_base", 0):
        add("MEDIUM", "behind-base", f["behind_base"], f"branch is {f['behind_base']} commit(s) behind base; merge result may differ from what was reviewed")
    if f.get("files_changed", 0) > LARGE_FILES or f.get("lines_changed", 0) > LARGE_LINES:
        add("MEDIUM", "large-diff", f.get("files_changed", 0), f"large change: {f.get('files_changed', 0)} files, {f.get('lines_changed', 0)} changed lines")
    return _finish(d, "No risk rule triggered for this commit range")
