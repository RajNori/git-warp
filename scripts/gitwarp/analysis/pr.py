"""``warp.py pr [BASE]``: analysis of ``merge-base(BASE, HEAD)..HEAD`` (three-dot semantics), never the working tree."""
from __future__ import annotations

import posixpath
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from gitwarp.core import git, revisions
from gitwarp.core.redact import redact

from . import risk as risk_mod
from . import secrets
from .common import (Ctx, cap, cluster_entries, commit_opportunities, dependency_drift, is_secret_file, mixed_concerns, tests_vs_source,
                     uniq)
from .diffscan import api_surface_changes, find_debug_leftovers, parse_diff

MAX_COMMITS = 500
COMMIT_LIST_CAP = 50
MAX_DIFF_BYTES = 3_000_000
LARGE_BLOB = 1_048_576
BLOB_CHECK_LIMIT = 300

_CONVENTIONAL = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|tests|build|ci|chore|revert)(\([^)]+\))?!?: \S")
_FIXUP = re.compile(r"^(fixup|squash|amend)! ")
_WIP = re.compile(r"^(?:wip\b|\[wip\]|wip:)|\bWIP\b|^(?:tmp|temp|asdf|xxx|\.+|-+|update|changes|stuff)$", re.I)
_TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b|(?<![\w&])#\d+\b")
_CI_FILES = (".gitlab-ci.yml", "Jenkinsfile", ".travis.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".circleci/config.yml", "appveyor.yml")
_TEST_DIR = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|cypress)/")
_TEST_CONFIGS = ("pytest.ini", "tox.ini", "noxfile.py", "jest.config.js", "jest.config.ts", "vitest.config.ts", "vitest.config.js", "karma.conf.js", "phpunit.xml", ".rspec")
_DESTRUCTIVE = re.compile(r"(?i)\bDROP\s+(?:TABLE|COLUMN|INDEX|SCHEMA|DATABASE|CONSTRAINT)\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\bALTER\s+TABLE\s+\S+\s+DROP\b|\bop\.drop_(?:table|column)\b|\bmigrations\.(?:RemoveField|DeleteModel)\b|\bdrop_(?:table|column)\b|\bremove_column\b")
_REVERSIBLE = re.compile(r"(?i)downgrade|def down\b|\bdown\s*[:(=]|rollback|reverse|\+goose Down|--\s*down\b|\.down\b")
_PRIORITY_STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type-changed"}


def _count(ctx: Ctx, include: list, exclude: list) -> Optional[int]:
    return ctx.safe("rev-list --count", lambda: git.count_commits(include, exclude, cwd=ctx.root))


def _name_status(ctx: Ctx, mb: str) -> dict:
    r = ctx.safe("name-status", lambda: git.run(["diff", "--name-status", "-z", "-M", mb, "HEAD"], cwd=ctx.root, timeout=30))
    out: dict = {}
    if r is None or not r.ok:
        return out
    parts = r.stdout.split("\x00")
    i = 0
    while i < len(parts):
        st = parts[i]
        i += 1
        if not st:
            continue
        if st[0] in "RC" and i + 1 < len(parts):
            out[parts[i + 1]] = {"status": st[0], "orig": parts[i]}
            i += 2
        elif i < len(parts):
            out[parts[i]] = {"status": st[0]}
            i += 1
    return out


def _commit_analysis(ctx: Ctx, mb: str, total: Optional[int]) -> tuple:
    commits = ctx.safe("log", lambda: git.log_commits(f"{mb}..HEAD", limit=MAX_COMMITS, cwd=ctx.root, timeout=45), [])
    truncated = bool(total and total > len(commits))
    if truncated:
        ctx.warnings.append(f"commits: range has {total} commits; only the newest {len(commits)} were analysed for hygiene")
    hygiene, nonconf, conf = [], [], 0
    for c in commits:
        subj = c.subject
        issues = []
        if _FIXUP.match(subj):
            issues.append("fixup-or-squash-commit")
        if _WIP.search(subj):
            issues.append("wip-commit")
        if not subj.strip():
            issues.append("empty-subject")
        elif len(subj) > 100:
            issues.append("long-subject")
        if issues:
            hygiene.append({"sha": c.short, "subject": secrets.strong_redact(subj)[:120], "issues": issues})
        if c.is_merge or _CONVENTIONAL.match(subj) or _FIXUP.match(subj):
            if _CONVENTIONAL.match(subj):
                conf += 1
        else:
            nonconf.append({"sha": c.short, "subject": secrets.strong_redact(subj)[:120]})
    authors = Counter(c.author_name for c in commits)
    info = {
        "count": total if total is not None else len(commits),
        "analysed": len(commits), "truncated": truncated,
        "merge_commits": sum(1 for c in commits if c.is_merge),
        "authors": [{"name": n, "commits": k} for n, k in authors.most_common(10)],
        "items": [{"sha": c.short, "subject": secrets.strong_redact(c.subject)[:120], "author": c.author_name, "date": c.author_date, "merge": c.is_merge}
                  for c in commits[:COMMIT_LIST_CAP]],
        "items_truncated": len(commits) > COMMIT_LIST_CAP,
        "conventional_commits": {"conforming": conf, "nonconforming": nonconf[:20], "nonconforming_total": len(nonconf)},
    }
    return commits, info, hygiene


def _tree_evidence(tree: set) -> dict:
    ci = sorted(p for p in tree if p.startswith(".github/workflows/") or p in _CI_FILES)
    dirs, cfgs = set(), []
    for p in tree:
        m = _TEST_DIR.search(p)
        if m and len(dirs) < 10:
            dirs.add(p[:m.end() - 1])
        if posixpath.basename(p) in _TEST_CONFIGS and len(cfgs) < 10:
            cfgs.append(p)
    return {"ci_config_files": ci[:15], "test_directories": sorted(dirs)[:10], "test_runner_configs": sorted(cfgs)}


def _large_blobs(ctx: Ctx, status: dict) -> list:
    paths = [p for p, s in status.items() if s["status"] != "D" and "\n" not in p][:BLOB_CHECK_LIMIT]
    if not paths:
        return []
    r = ctx.safe("cat-file", lambda: git.run(["cat-file", "--batch-check=%(objectsize)"], cwd=ctx.root, input="".join(f"HEAD:{p}\n" for p in paths), timeout=30))
    if r is None or not r.ok:
        return []
    out = []
    for p, line in zip(paths, r.stdout.splitlines()):
        if line.strip().isdigit() and int(line) > LARGE_BLOB:
            out.append({"path": p, "size_bytes": int(line)})
    return out


def _type_hint(ctx: Ctx, paths: list, conv: Counter) -> list:
    hints = []
    kinds = [ctx.tags(p) for p in paths]
    if paths:
        if all("docs" in t for t in kinds):
            hints.append("docs-only")
        if all("test" in t for t in kinds):
            hints.append("tests-only")
        if all(t & {"dependency", "lockfile"} for t in kinds):
            hints.append("dependency-update")
        if all(t & {"ci", "infra"} for t in kinds):
            hints.append("ci-or-infra-only")
    if conv:
        hints.append("conventional types in commits: " + ", ".join(f"{k}={v}" for k, v in conv.most_common(4)))
    return hints or ["unknown: read the diff"]


def run_pr(root: Path, base_arg: Optional[str] = None) -> dict:
    ctx = Ctx(root)
    head = ctx.safe("head", lambda: git.head_sha(root))
    if head is None:
        return {"error": "repository has no commits yet (unborn branch); nothing to compare against a base", "hint": "make a first commit, then re-run"}
    branch = ctx.safe("branch", lambda: git.current_branch(root))
    shallow = bool(ctx.safe("shallow", lambda: git.is_shallow(root), False))
    auto = base_arg is None
    base = base_arg
    if auto:
        base = ctx.safe("default-branch", lambda: git.default_branch(root))
        if not base:
            names = [b["name"] for b in ctx.safe("branches", lambda: git.branches(root), [])][:15]
            return {"error": "could not auto-detect a base branch (no origin/HEAD, origin/main|master, main or master)",
                    "hint": "pass the base explicitly: `warp.py pr <BASE>` or `--base <REF>`", "local_branches": names}
    try:
        base_rev = revisions.resolve(base, root)          # the ONE normalization: everything below uses the sha
    except revisions.RevisionError as e:
        if e.reason in revisions.SYNTAX_REASONS:
            return {"error": f"invalid base ref: {base!r} (refs must not start with '-' or contain control characters)", "reason": e.reason}
        if e.reason == "ambiguous":
            return {"error": f"base ref is ambiguous: {base!r}", "reason": e.reason, "hint": "use a longer id or the full ref name"}
        return {"error": f"base ref not found: {base!r}", "reason": e.reason, "hint": "check the name, or `git fetch` if it is a remote branch"}
    base_sha = base_rev.sha
    mb = ctx.safe("merge-base", lambda: git.merge_base(base_rev, head, root))
    if not mb:
        msg = ("no merge base found between base and HEAD" + (" (shallow clone: history is truncated)" if shallow else " (unrelated histories)"))
        return {"error": msg, "base": {"ref": base, "sha": base_sha},
                "hint": "run `git fetch --unshallow` (or deepen the fetch) and retry" if shallow else "the branches share no history; a PR comparison is not meaningful"}
    if shallow:
        ctx.warnings.append("shallow clone: merge-base found, but commit list and ahead/behind may be truncated")

    total = _count(ctx, [head], [mb])
    behind_base = _count(ctx, [base_rev], [head])
    up = ctx.safe("upstream", lambda: git.upstream(root)) if branch else None
    ab = ctx.safe("ahead/behind", lambda: git.ahead_behind(root, up)) if up else None
    if ab is None:
        up_state = "none"
    elif ab[0] and ab[1]:
        up_state = "diverged"
    elif ab[0]:
        up_state = "ahead-of-upstream (unpushed commits)"
    elif ab[1]:
        up_state = "behind-upstream"
    else:
        up_state = "in-sync"
    relation = {
        "base": {"ref": base, "sha": base_sha, "auto_detected": auto}, "merge_base": mb,
        "head": {"sha": head, "branch": branch, "detached": branch is None},
        "behind_base": behind_base, "is_behind_base": bool(behind_base), "ahead_of_base": total,
        "upstream": {"ref": up, "ahead": ab[0] if ab else None, "behind": ab[1] if ab else None, "state": up_state},
    }

    # what is NOT in this analysis
    entries = ctx.safe("status", lambda: git.working_tree_status(root), [])
    not_included = {
        "note": "This analysis covers committed changes in merge-base..HEAD only; the items below are not part of it.",
        "staged": cap(e.path for e in entries if e.staged and not e.conflicted),
        "unstaged": cap(e.path for e in entries if e.unstaged and not e.conflicted),
        "untracked": cap(e.path for e in entries if e.untracked),
        "conflicted": cap(e.path for e in entries if e.conflicted),
        "operation_in_progress": ctx.safe("operation", lambda: git.repo_operation(root)),
    }
    not_included["has_uncommitted_changes"] = bool(entries)

    result = {"command": "pr", "repo": str(root), "status": "ok", **relation, "not_included": not_included}
    if not total:
        result["status"] = "no-commits"
        result["message"] = (f"HEAD has no commits beyond {base}" + (f"; the branch is {behind_base} commit(s) behind it, so there is nothing to propose" if behind_base else " (HEAD equals the base or is contained in it)")
                             + ". Nothing to put in a PR.")
        result["warnings"] = ctx.warnings
        return result

    commits, cinfo, hygiene = _commit_analysis(ctx, mb, total)

    # file-level facts
    ns = ctx.safe("numstat", lambda: git.numstat(root, args=["-M", mb, "HEAD"]), [])
    status = _name_status(ctx, mb)
    files = [{"path": n["path"], "added": n["added"], "deleted": n["deleted"], "binary": n["binary"],
              "status": _PRIORITY_STATUS.get(status.get(n["path"], {}).get("status", "M"), "modified"),
              **({"renamed_from": status[n["path"]]["orig"]} if status.get(n["path"], {}).get("orig") else {})} for n in ns]
    paths = uniq(n["path"] for n in ns)
    added = sum(n["added"] for n in ns)
    deleted = sum(n["deleted"] for n in ns)
    ranked = sorted((f for f in files if not f["binary"]), key=lambda f: f["added"] + f["deleted"], reverse=True)

    # patch scan (bounded)
    r = ctx.safe("patch-scan", lambda: git.run(["-c", "core.quotepath=off", "diff", "-U0", "--no-color", "--no-ext-diff", "--no-textconv", "-M", mb, "HEAD"],
                                               cwd=root, timeout=60))
    records, trunc_files, patch_truncated = [], set(), False
    if r is not None and r.ok:
        text = r.stdout
        if len(text) > MAX_DIFF_BYTES:
            text, patch_truncated = text[:MAX_DIFF_BYTES], True
            ctx.warnings.append("patch scan: diff larger than 3MB; heuristics cover only the first 3MB")
        records, trunc_files = parse_diff(text)
        if trunc_files:
            patch_truncated = True
            ctx.warnings.append(f"patch scan: {len(trunc_files)} file(s) exceeded the per-file line cap; later lines were not scanned")
    else:
        ctx.warnings.append("patch scan unavailable; debug/secret/API heuristics are empty")

    skip_secret = lambda p: bool(ctx.tags(p) & {"lockfile", "generated"})  # noqa: E731
    sec = secrets.scan_records(records, skip_secret)
    for f in sec["findings"]:
        f["in_test_file"] = "test" in ctx.tags(f["path"])
    secret_files = [p for p in paths if is_secret_file(p, ctx.tags(p)) and status.get(p, {}).get("status") != "D"]
    debug = find_debug_leftovers(records, ctx, secrets.safe_snippet)
    api = api_surface_changes(records, ctx)
    schema_files = [p for p in paths if "schema" in ctx.tags(p) and "migration" not in ctx.tags(p)]

    # tags
    tag = lambda t: [p for p in paths if t in ctx.tags(p)]  # noqa: E731
    tag_summary = {}
    for t in ("migration", "schema", "dependency", "lockfile", "config", "infra", "ci", "docs", "test", "sensitive", "generated", "secret-file"):
        lst = tag(t)
        if lst:
            tag_summary[t] = {"count": len(lst), "paths": lst[:15], "truncated": len(lst) > 15}

    tree = set(ctx.safe("ls-tree", lambda: git.tracked_files(root), []))
    drift = dependency_drift(paths, tree)
    lock_drift = len(drift["manifest_without_lockfile"]) + len(drift["lockfile_without_manifest"])
    tests = tests_vs_source(paths, ctx)
    cluster_in = [{"path": f["path"], "added": f["added"], "deleted": f["deleted"], "status": {"added": "A", "deleted": "D", "renamed": "R"}.get(f["status"], "M")} for f in files]
    clusters, csource = cluster_entries(cluster_in, ctx)
    mixed = mixed_concerns(clusters)

    # rollback facts
    migs = []
    for p in tag("migration"):
        st = status.get(p, {}).get("status", "M")
        body = "\n".join(t for (pp, s, _l, t) in records if pp == p and s == "+")
        migs.append({"path": p, "status": _PRIORITY_STATUS.get(st, st), "reversible_signal": bool(_REVERSIBLE.search(body)) if st != "D" else None})
    destructive = sum(1 for (pp, s, _l, t) in records if s == "+" and ("migration" in ctx.tags(pp) or pp.endswith(".sql")) and _DESTRUCTIVE.search(t))
    large_blobs = _large_blobs(ctx, status)
    very_large = [{"path": f["path"], "changed_lines": f["added"] + f["deleted"]} for f in ranked if f["added"] + f["deleted"] > 1000][:10]

    # facts_for_prose
    conv = Counter(m.group(1) for c in commits if (m := _CONVENTIONAL.match(c.subject)))
    refs = uniq(_TICKET.findall(" ".join([branch or ""] + [c.subject + " " + c.body for c in commits[:100]])))
    breaking = [c.short for c in commits if re.search(r"^[a-z]+(\([^)]+\))?!:", c.subject) or "BREAKING CHANGE" in c.body]
    kind_counts = Counter(c["kind"] for c in clusters)

    pr_facts = {
        "secret_files": secret_files, "secret_findings": sec["count"], "migrations": len(tag("migration")), "destructive_migration_stmts": destructive,
        "lock_drift": lock_drift, "sensitive": len([p for p in tag("sensitive") if p not in secret_files]), "behind_base": behind_base or 0,
        "files_changed": len(paths), "lines_changed": added + deleted,
        "test_only_markers": debug["counts"].get("test-only", 0) + debug["counts"].get("test-skip", 0),
        "missing_tests": tests["potential_missing_tests"], "source_files_changed": tests["source_files_changed"],
        "breaking_api": api["counts"]["removed"] + api["counts"]["signature_changed"],
    }
    risk = risk_mod.evaluate_pr(pr_facts)

    hygiene_findings = []
    if hygiene:
        hygiene_findings.append({"id": "commit-hygiene", "count": len(hygiene), "detail": hygiene[:20]})
    if debug["total"]:
        hygiene_findings.append({"id": "debug-leftovers", "count": debug["total"], "kinds": debug["counts"]})
    if sec["count"]:
        hygiene_findings.append({"id": "secret-looking-added-lines", "count": sec["count"]})
    if secret_files:
        hygiene_findings.append({"id": "secret-file-in-range", "count": len(secret_files)})
    if tag("generated") or large_blobs or any(f["binary"] for f in files):
        hygiene_findings.append({"id": "generated-large-or-binary-files", "count": len(tag("generated")) + len(large_blobs) + sum(1 for f in files if f["binary"])})
    if cinfo["merge_commits"]:
        hygiene_findings.append({"id": "merge-commits-in-range", "count": cinfo["merge_commits"]})
    if mixed["flag"]:
        hygiene_findings.append({"id": "unrelated-changes", "count": len(clusters)})

    result.update({
        "commits": cinfo,
        "files": {"count": len(files), "items": files[:200], "truncated": len(files) > 200},
        "totals": {"files": len(files), "added": added, "deleted": deleted, "binary_files": sum(1 for f in files if f["binary"])},
        "largest_changes": [{"path": f["path"], "added": f["added"], "deleted": f["deleted"]} for f in ranked[:5]],
        "clusters": {"source": csource, "items": clusters, "atomic_commit_opportunities": commit_opportunities(clusters)},
        "unrelated_changes": mixed,
        "tags": tag_summary,
        "dependencies": drift,
        "api_surface": {**api, "schema_files_changed": cap(schema_files)},
        "debug_leftovers": debug,
        "secrets": {**sec, "secret_file_paths": cap(secret_files), "note": "Added lines of the net range diff only; values are never printed."},
        "artifacts": {"binary_files": cap(p["path"] for p in files if p["binary"]), "generated_files": cap(tag("generated")),
                      "large_files": large_blobs, "very_large_diffs": very_large},
        "tests": tests,
        "rollback": {
            "migrations_present": bool(migs), "migrations": migs[:20],
            "destructive_statements_added": destructive,
            "note": "reversible_signal is a keyword heuristic (downgrade/down/rollback/reverse); verify by reading the migration. Applied migrations may need data restore, not just a revert.",
            "dependencies_changed": bool(tag("dependency")), "dependency_files": tag("dependency")[:15],
            "config_files_changed": tag("config")[:15], "infra_or_ci_changed": uniq(tag("infra") + tag("ci"))[:15],
        },
        "testing_evidence": {"found_in_repo": _tree_evidence(tree), "test_files_changed_in_range": tests["test_files_changed"], "ran_by_git_warp": False,
                             "note": "Git Warp never runs tests. State 'not run' unless you actually ran them and saw the output."},
        "patch_scan": {"truncated": patch_truncated, "bytes_cap": MAX_DIFF_BYTES, "scope": "added lines of merge-base..HEAD net diff (not per-commit)"},
        "hygiene": hygiene_findings,
        "facts_for_prose": {
            "size": {"commits": cinfo["count"], "files": len(files), "added": added, "deleted": deleted},
            "type_hints": _type_hint(ctx, paths, conv),
            "scope_hints": [f"{k}: {v} cluster(s)" for k, v in kind_counts.most_common()],
            "cluster_labels": [c["label"] for c in clusters][:10],
            "ticket_refs": refs[:10],
            "breaking_change_markers": breaking[:10],
            "branch": branch,
            "commit_subjects": [i["subject"] for i in cinfo["items"][:30]],
        },
        "risk": risk,
        "warnings": ctx.warnings,
    })
    return result
