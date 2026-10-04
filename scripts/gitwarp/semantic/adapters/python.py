"""Python adapter: import / from-import / relative imports / packages (``__init__.py``) via the stdlib ``ast``."""
from __future__ import annotations

import ast
import posixpath
import re
from typing import Optional

_IMPORT_RE = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w*,\s()]+)|import\s+([\w.,\s]+))", re.M)
_SRC_ROOTS = ("", "src", "lib", "app", "scripts")


class PythonAdapter:
    name = "python"
    extensions = {".py", ".pyi"}

    def __init__(self, root=None):
        self.notes: list = []

    def imports(self, path: str, text: str) -> list:
        specs: list = []

        def add(s):
            if s and s not in specs:
                specs.append(s)

        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            for m in _IMPORT_RE.finditer(text):
                if m.group(1) is not None:
                    add(m.group(1))
                    for n in re.split(r"[,\s()]+", m.group(2) or ""):
                        if n and n != "*":
                            add(m.group(1).rstrip(".") + "." + n if not m.group(1).endswith(".") else m.group(1) + n)
                else:
                    for n in (m.group(3) or "").split(","):
                        add(n.strip().split(" as ")[0].strip())
            return specs
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    add(a.name)
            elif isinstance(node, ast.ImportFrom):
                dots = "." * (node.level or 0)
                mod = node.module or ""
                add(dots + mod)
                for a in node.names:
                    if a.name != "*":
                        add(dots + mod + ("." if mod else "") + a.name)
        return specs

    def exports(self, path: str, text: str) -> list:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return []
        names, all_names = [], None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.append(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                        all_names = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return (all_names if all_names is not None else names)[:40]

    # ------------------------------------------------------------------
    @staticmethod
    def _module_file(modpath: str, files) -> Optional[str]:
        if modpath + ".py" in files:
            return modpath + ".py"
        if modpath + ".pyi" in files:
            return modpath + ".pyi"
        init = modpath + "/__init__.py"
        if init in files:
            return init
        return None

    def resolve(self, spec: str, from_path: str, files) -> Optional[str]:
        if spec.startswith("."):
            level = len(spec) - len(spec.lstrip("."))
            rest = spec[level:].replace(".", "/")
            base = posixpath.dirname(from_path)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            cand = posixpath.join(base, rest) if rest else base
            cand = posixpath.normpath(cand)
            if not rest:
                init = posixpath.join(cand, "__init__.py") if cand != "." else "__init__.py"
                return init if init in files else None
            return self._module_file(cand, files)
        rel = spec.replace(".", "/")
        # roots: repo root, common source roots, and every ancestor directory of the importing file
        roots = list(_SRC_ROOTS)
        d = posixpath.dirname(from_path)
        while d:
            roots.append(d)
            d = posixpath.dirname(d)
        seen = set()
        for r in roots:
            if r in seen:
                continue
            seen.add(r)
            r_ = self._module_file(posixpath.join(r, rel) if r else rel, files)
            if r_:
                return r_
        return None
