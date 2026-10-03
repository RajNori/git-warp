"""Approximate source-level dependant discovery for JS/TS and Python."""
from __future__ import annotations
from pathlib import Path
import re
from ..git import DEFAULT_TIMEOUT_SECONDS
from .common import changed_paths, root as repo_root, source_files
from .models import Analysis, Evidence, Finding

_JS_IMPORT = re.compile(r"(?:\bfrom\s*|\bimport\s*\(|\brequire\s*\()\s*[\"']([^\"']+)[\"']")
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)

def _module_refs(path: Path, text: str) -> set[str]:
    refs: set[str] = set()
    if path.suffix == ".py":
        refs.update(a or b for a, b in _PY_IMPORT.findall(text))
    else:
        refs.update(_JS_IMPORT.findall(text))
    return refs

def _matches(root: Path, importer: Path, reference: str, candidates: dict[str, Path]) -> set[Path]:
    if reference.startswith("."):
        base = (importer.parent / reference)
        probes = [base, *(Path(str(base) + ext) for ext in (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")), *(base / ("index" + ext) for ext in (".js", ".jsx", ".ts", ".tsx")), base / "__init__.py"]
        return {p.resolve() for p in probes if p.exists() and p.is_file()}
    if importer.suffix == ".py":
        parts = reference.split(".")
        relative = Path(*parts)
        probes = [root / relative.with_suffix(".py"), root / relative / "__init__.py"]
        return {p.resolve() for p in probes if p.exists() and p.is_file()}
    # Non-relative JS imports are third-party/package aliases unless repository-local exact paths exist.
    base = root / reference
    probes = [base, *(Path(str(base) + ext) for ext in (".js", ".jsx", ".ts", ".tsx")), *(base / ("index" + ext) for ext in (".js", ".jsx", ".ts", ".tsx"))]
    return {p.resolve() for p in probes if p.exists() and p.is_file()}

def analyze_blast_radius(cwd: str | Path, *, base: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS, file_limit: int = 5000) -> Analysis:
    root = repo_root(cwd, timeout)
    changed = tuple(p for p in changed_paths(root, base, timeout) if Path(p).suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
    files = source_files(root, limit=file_limit)
    truncated = len(files) >= file_limit
    graph: dict[Path, set[Path]] = {}
    for f in files:
        try: text = f.read_text(encoding="utf-8", errors="replace")
        except OSError: continue
        refs = _module_refs(f, text)
        graph[f.resolve()] = set().union(*(_matches(root, f, ref, {}) for ref in refs)) if refs else set()
    direct: dict[str, tuple[str, ...]] = {}
    for name in changed:
        target = (root / name).resolve()
        direct[name] = tuple(sorted(str(p.relative_to(root)) for p, deps in graph.items() if target in deps))
    findings = tuple(Finding("Potential dependants", f"{len(consumers)} source file(s) statically import {name}.", (Evidence("changed file", name), *(Evidence("import reference", c) for c in consumers)), ("Import parsing is regex based and may miss aliases, generated modules, dynamic imports, namespace/package resolution, or non-source dependants.",), "medium") for name, consumers in direct.items() if consumers)
    unknown = tuple(f"Could not resolve import graph for {name}; unsupported languages and runtime wiring are not analyzed." for name in changed)
    if truncated: unknown += (f"Source scan stopped at the configured limit of {file_limit} files.",)
    return Analysis("blast-radius", f"Scanned {len(graph)} source files for dependants of {len(changed)} changed source file(s).", findings, tuple(Evidence("changed file", p) for p in changed), unknown or ("A lack of static matches does not prove the file has no runtime dependants.",), metadata={"changed": changed, "dependants": direct, "scanned_files": len(graph), "truncated": truncated})
