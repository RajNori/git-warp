"""Evidence-bearing, read-only repository analysis results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    detail: str
    sha: str | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    title: str
    reason: str
    evidence: tuple[Evidence, ...] = ()
    uncertainty: tuple[str, ...] = ()
    severity: str = "info"


@dataclass(frozen=True, slots=True)
class Analysis:
    kind: str
    summary: str
    findings: tuple[Finding, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    uncertainty: tuple[str, ...] = ()
    proposals: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
