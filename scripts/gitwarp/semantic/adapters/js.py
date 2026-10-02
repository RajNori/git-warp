"""JavaScript / TypeScript adapter: import/require/export-from/dynamic import, tsconfig paths, index files."""
from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Optional

_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json", ".vue", ".svelte")
_FROM_RE = re.compile(r"""(?:^|[;\s}])(?:import|export)\s[^'"`;]*?\bfrom\s*['"]([^'"\n]+)['"]""")
_SIDE_RE = re.compile(r"""(?:^|[;\s}])import\s*['"]([^'"\n]+)['"]""")
_REQ_RE = re.compile(r"""\brequire\(\s*['"]([^'"\n]+)['"]\s*\)""")
_DYN_RE = re.compile(r"""\bimport\(\s*['"]([^'"\n]+)['"]\s*\)""")
_EXPORT_DECL = re.compile(r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?(?:abstract\s+)?(?:function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.M)
_EXPORT_LIST = re.compile(r"^\s*export\s*\{([^}]*)\}", re.M)
_MODULE_EXPORTS = re.compile(r"\b(?:module\.exports|exports)\.([A-Za-z_$][\w$]*)\s*=", re.M)


def _strip_json_comments(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 1
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


class JsAdapter:
    name = "javascript"
    extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte"}

    def __init__(self, root=None):
        self.base_url = ""
        self.paths: list = []  # (prefix, suffix, [targets])
        self.notes: list = []
        if root is not None:
            self._load_tsconfig(Path(root))

    def _load_tsconfig(self, root: Path) -> None:
        for name in ("tsconfig.json", "jsconfig.json"):
            f = root / name
            try:
                if not f.is_file() or f.stat().st_size > 200_000:
                    continue
                data = json.loads(_strip_json_comments(f.read_text(encoding="utf-8", errors="replace")))
            except (OSError, ValueError):
                self.notes.append(f"could not parse {name}; path aliases ignored")
                continue
            co = data.get("compilerOptions") or {}
            self.base_url = posixpath.normpath(co.get("baseUrl", ".")) if "baseUrl" in co else ""
            for pat, targets in (co.get("paths") or {}).items():
                if not isinstance(targets, list):
                    continue
                pre, star, suf = pat.partition("*")
                self.paths.append((pre, suf if star else None, [t for t in targets if isinstance(t, str)]))
            return

    # ------------------------------------------------------------------ parsing
    def imports(self, path: str, text: str) -> list:
        specs = []
        for rx in (_FROM_RE, _SIDE_RE, _REQ_RE, _DYN_RE):
            for m in rx.finditer(text):
                s = m.group(1).strip()
                if s and s not in specs:
                    specs.append(s)
        return specs

    def exports(self, path: str, text: str) -> list:
        names = []
        for m in _EXPORT_DECL.finditer(text):
            names.append(m.group(1))
        for m in _EXPORT_LIST.finditer(text):
            for part in m.group(1).split(","):
                part = part.strip().split(" as ")[-1].strip()
                if part and re.match(r"^[\w$]+$", part):
                    names.append(part)
        names += _MODULE_EXPORTS.findall(text)
        if re.search(r"^\s*export\s+default\b", text, re.M) or re.search(r"\bmodule\.exports\s*=", text):
            names.append("default")
        seen, out = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out[:40]

    # ------------------------------------------------------------------ resolution
    def _try(self, cand: str, files) -> Optional[str]:
        cand = posixpath.normpath(cand)
        if cand.startswith("../") or cand == "..":
            return None
        if cand in files:
            return cand
        base, ext = posixpath.splitext(cand)
        # TS ESM style: ./x.js really means ./x.ts
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            for e in (".ts", ".tsx", ".mts", ".cts"):
                if base + e in files:
                    return base + e
        for e in _EXTS:
            if cand + e in files:
                return cand + e
        for e in _EXTS:
            idx = f"{cand}/index{e}"
            if idx in files:
                return idx
        return None

    def resolve(self, spec: str, from_path: str, files) -> Optional[str]:
        if spec.startswith("."):
            return self._try(posixpath.join(posixpath.dirname(from_path), spec), files)
        for pre, suf, targets in self.paths:
            if suf is None:
                if spec != pre:
                    continue
                star = ""
            else:
                if not (spec.startswith(pre) and spec.endswith(suf) and len(spec) >= len(pre) + len(suf)):
                    continue
                star = spec[len(pre):len(spec) - len(suf) if suf else None]
            for t in targets:
                cand = posixpath.join(self.base_url or "", t.replace("*", star))
                r = self._try(cand, files)
                if r:
                    return r
        if self.base_url and not spec.startswith("@"):
            r = self._try(posixpath.join(self.base_url, spec), files)
            if r:
                return r
        if spec[:2] in ("@/", "~/"):
            for root in ("src", "app", ""):
                r = self._try(posixpath.join(root, spec[2:]), files)
                if r:
                    return r
        return None
