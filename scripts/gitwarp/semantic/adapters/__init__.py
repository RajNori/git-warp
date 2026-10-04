"""Ecosystem adapters for the blast-radius import graph.

Contract (see ARCHITECTURE.md section 8): ``imports(path, text) -> list[str]`` and
``resolve(spec, from_path, files) -> str | None`` where ``files`` is a set of
repo-relative POSIX paths.  ``exports(path, text)`` is a best-effort list of exported symbols.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional

from .generic import GenericAdapter
from .js import JsAdapter
from .python import PythonAdapter


def build_adapters(root=None) -> list:
    return [JsAdapter(root), PythonAdapter(root)]


def adapter_for(path: str, adapters: list) -> Optional[object]:
    ext = PurePosixPath(path).suffix.lower()
    for a in adapters:
        if ext in a.extensions:
            return a
    return None


__all__ = ["JsAdapter", "PythonAdapter", "GenericAdapter", "build_adapters", "adapter_for"]
