"""``warp.py temporal``: collect HISTORICAL EVIDENCE relevant to a change set.  Read-only and bounded.

Everything here is a lead for Claude, not a verdict: string similarity is not semantic equivalence.
Every finding cites commits and carries a ``match_quality`` and an explicit ``caveat``.  With no evidence the
result has ``findings: []`` and no warning is fabricated.
"""
from __future__ import annotations

import re
import time
from pathlib import Path, PurePosixPath
from typing import Optional

from ..core import git
from ..core.config import load_config
from ..core.redact import redact
from .common import read_text, repo_state, state_warnings

SIGNAL_PATTERNS = (
    ("race", r"\brace\b|race[- ]condition"), ("deadlock", r"deadlock"), ("leak", r"\bleak"), ("vuln", r"vuln"),
    ("security", r"security|exploit"), ("XSS", r"\bxss\b|cross[- ]site"), ("injection", r"inject"), ("validate", r"validat"),
    ("sanitize", r"saniti[sz]"), ("revert", r"\brevert"), ("regression", r"regress"), ("hotfix", r"hot[- ]?fix"),
    ("CVE", r"\bcve-\d{4}-\d+"), ("csrf", r"\bcsrf\b"), ("overflow", r"overflow"), ("crash", r"\bcrash"), ("fix", r"\bfix(?:e[sd])?\b|\bbug\b"),
)
_SIGNAL_RES = [(n, re.compile(p, re.I)) for n, p in SIGNAL_PATTERNS]
STRONG = {"race", "deadlock", "leak", "vuln", "security", "XSS", "injection", "sanitize", "regression", "hotfix", "CVE", "csrf", "overflow", "revert"}
FIX_LIKE = {"fix", "hotfix", "race", "deadlock", "leak", "vuln", "security", "XSS", "injection", "sanitize", "regression", "CVE", "csrf", "overflow", "crash", "validate"}

CAVEAT = ("String/identifier similarity is not semantic equivalence. Read the cited commit's diff and the current change "
          "before deciding whether this is a real regression; do not warn the user without citing these commits.")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_TRIVIAL = re.compile(r"^(?:[{}()\[\];,]|return;?|else:?|try:?|pass|break;?|continue;?|end|\)\s*\{|\}\s*else\s*\{?)+$")
_COMMENT = re.compile(r"^(?:#|//|/\*|\*|--|\"\"\"|''')")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{7,}")
_COMMON_IDENT = {"function", "unsigned", "continue", "response", "request", "boolean", "return", "default", "typeof", "undefined", "console", "document", "Promise", "constructor", "interface", "implements", "extends", "private", "protected", "export", "import", "string", "number", "optional", "Optional", "isinstance", "required", "__init__", "__name__", "__main__", "self"}
_MANIFESTS = {"package.json", "pyproject.toml", "go.mod", "Cargo.toml", "Gemfile"}
_NOT_DEPS = {"name", "version", "description", "license", "main", "types", "module", "type", "author", "homepage", "repository", "python", "edition", "scripts", "private", "engines"}


def signals_of(text: str) -> list:
    return [n for n, rx in _SIGNAL_RES if rx.search(text or "")]


# ------------------------------------------------------------------------------------------ diff parsing

def parse_diff(text: str) -> dict:
    """``{path: {"added": [(lineno, text)], "removed": [(oldlineno, text)], "old_ranges": [(start, count)], "new_file": bool, "deleted": bool}}``"""
    files: dict = {}
    cur = None
    old_ln = new_ln = 0
    for ln in text.split("\n"):
        if ln.startswith("diff --git "):
            cur = None
        elif ln.startswith("--- "):
            pending_old = ln[4:].strip()
            cur = {"old": pending_old}
        elif ln.startswith("+++ ") and cur is not None:
            new = ln[4:].split("\t")[0].strip()
            old = cur["old"].split("\t")[0]
            path = new[2:] if new.startswith("b/") else None
            if path is None and old.startswith("a/"):
                path = old[2:]
            if path is None:
                cur = None
                continue
            path = path.strip('"')
            cur = files.setdefault(path, {"added": [], "removed": [], "old_ranges": [], "new_file": old == "/dev/null", "deleted": new == "/dev/null"})
        elif cur is not None and ln.startswith("@@"):
            m = _HUNK_RE.match(ln)
            if m:
                old_ln, new_ln = int(m.group(1)), int(m.group(3))
                oc = int(m.group(2) if m.group(2) is not None else 1)
                if oc:
                    cur["old_ranges"].append((old_ln, oc))
        elif cur is not None and "added" in cur:
            if ln.startswith("+") and not ln.startswith("+++"):
                cur["added"].append((new_ln, ln[1:].rstrip("\r")))
                new_ln += 1
            elif ln.startswith("-") and not ln.startswith("---"):
                cur["removed"].append((old_ln, ln[1:].rstrip("\r")))
                old_ln += 1
    return files


def _norm(s: str) -> str:
    return " ".join(s.split())


def distinctive_lines(added: list, limit: int = 40) -> list:
    """Pick added lines worth searching history for (deterministic)."""
    seen, out = set(), []
    for ln, text in added:
        s = text.strip()
        n = _norm(s)
        if len(n) < 20 or _TRIVIAL.match(n) or _COMMENT.match(n) or n in seen:
            continue
        if re.match(r"^(?:import|from|using|#include|package|use)\s", n):
            continue
        alnum = sum(c.isalnum() for c in n)
        if alnum / len(n) < 0.5 or not _IDENT.search(n) and len(re.findall(r"\w{4,}", n)) < 2:
            continue
        seen.add(n)
        out.append((ln, s))
    out.sort(key=lambda x: (-min(len(_norm(x[1])), 90), x[0]))
    return out[:limit]


def new_identifiers(added: list, removed: list, existing_text: Optional[str], limit: int = 20) -> list:
    removed_txt = "\n".join(t for _, t in removed)
    cand: dict = {}
    for ln, text in added:
        if _COMMENT.match(text.strip()):
            continue
        for tok in _IDENT.findall(text):
            if tok in _COMMON_IDENT or tok in cand:
                continue
            if tok in removed_txt or (existing_text is not None and tok in existing_text):
                continue
            cand[tok] = ln
    def score(tok):
        mixed = ("_" in tok and not tok.isupper()) or (any(c.isupper() for c in tok) and any(c.islower() for c in tok))
        return (0 if mixed else 1, -len(tok), tok)
    return [(cand[t], t) for t in sorted(cand, key=score)][:limit]


# ------------------------------------------------------------------------------------------ git helpers

class _Ctx:
    def __init__(self, root: Path, deadline: float, budget: int):
        self.root, self.deadline, self.budget, self.used = root, deadline, budget, 0
        self.timed_out = False
        self.exclude: set = set()
        self.warnings: list = []

    def time_left(self) -> float:
        return self.deadline - time.monotonic()

    def can_probe(self) -> bool:
        if self.used >= self.budget:
            return False
        if self.time_left() <= 0.5:
            self.timed_out = True
            return False
        return True

    def probe(self, frag: str, path: Optional[str], limit: int = 6) -> list:
        self.used += 1
        try:
            return git.log_commits("HEAD", paths=[path] if path else (), limit=limit, extra=[f"-S{frag}", "--no-merges"],
                                   cwd=self.root, timeout=max(1.0, min(20.0, self.time_left())))
        except git.GitError as e:
            self.warnings.append(f"probe skipped: {str(e)[:120]}")
            if isinstance(e, git.GitTimeout):
                self.timed_out = True
            return []


def removed_lines_in_commit(ctx: _Ctx, sha: str, path: Optional[str]) -> list:
    args = ["-c", "core.quotepath=off", "show", "--no-color", "--format=", "-U0", "--no-ext-diff", sha]
    if path:
        args += ["--", path]
    try:
        r = git.run(args, cwd=ctx.root, timeout=max(1.0, min(20.0, ctx.time_left())))
    except git.GitError:
        return []
    out = []
    for f, d in parse_diff(r.stdout[:2_000_000]).items():
        for _, t in d["removed"]:
            out.append((f, t))
    return out


def _evidence(c, side: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    e = {"sha": c.sha, "short": c.short, "subject": c.subject, "date": c.author_date}
    if side:
        e["side"] = side
    if extra:
        e.update(extra)
    return e


def _excerpt(s: str, n: int = 160) -> str:
    return redact(s.strip())[:n]


# ------------------------------------------------------------------------------------------ evidence kinds

def reintroduced(ctx: _Ctx, files: dict, existing: dict, findings: list) -> None:
    """(a) added lines/identifiers that earlier history REMOVED."""
    lines = []
    for path, d in sorted(files.items()):
        if d["deleted"]:
            continue
        for ln, s in distinctive_lines(d["added"], 12):
            lines.append((path, ln, s, d["new_file"] or existing.get(path) is None))
    lines.sort(key=lambda x: (-min(len(_norm(x[2])), 90), x[0], x[1]))
    seen_commit_file: set = set()
    exact_budget = max(1, int(ctx.budget * 0.6))
    n_exact = 0
    for path, ln, s, is_new in lines:
        if n_exact >= exact_budget or not ctx.can_probe():
            break
        n_exact += 1
        for c in ctx.probe(s, None if is_new else path):
            if c.sha in ctx.exclude:
                continue
            for f, t in removed_lines_in_commit(ctx, c.sha, None if is_new else path):
                q = None
                if _norm(t) == _norm(s):
                    q = "exact-line"
                elif _norm(s) in _norm(t):
                    q = "heuristic"
                if q and (c.sha, f) not in seen_commit_file:
                    seen_commit_file.add((c.sha, f))
                    findings.append({"kind": "reintroduced-removed-code", "file": path, "line": ln, "match_quality": q,
                                     "current_change": {"side": "added", "line": ln, "text": _excerpt(s)},
                                     "evidence": [_evidence(c, "removed", {"file": f, "removed_text": _excerpt(t)})],
                                     "signals": signals_of(c.subject + "\n" + c.body), "caveat": CAVEAT})
                    break
    # identifiers (global history, so it also finds moved code)
    for path, d in sorted(files.items()):
        if d["deleted"]:
            continue
        for ln, tok in new_identifiers(d["added"], d["removed"], existing.get(path))[:4]:
            if not ctx.can_probe():
                return
            for c in ctx.probe(tok, None):
                if c.sha in ctx.exclude:
                    continue
                hits = [(f, t) for f, t in removed_lines_in_commit(ctx, c.sha, None) if re.search(r"(?<!\w)" + re.escape(tok) + r"(?!\w)", t)]
                if hits and (c.sha, hits[0][0]) not in seen_commit_file:
                    seen_commit_file.add((c.sha, hits[0][0]))
                    findings.append({"kind": "reintroduced-removed-code", "file": path, "line": ln, "match_quality": "identifier",
                                     "current_change": {"side": "added", "identifier": tok, "line": ln},
                                     "evidence": [_evidence(c, "removed", {"file": hits[0][0], "removed_text": _excerpt(hits[0][1])})],
                                     "signals": signals_of(c.subject + "\n" + c.body), "caveat": CAVEAT})
                    break


def removed_fixes(ctx: _Ctx, files: dict, base_rev: Optional[str], findings: list, cap: int = 15) -> None:
    """(b) removed lines whose blame commit looks like a fix."""
    if not base_rev:
        return
    done = 0
    for path, d in sorted(files.items()):
        if d["new_file"] or not d["old_ranges"]:
            continue
        for start, count in d["old_ranges"][:4]:
            if done >= cap or ctx.time_left() < 1:
                return
            done += 1
            count = min(count, 200)
            try:
                bl = git.blame(path, base_rev, (start, start + count - 1), cwd=ctx.root)
            except git.GitError:
                continue
            by: dict = {}
            for b in bl:
                by.setdefault(b["sha"], []).append(b)
            for sha, items in by.items():
                subj = items[0].get("summary", "")
                sig = signals_of(subj)
                if not set(sig) & FIX_LIKE:
                    continue
                meta = git.commit_metadata(sha, ctx.root)
                if meta and meta.sha in ctx.exclude:
                    continue
                sig = signals_of(subj + "\n" + (meta.body if meta else ""))
                findings.append({"kind": "removed-fix-code", "file": path, "line": start, "match_quality": "exact-line",
                                 "current_change": {"side": "removed", "old_line_range": [start, start + count - 1],
                                                    "text": _excerpt(items[0].get("text", ""))},
                                 "evidence": [_evidence(meta, "added", {"lines_attributed": len(items)}) if meta else {"sha": sha, "subject": subj, "side": "added"}],
                                 "signals": sig, "caveat": CAVEAT + " Blame shows who last touched the removed lines, which may be a refactor rather than the fix itself."})


def reverts(ctx: _Ctx, files: dict, findings: list, cap: int = 15) -> None:
    """(c) revert-like commits that touched the changed files."""
    for path in sorted(files)[:cap]:
        if ctx.time_left() < 1:
            return
        cs = git.log_commits("HEAD", paths=[path], limit=5, cwd=ctx.root,
                             extra=["-i", "--grep=revert", "--grep=back out", "--grep=backout", "--grep=roll back", "--grep=rollback", "--no-merges"],
                             timeout=max(1.0, min(15.0, ctx.time_left())))
        cs = [c for c in cs if c.sha not in ctx.exclude]
        if not cs:
            continue
        ev = []
        for c in cs:
            m = re.search(r"This reverts commit ([0-9a-f]{7,40})", c.body)
            ev.append(_evidence(c, None, {"reverts": m.group(1)} if m else None))
        findings.append({"kind": "touched-file-has-reverts", "file": path, "match_quality": "heuristic", "evidence": ev,
                         "signals": sorted({s for c in cs for s in signals_of(c.subject + "\n" + c.body)}),
                         "caveat": CAVEAT + " Matched on commit-message wording only; the revert may be unrelated to the lines being changed now."})


def dep_names(path: str, lines: list) -> set:
    name = PurePosixPath(path).name
    out = set()
    for _, t in lines:
        s = t.strip()
        m = None
        if name == "package.json":
            m = re.match(r'^"([@\w./-]+)"\s*:\s*"((?:[~^<>=*\d]|workspace:|npm:|git|file:|link:|latest)[^"]*)"', s)
        elif name == "go.mod":
            m = re.match(r"^(?:require\s+)?([\w./~-]+\.[\w./~-]+)\s+v\d", s)
        elif name == "Gemfile":
            m = re.match(r"^gem\s+['\"]([\w.-]+)['\"]", s)
        elif name in ("pyproject.toml", "Cargo.toml"):
            m = re.match(r'^"([A-Za-z0-9_.-]+)\s*(?:[=<>!~\[;]|$)', s) or re.match(r'^([A-Za-z0-9_.-]+)\s*=\s*["{]', s)
        if m and m.group(1).lower() not in _NOT_DEPS:
            out.add(m.group(1))
    return out


def dependency_flips(ctx: _Ctx, files: dict, findings: list) -> None:
    """(d) dependencies added/removed now that history shows were removed before."""
    n = 0
    for path, d in sorted(files.items()):
        base = PurePosixPath(path).name
        if base not in _MANIFESTS and not re.match(r"requirements[-_.\w]*\.txt$", base):
            continue
        if base.endswith(".txt"):
            add = {re.split(r"[=<>!~\[;\s]", t.strip())[0] for _, t in d["added"] if t.strip() and not t.strip().startswith(("#", "-"))}
            rem = {re.split(r"[=<>!~\[;\s]", t.strip())[0] for _, t in d["removed"] if t.strip() and not t.strip().startswith(("#", "-"))}
            add, rem = {a for a in add if a}, {r for r in rem if r}
        else:
            add, rem = dep_names(path, d["added"]), dep_names(path, d["removed"])
        for dep, direction in [(x, "added") for x in sorted(add - rem)] + [(x, "removed") for x in sorted(rem - add)]:
            if n >= 10 or not ctx.can_probe():
                return
            n += 1
            ev = []
            for c in ctx.probe(dep, path, limit=6):
                if c.sha in ctx.exclude:
                    continue
                for _, t in removed_lines_in_commit(ctx, c.sha, path):
                    if re.search(r"(?<![\w-])" + re.escape(dep) + r"(?![\w-])", t):
                        ev.append(_evidence(c, "removed", {"removed_text": _excerpt(t, 100)}))
                        break
            if ev:
                msg = ("this change ADDS the dependency, which history shows was removed before" if direction == "added"
                       else "this change REMOVES the dependency, which history shows was removed and later re-added (flip-flop)")
                findings.append({"kind": "dependency-previously-removed", "file": path, "dependency": dep, "direction": direction,
                                 "match_quality": "identifier", "note": msg, "evidence": ev,
                                 "signals": sorted({s for e in ev for s in signals_of(e["subject"])}), "caveat": CAVEAT})


def memory_hints(ctx: _Ctx, files: dict, findings: list, sources: dict) -> None:
    """Optional: strong-signal recent commits on touched files, via the memory index when it exists."""
    try:
        from gitwarp.memory import index  # type: ignore
    except ImportError:
        sources["memory_index"] = "unavailable"
        return
    except Exception:  # noqa: BLE001
        sources["memory_index"] = "error"
        return
    sources["memory_index"] = "used"
    for path in sorted(files)[:10]:
        try:
            rows = index.file_commits(ctx.root, path, limit=40)
        except Exception as e:  # noqa: BLE001 - index absent/corrupt must degrade
            sources["memory_index"] = f"degraded: {str(e)[:80]}"
            return
        ev, sig = [], set()
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            subj = str(r.get("subject", ""))
            s = set(signals_of(subj)) & STRONG
            sha = str(r.get("sha", ""))
            if s and sha and sha not in ctx.exclude:
                ev.append({"sha": sha, "short": sha[:8], "subject": subj, "date": r.get("date") or r.get("author_date")})
                sig |= s
            if len(ev) >= 3:
                break
        if ev:
            findings.append({"kind": "file-history-signals", "file": path, "match_quality": "heuristic", "evidence": ev,
                             "signals": sorted(sig), "caveat": CAVEAT + " These commits touched the same file; they may be unrelated to the lines changed now."})


# ------------------------------------------------------------------------------------------ entry point

_ORDER = {"reintroduced-removed-code": 0, "removed-fix-code": 1, "dependency-previously-removed": 2, "touched-file-has-reverts": 3, "file-history-signals": 4}
_QUAL = {"exact-line": 0, "identifier": 1, "heuristic": 2}


def analyze(root: Path, base: Optional[str] = None, limit: int = 20, budget: int = 25, timeout: float = 30.0) -> dict:
    t0 = time.monotonic()
    ctx = _Ctx(root, t0 + timeout, max(1, min(int(budget), 60)))
    cfg = load_config(root)
    st = repo_state(root)
    warnings = cfg.warnings + state_warnings(st)
    has_head = st["head"] is not None
    if not has_head:
        return {"repo": str(root), "state": st, "mode": "n/a", "findings": [], "message": "repository has no commits, so there is no history to compare against", "warnings": warnings}

    mode, base_rev = "working-tree", st["head"]
    if base:
        try:
            git.check_ref(base)
        except ValueError as e:
            return {"error": str(e), "warnings": warnings}
        if git.rev_parse(base, root) is None:
            return {"error": f"unknown base ref: {base}", "warnings": warnings}
        mode = f"base:{base}"
        base_rev = git.merge_base(base, "HEAD", root)
        if base_rev is None:
            warnings.append("no merge base between base and HEAD; comparing against the base ref directly")
            base_rev = git.rev_parse(base, root)
        r = git.run(["-c", "core.quotepath=off", "diff", "-U0", "--no-color", "--no-ext-diff", f"{base}...HEAD"], cwd=root, timeout=60)
        ex = git.run(["rev-list", "--max-count=5000", f"{base}..HEAD"], cwd=root, timeout=20)
        ctx.exclude = set(ex.lines)
    else:
        r = git.run(["-c", "core.quotepath=off", "diff", "-U0", "--no-color", "--no-ext-diff", "HEAD"], cwd=root, timeout=60)
    files = parse_diff(r.stdout[:3_000_000]) if r.ok else {}
    if not r.ok:
        warnings.append("could not compute the diff: " + r.stderr.strip()[:150])
    if not base:
        try:
            for s in git.working_tree_status(root, untracked="all"):
                if s.untracked and s.path not in files:
                    t = read_text(root / s.path, 300_000)
                    if t:
                        files[s.path] = {"added": [(i, ln) for i, ln in enumerate(t.split("\n")[:1500], 1)], "removed": [], "old_ranges": [],
                                         "new_file": True, "deleted": False}
        except git.GitError:
            pass
    from ..core.paths import classify_path
    files = {p: d for p, d in files.items() if "generated" not in classify_path(p, cfg) and "secret-file" not in classify_path(p, cfg)}
    if not files:
        return {"repo": str(root), "state": st, "mode": mode, "base": base, "changed_files": [], "findings": [],
                "message": "no analysable changes (empty or generated-only change set)", "warnings": warnings}

    existing = {}
    for p, d in files.items():
        existing[p] = None if d["new_file"] else git.show_file(base_rev or "HEAD", p, root)
    findings: list = []
    sources = {"git_log_pickaxe": "used"}
    reintroduced(ctx, files, existing, findings)
    removed_fixes(ctx, files, base_rev, findings)
    dependency_flips(ctx, files, findings)
    reverts(ctx, files, findings)
    memory_hints(ctx, files, findings, sources)
    findings.sort(key=lambda f: (_ORDER.get(f["kind"], 9), 0 if f.get("signals") else 1, _QUAL.get(f["match_quality"], 3), f["file"], f.get("line", 0)))
    total = len(findings)
    findings = findings[:max(1, int(limit))]
    for i, f in enumerate(findings, 1):
        f["id"] = i
        f["verify_with"] = [f"git show {e['sha']} -- {f['file']}" for e in f["evidence"][:2] if e.get("sha")]
    out = {"repo": str(root), "state": st, "mode": mode, "base": base, "changed_files": sorted(files),
           "findings": findings, "findings_total": total,
           "probes": {"used": ctx.used, "budget": ctx.budget, "timed_out": ctx.timed_out, "seconds": round(time.monotonic() - t0, 2)},
           "sources": sources,
           "instructions": "Each finding is a lead, not a conclusion. Read the cited commits' diffs; only warn the user for findings you verified, and cite the commits.",
           "warnings": warnings + ctx.warnings}
    if not findings:
        out["message"] = f"no historical evidence found within the probe budget ({ctx.used}/{ctx.budget} probes); this is not proof that the change is safe"
    if st["shallow"]:
        out["warnings"].append("shallow clone: older history is missing, absence of evidence is weaker")
    return out
