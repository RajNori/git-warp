"""Repository and pull-request change summaries from explicit Git evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re

from ..git import DEFAULT_TIMEOUT_SECONDS, current_branch
from .common import changed_paths, diff_stat, evidence_paths, head, log_records, porcelain_entries, read, root
from .models import Analysis, Evidence, Finding

_PATH_SIGNALS = {
    "sensitive": (".env", "secret", "credential", "private_key", "id_rsa", "token"),
    "migration": ("migration", "migrations", "alembic", "prisma/migrations", "schema/migrations"),
    "infrastructure": ("terraform", "infrastructure", "infra/", ".github/workflows", "dockerfile", "kubernetes", "k8s/", "helm/"),
    "lockfile": ("lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "cargo.lock", "gemfile.lock"),
    "tests": ("test", "tests/", "spec", "specs/"),
    "api": ("api/", "/api.", "api.", "routes/", "openapi", "swagger", "graphql", "proto", "schema"),
    "dependency": ("package.json", "pyproject.toml", "requirements", "pipfile", "cargo.toml", "go.mod", "gemfile", "pom.xml", "build.gradle"),
    "generated": ("generated", "dist/", "build/", ".min.", "_generated", "codegen"),
}
_SECRET_RE = re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{12,}")
_DEBUG_RE = re.compile(r"(?i)(?:console\.log\s*\(|\bdebugger\s*;|\bprint\s*\(|logging\.debug\s*\(|logger\.debug\s*\()")


def _signals(paths: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    result = {}
    for category, needles in _PATH_SIGNALS.items():
        hits = tuple(path for path in paths if any(n in path.lower() for n in needles))
        if hits:
            result[category] = hits
    return result


def _diff_args(base: str | None) -> tuple[str, ...]:
    return ("diff", "--no-ext-diff", "--unified=0", *((base,) if base else ("HEAD",)), "--")


def _hotspots(repo: Path, timeout: float, limit: int = 200) -> tuple[tuple[str, int], ...]:
    raw = read(repo, ("log", "--format=%H", "--name-only", "-z", f"-n{limit}", "HEAD", "--"), timeout, check=False)
    counts: Counter[str] = Counter()
    for item in raw.split("\0"):
        value = item.strip()
        if value and not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
            counts[value] += 1
    return tuple(counts.most_common(10))


def analyze_xray(cwd: str | Path, *, base: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Analysis:
    repo = root(cwd, timeout)
    paths = changed_paths(repo, base, timeout)
    current = head(repo, timeout)
    branch = current_branch(cwd=repo, timeout=timeout)
    status = porcelain_entries(repo, timeout)
    staged = tuple(path for xy, path in status if xy[0] not in " ?")
    unstaged = tuple(path for xy, path in status if xy[1] not in " ?")
    untracked = tuple(path for xy, path in status if xy == "??")
    upstream_result = read(repo, ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"), timeout, check=False).strip()
    upstream = upstream_result or None
    ahead_behind = None
    if upstream:
        count = read(repo, ("rev-list", "--left-right", "--count", f"{upstream}...HEAD"), timeout, check=False).split()
        if len(count) == 2 and all(x.isdigit() for x in count):
            ahead_behind = {"behind": int(count[0]), "ahead": int(count[1])}
    stashes = tuple(line for line in read(repo, ("stash", "list", "--format=%gd %h %s"), timeout, check=False).splitlines() if line)
    worktrees = tuple(line for line in read(repo, ("worktree", "list", "--porcelain"), timeout, check=False).splitlines() if line.startswith(("worktree ", "branch ", "HEAD ", "detached")))
    reflog = tuple(line for line in read(repo, ("reflog", "-n", "10", "--format=%H %gs", "HEAD"), timeout, check=False).splitlines() if line)
    historical_hotspots = _hotspots(repo, timeout)
    findings: list[Finding] = []
    uncertainty: list[str] = []
    if not paths:
        uncertainty.append("No changed paths were reported; ignored files and commits outside the selected comparison are not represented.")
    signals = _signals(paths)
    if signals.get("sensitive"):
        findings.append(Finding("Sensitive-looking path signal", "A changed path name matches common secret or credential naming patterns; this does not establish that the file contains a secret.", evidence_paths(signals["sensitive"]), ("Name matching can produce false positives and does not detect secrets under ordinary names.",), "high"))
    for category, paths_for_signal in signals.items():
        if category != "sensitive":
            findings.append(Finding(f"{category.title()} change signal", f"Changed path names match the {category} heuristic.", evidence_paths(paths_for_signal), ("This is a path-name classification; inspect the diff to determine actual behavior.",), "low"))
    if not branch:
        uncertainty.append("HEAD is detached; branch and upstream tracking information are unavailable.")
    elif not upstream:
        uncertainty.append("No upstream is configured for the current branch; ahead/behind counts are unavailable.")
    if not reflog:
        uncertainty.append("HEAD reflog is unavailable or empty; local recovery points could not be assessed.")
    if not stashes:
        uncertainty.append("No stash entries were listed; stash availability may be disabled or empty.")

    patch = read(repo, _diff_args(base), timeout, check=False)
    if any(_SECRET_RE.search(line[1:]) for line in patch.splitlines() if line.startswith("+")):
        findings.append(Finding("Possible secret-like added value", "An added diff line matched a credential-shaped pattern; the matching value is intentionally not reproduced.", (), ("Pattern matching is incomplete and can flag placeholders; verify securely in the source diff.",), "high"))
    if any(_DEBUG_RE.search(line[1:]) for line in patch.splitlines() if line.startswith("+")):
        findings.append(Finding("Debug or diagnostic code signal", "An added line matched a common debug logging pattern.", (), ("Pattern matching is language-agnostic and may include intentional production logging.",), "low"))

    # Path families are proposed as review boundaries, never as semantic proof.
    families: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        parts = Path(path).parts
        family = parts[0] if len(parts) > 1 else "root files"
        if family in {"tests", "test", "spec", "specs"}:
            family = "tests/specs"
        families[family].append(path)
    boundaries = tuple({"suggested_boundary": name, "paths": tuple(members), "evidence": "shared top-level path family", "proposal_only": True} for name, members in families.items() if len(members) > 1)
    evidence = list(evidence_paths(paths))
    if current:
        evidence.append(Evidence("HEAD", current, current))
    evidence.extend(Evidence("status", f"{xy} {path}") for xy, path in status)
    if upstream:
        evidence.append(Evidence("upstream", upstream))
    summary = f"{len(paths)} changed path(s)" + (f" relative to {base}" if base else " in the worktree")
    return Analysis("xray", summary, tuple(findings), tuple(evidence), tuple(uncertainty), boundaries, {"repository_root": str(repo), "paths": paths, "status": status, "staged": staged, "unstaged": unstaged, "untracked": untracked, "branch": branch, "detached": branch is None, "upstream": upstream, "ahead_behind": ahead_behind, "stashes": stashes, "worktrees": worktrees, "reflog": reflog, "historical_hotspots": historical_hotspots, "signals": signals, "stat": diff_stat(repo, base, timeout), "head": current, "recent_commits": log_records(repo, limit=15, timeout=timeout)})


def analyze_pr(cwd: str | Path, base: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Analysis:
    if not base or base.startswith("-"):
        raise ValueError("base must be a non-option Git revision")
    repo = root(cwd, timeout)
    comparison = f"{base}...HEAD"
    raw_paths = read(repo, ("diff", "--name-only", "-z", "--no-ext-diff", comparison, "--"), timeout)
    paths = tuple(p for p in raw_paths.split("\0") if p)
    raw = read(repo, ("diff", "--numstat", "--no-ext-diff", comparison, "--"), timeout)
    additions = deletions = 0
    for line in raw.splitlines():
        cols = line.split("\t", 2)
        if len(cols) >= 2:
            additions += int(cols[0]) if cols[0].isdigit() else 0
            deletions += int(cols[1]) if cols[1].isdigit() else 0
    findings: list[Finding] = []
    signals = _signals(paths)
    categories = {"migration": "Migration", "dependency": "Dependency manifest", "lockfile": "Lockfile", "generated": "Generated artifact", "api": "API/schema", "infrastructure": "Infrastructure", "sensitive": "Sensitive-looking"}
    for category, title in categories.items():
        if signals.get(category):
            findings.append(Finding(f"{title} path signal", f"Changed path names match the {category} heuristic.", evidence_paths(signals[category]), ("Path evidence indicates an area to review; inspect the actual diff to establish impact.",), "medium" if category in {"sensitive", "migration", "api"} else "low"))
    tests = signals.get("tests", ())
    code = [p for p in paths if p.lower().endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb")) and p not in tests]
    if code and not tests:
        findings.append(Finding("No test paths in diff", "Changed source files have no matching test/spec paths in the compared change set.", evidence_paths(code), ("Tests may exist outside the diff or use an unrecognized naming convention.",), "medium"))
    patch = read(repo, ("diff", "--no-ext-diff", "--unified=0", comparison, "--"), timeout, check=False)
    added_lines = [(i, line[1:]) for i, line in enumerate(patch.splitlines(), 1) if line.startswith("+") and not line.startswith("+++")]
    if any(_SECRET_RE.search(line) for _, line in added_lines):
        findings.append(Finding("Possible secret-like added value", "An added diff line matched a credential-shaped pattern; values are not copied into the report.", (), ("Regex matching can miss secrets and can flag examples or placeholders; inspect the diff securely.",), "high"))
    if any(_DEBUG_RE.search(line) for _, line in added_lines):
        findings.append(Finding("Debug logging signal", "An added diff line matched a common debug-output pattern.", (), ("This signal does not determine whether logging is inappropriate in production.",), "low"))
    arch = tuple(p for p in paths if p.startswith(("src/", "app/", "lib/", "services/", "packages/")) or p.count("/") >= 2)
    if arch:
        findings.append(Finding("Architecture surface changed", "The diff touches application or package structure paths.", evidence_paths(arch), ("Path layout alone does not reveal dependency direction or runtime architecture.",), "info"))
    questions = []
    if signals.get("migration") or signals.get("api"):
        questions.append("Are schema/API changes backward compatible, and is a staged rollout needed?")
    if signals.get("dependency") or signals.get("lockfile"):
        questions.append("Are dependency versions, licenses, and runtime support intentional?")
    if signals.get("generated"):
        questions.append("Is generated output reproducible from the checked-in source and generator version?")
    if not tests and code:
        questions.append("Which automated checks cover the changed source paths?")
    rollback = "Revert the change commit(s) after checking for irreversible data migrations or external side effects." if signals.get("migration") else "Revert the change commit(s); inspect external side effects before rollback."
    evidence = [Evidence("diff base", base), *evidence_paths(paths), Evidence("HEAD", head(repo, timeout) or "unborn")]
    stat = read(repo, ("diff", "--stat", "--no-ext-diff", comparison, "--"), timeout)
    metadata = {"paths": paths, "stat": stat, "additions": additions, "deletions": deletions, "signals": signals, "architecture_paths": arch, "migration_implications": tuple(signals.get("migration", ())), "rollback_consideration": rollback, "reviewer_questions": tuple(questions), "generated_paths": tuple(signals.get("generated", ())), "secret_scan": "heuristic added-line scan; matched values withheld"}
    return Analysis("pr", f"{len(paths)} path(s), +{additions}/-{deletions} lines against {base}", tuple(findings), tuple(evidence), ("Static path and diff heuristics do not establish test coverage, runtime behavior, or mergeability.",), metadata=metadata)
