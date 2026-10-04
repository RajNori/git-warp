"""``warp.py commits``: analyse the change set and PROPOSE atomic commits.  Strictly read-only.

Nothing here stages, commits or edits anything; the ``commands`` it emits are text for the user.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..core import git
from ..core.config import load_config
from ..core.paths import classify_path
from . import cluster as cl
from .common import read_text, repo_state, state_warnings, status_letter

ORDER_RANK = {"deps": 0, "migration": 1, "config": 2, "source": 3, "ui": 4, "test": 5, "infra": 6, "docs": 7}
MAX_HUNK_FILES = 40
MAX_HUNKS_PER_FILE = 20
# output bounds (the plugin output is read by a model and by tools: it must stay small on huge change sets)
MAX_LISTED_FILES = 500        # entries in ``files``
MAX_PATHS_PER_LIST = 200      # paths inside one cluster / proposal / flag list / staging command
MAX_MIXED = 200               # ``mixed_concerns`` entries
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")

CONCERN_MAP = (("test", "test"), ("docs", "docs"), ("infra", "infra/ci"), ("ci", "infra/ci"), ("migration", "db-schema"),
               ("schema", "db-schema"), ("dependency", "dependencies"), ("lockfile", "dependencies"), ("ui", "code"), ("source", "code"))


def _count_lines(path: Path) -> tuple:
    t = read_text(path, 2_000_000)
    if t is None:
        return 0, True
    return (t.count("\n") + (0 if t.endswith("\n") or not t else 1)), False


def collect_entries(root: Path, staged_only: bool, warnings: list) -> tuple:
    """Return ``(entries, meta)``; entries follow the cluster contract plus ``orig_path``/``binary``."""
    try:
        status = git.working_tree_status(root, untracked="all")
    except git.GitError as e:
        warnings.append(f"git status failed: {e}")
        return [], {}
    has_head = git.head_sha(root) is not None
    if staged_only:
        status = [s for s in status if s.staged and not s.conflicted]
        nargs = ["--cached", "--"] if has_head else ["--cached", "--"]
    else:
        nargs = ["HEAD", "--"] if has_head else ["--cached", "--"]
    stats = {n["path"]: n for n in git.numstat(root, nargs)}
    entries, meta = [], {}
    for s in status:
        letter = status_letter(s.xy)
        n = stats.get(s.path)
        binary = bool(n and n["binary"])
        if n:
            added, deleted = n["added"], n["deleted"]
        elif letter == "?":
            added, binary = _count_lines(root / s.path)
            deleted = 0
        else:
            added = deleted = 0
        e = {"path": s.path, "added": added, "deleted": deleted, "status": letter}
        if s.orig_path:
            e["orig_path"] = s.orig_path
        entries.append(e)
        meta[s.path] = {"xy": s.xy, "staged": s.staged, "unstaged": s.unstaged or s.untracked, "binary": binary,
                        "conflicted": s.conflicted, "orig_path": s.orig_path}
    return entries, meta


def _scope(label: str) -> str:
    base = label.split(" (")[0]
    if base.startswith("ui/"):
        base = base[3:]
    last = base.split("/")[-1]
    last = re.sub(r"[^\w.-]", "", last)
    return "" if last in ("", "root", "rootsource", "rootui") or label.startswith("root ") else last


def _message(c: dict) -> tuple:
    kind, scope = c["kind"], _scope(c["label"])
    all_deleted = c["statuses"] == {"D"}
    if kind == "test":
        t, decide = "test", False
    elif kind == "docs":
        t, scope, decide = "docs", "", False
    elif kind == "infra":
        t, scope, decide = ("ci" if c["label"] == "ci" else "chore"), ("" if c["label"] == "ci" else "infra"), False
    elif kind == "deps":
        t, scope, decide = "build", "deps", False
    elif kind == "migration":
        t, scope, decide = "feat", "db", False
    elif kind == "config":
        t, scope, decide = "chore", "config", False
    else:
        t, decide = ("refactor|chore?" if all_deleted else "feat|fix?"), True
    summary = "remove <what was removed>" if all_deleted else "<describe the change>"
    head = f"{t}({scope})" if scope else t
    return t, f"{head}: {summary}", decide


def _hunks(root: Path, path: str, staged_only: bool, has_head: bool) -> list:
    args = ["diff", "-U0", "--no-color", "--no-ext-diff"] + (["--cached"] if staged_only or not has_head else ["HEAD"]) + ["--", path]
    r = git.run(args, cwd=root, timeout=20)
    out = []
    if not r.ok:
        return out
    for ln in r.stdout.splitlines():
        m = _HUNK_RE.match(ln)
        if m:
            out.append({"header": ln[:200], "old_start": int(m.group(1)), "new_start": int(m.group(3)),
                        "old_lines": int(m.group(2) or 1), "new_lines": int(m.group(4) or 1)})
    return out


def _concerns(tags: set) -> list:
    got = []
    for tag, name in CONCERN_MAP:
        if tag in tags and name not in got:
            got.append(name)
    if "config" in tags and not got:
        got.append("config")
    return got


def analyze(root: Path, staged_only: bool = False) -> dict:
    warnings: list = []
    cfg = load_config(root)
    warnings += cfg.warnings
    st = repo_state(root)
    warnings += state_warnings(st)
    if st["operation"]:
        warnings.append(f"a {st['operation']} is in progress; finish or abort it before committing")
    entries, meta = collect_entries(root, staged_only, warnings)
    has_head = st["head"] is not None

    conflicted = sorted(p for p, m in meta.items() if m["conflicted"])
    if conflicted:
        warnings.append(f"{len(conflicted)} file(s) have unresolved merge conflicts and are excluded: {', '.join(conflicted[:5])}")
    tags = {e["path"]: classify_path(e["path"], cfg) for e in entries}
    secrets = sorted(p for p, t in tags.items() if "secret-file" in t)
    generated = sorted(p for p, t in tags.items() if "generated" in t and "secret-file" not in t)
    usable = [e for e in entries if e["path"] not in conflicted]
    clusters = cl.cluster_paths(usable, cfg, repo=root)
    cid_of = {p: c.id for c in clusters for p in c.paths}
    emap = {e["path"]: e for e in entries}

    files = []
    for e in sorted(entries, key=lambda x: x["path"]):
        m = meta[e["path"]]
        files.append({"path": e["path"], "status": e["status"], "added": e["added"], "deleted": e["deleted"], "binary": m["binary"],
                      "staged": m["staged"], "unstaged": m["unstaged"], "tags": sorted(tags[e["path"]]),
                      "cluster": cid_of.get(e["path"]), **({"orig_path": m["orig_path"]} if m["orig_path"] else {})})

    # mixed concerns / ambiguity
    mixed = []
    for p, t in tags.items():
        if p in secrets or "generated" in t or p in conflicted:
            continue
        cs = _concerns(t)
        if len(cs) >= 2:
            mixed.append({"path": p, "concerns": cs, "severity": "info",
                          "note": "path matches several concerns; confirm it belongs in one commit"})
    by_concern: dict = {}
    for e in usable:
        by_concern.setdefault(cl.concern_of(tags[e["path"]]), []).append(e["path"])
    for t, cands in sorted(cl.pair_candidates(by_concern).items()):
        owners = {cid_of.get(c) for c in cands}
        if len(cands) > 1 and len(owners) > 1:
            mixed.append({"path": t, "concerns": ["test"], "severity": "warn",
                          "note": f"test could belong to more than one cluster: {', '.join(cands[:4])}"})
    partial = sorted(p for p, m in meta.items() if m["staged"] and m["unstaged"] and not m["conflicted"] and emap[p]["status"] != "?")
    for p in partial:
        mixed.append({"path": p, "concerns": [], "severity": "warn",
                      "note": "partially staged: part of this file's changes is staged and part is not; `git add -- file` would stage everything"})

    # hunks for split candidates
    hunk_info = {}
    cand = sorted((e for e in usable if e["status"] in ("M", "R") and not meta[e["path"]]["binary"] and e["path"] not in secrets
                   and "generated" not in tags[e["path"]]), key=lambda e: -(e["added"] + e["deleted"]))[:MAX_HUNK_FILES]
    for e in cand:
        if e["added"] + e["deleted"] < 2:
            continue
        hs = _hunks(root, e["path"], staged_only, has_head)
        if len(hs) >= 2:
            span = hs[-1]["new_start"] - hs[0]["new_start"]
            hunk_info[e["path"]] = {"count": len(hs), "span_lines": span, "split_candidate": len(hs) >= 3 or span > 40,
                                    "hunks": hs[:MAX_HUNKS_PER_FILE],
                                    "hint": "if these hunks are unrelated, Claude may propose `git add -p -- <path>` to split them; the plugin never runs it"}

    # proposals
    props = []
    sc_staged = {p for p, m in meta.items() if m["staged"]}
    for c in clusters:
        if c.kind == "generated" or c.label == "secret-looking files":
            continue
        d = c.to_dict()
        d["statuses"] = {emap[p]["status"] for p in c.paths}
        t, msg, decide = _message(d)
        props.append({"cluster": c.id, "label": c.label, "kind": c.kind, "type": t, "message": msg, "needs_type_decision": decide,
                      "files": c.paths, "added": c.added, "deleted": c.deleted, "_rank": ORDER_RANK.get(c.kind, 9)})
    props.sort(key=lambda x: (x["_rank"], x["cluster"]))
    commands = []
    for i, pr in enumerate(props, 1):
        pr["order"] = i
        later = {p for q in props[i:] for p in q["files"]}
        stage_paths = []
        for p in pr["files"]:
            stage_paths.append(p)
            if meta[p]["orig_path"]:
                stage_paths.append(meta[p]["orig_path"])
        unstage = sorted(p for p in later if p in sc_staged)
        cmds = []
        if unstage:
            cmds.append("git restore --staged -- " + " ".join(shlex.quote(p) for p in unstage))
        cmds.append("git add -- " + " ".join(shlex.quote(p) for p in stage_paths))
        cmds.append("git diff --cached --stat")
        cmds.append("git commit -m " + shlex.quote(pr["message"]))
        pr["commands"] = cmds
        pr["order_reason"] = {"deps": "dependencies first so later code can rely on them", "migration": "schema/migrations before the code that uses them",
                              "config": "configuration before code that reads it", "source": "implementation (with its tests when paired)",
                              "ui": "UI after the logic it displays", "test": "tests that were not paired with a source file",
                              "infra": "infra/CI independent of code", "docs": "docs last, describing the final state"}.get(pr["kind"], "")
        commands.append({"step": i, "cluster": pr["cluster"], "commands": cmds})
        del pr["_rank"]

    notes = ["NOTHING WAS STAGED OR COMMITTED. These commands are proposals for the user; run one cluster at a time and verify `git diff --cached --stat` after each `git add`.",
             "Cluster labels, commit types and message summaries are suggestions: review the diffs and decide feat vs fix, merge or split clusters.",
             "Commit messages contain <placeholders>; fill them in before running."]
    if secrets:
        warnings.append(f"secret-looking file(s) detected and excluded from every proposal: {', '.join(secrets)}; do not commit them (add to .gitignore if appropriate)")
    if generated:
        warnings.append(f"{len(generated)} generated/vendored file(s) changed and excluded from proposals; confirm they should be ignored")
    out = {
        "repo": str(root), "mode": "staged" if staged_only else "working-tree", "state": st,
        "summary": {"files": len(entries), "clusters": len([p for p in props]), "added": sum(e["added"] for e in entries),
                    "deleted": sum(e["deleted"] for e in entries), "staged": len(sc_staged),
                    "untracked": sum(1 for e in entries if e["status"] == "?")},
        "files": files, "clusters": [c.to_dict() for c in clusters], "proposals": props, "commands": commands,
        "mixed_concerns": mixed, "hunks": hunk_info,
        "flags": {"secrets": secrets, "generated": generated, "conflicted": conflicted, "binary": sorted(p for p, m in meta.items() if m["binary"])},
        "notes": notes, "warnings": warnings,
    }
    return _bound(out)


def _cap_list(items: list, limit: int) -> tuple:
    return (items[:limit], len(items) - limit) if len(items) > limit else (items, 0)


def _bound(out: dict) -> dict:
    """Cap every unbounded list.  Output of repositories under the caps is returned unchanged (byte-for-byte);
    above them ``truncated: true``, per-list ``*_total`` / ``*_omitted`` counts and a warning say what was cut."""
    omitted: dict = {}

    def note(key: str, n: int) -> None:
        if n:
            omitted[key] = omitted.get(key, 0) + n

    if len(out["files"]) > MAX_LISTED_FILES:
        # tracked changes first (they matter most), then untracked, each in path order
        ordered = sorted(out["files"], key=lambda f: (f["status"] == "?", f["path"]))
        out["files_total"] = len(out["files"])
        out["files"], n = _cap_list(ordered, MAX_LISTED_FILES)
        note("files", n)
    for c in out["clusters"]:
        if len(c["paths"]) > MAX_PATHS_PER_LIST:
            c["paths_total"] = len(c["paths"])
            c["paths"], n = _cap_list(c["paths"], MAX_PATHS_PER_LIST)
            c["paths_omitted"] = n
            note("cluster_paths", n)
    for pr in out["proposals"]:
        if len(pr["files"]) > MAX_PATHS_PER_LIST:
            pr["files_total"] = len(pr["files"])
            pr["files"], n = _cap_list(pr["files"], MAX_PATHS_PER_LIST)
            pr["files_omitted"] = n
            note("proposal_files", n)
    for cmds in [pr["commands"] for pr in out["proposals"]] + [c["commands"] for c in out["commands"]]:
        for i, cmd in enumerate(cmds):
            if len(cmd) > 16_000 or cmd.count(" ") > MAX_PATHS_PER_LIST * 2:
                verb = cmd.split(" -- ")[0]
                cmds[i] = (f"# {verb}: too many paths to list here; run it per directory/pathspec (see `files_total`/`paths_total` of the cluster)")
                note("commands_truncated", 1)
    if len(out["mixed_concerns"]) > MAX_MIXED:
        out["mixed_concerns"], n = _cap_list(out["mixed_concerns"], MAX_MIXED)
        note("mixed_concerns", n)
    for k, v in list(out["flags"].items()):
        if len(v) > MAX_PATHS_PER_LIST:
            out["flags"][k + "_total"] = len(v)
            out["flags"][k], n = _cap_list(v, MAX_PATHS_PER_LIST)
            note("flags_" + k, n)
    if omitted:
        out["truncated"] = True
        out["omitted"] = omitted
        out["warnings"].append("output truncated for size: " + ", ".join(f"{k}={v}" for k, v in sorted(omitted.items()))
                               + " entries omitted; summary counts are complete, listed paths are not")
        out["notes"].append("Because the change set is large, file lists and some staging commands are abbreviated; "
                            "work cluster by cluster (use `--staged` after staging) rather than copying a truncated command.")
    return out
