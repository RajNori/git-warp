"""Configuration: optional ``.claude/git-warp.local.md`` (YAML-ish frontmatter).

Follows the Claude Code plugin-settings convention.  The plugin works with no
file at all.  The parser is intentionally tiny (scalars, inline lists and
``- item`` block lists) so there is no PyYAML dependency; unknown keys and
malformed values fall back to defaults and are reported in ``Config.warnings``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

DEFAULT_PROTECTED = ("main", "master", "develop", "development", "production", "prod", "release")
SAFETY_MODES = ("standard", "strict")
CONFIG_RELATIVE = Path(".claude") / "git-warp.local.md"


@dataclass
class Config:
    protected_branches: list = field(default_factory=lambda: list(DEFAULT_PROTECTED))
    safety_mode: str = "standard"            # standard | strict (strict: risky ops are denied, not asked)
    sensitive_paths: list = field(default_factory=list)   # extra globs
    ignored_paths: list = field(default_factory=list)     # generated/vendored globs excluded from analysis
    test_paths: list = field(default_factory=list)        # extra globs treated as tests
    infra_paths: list = field(default_factory=list)       # extra globs treated as infrastructure
    memory_enabled: bool = True
    recorder_enabled: bool = True
    recorder_retention_days: int = 30
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(v)
    except ValueError:
        return v


def _inline_list(v: str) -> list:
    inner = v.strip()[1:-1]
    return [_scalar(x) for x in inner.split(",") if x.strip()]


def parse_frontmatter(text: str) -> dict:
    """Parse the leading ``---`` block into a dict. Raises ValueError if absent/unterminated."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("no frontmatter")
    out, key = {}, None
    for ln in lines[1:]:
        if ln.strip() == "---":
            return out
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln.lstrip().startswith("- ") and key is not None and isinstance(out.get(key), list):
            out[key].append(_scalar(ln.lstrip()[2:]))
            continue
        k, sep, v = ln.partition(":")
        if not sep:
            continue
        key = k.strip()
        v = v.strip()
        if v == "":
            out[key] = []
        elif v.startswith("[") and v.endswith("]"):
            out[key] = _inline_list(v)
        else:
            out[key] = _scalar(v)
    raise ValueError("unterminated frontmatter")


_LIST_KEYS = ("protected_branches", "sensitive_paths", "ignored_paths", "test_paths", "infra_paths")
_BOOL_KEYS = ("memory_enabled", "recorder_enabled")


def load_config(root: Optional[Union[str, Path]]) -> Config:
    """Load config for the repository at ``root`` (None → defaults). Never raises."""
    cfg = Config()
    if root is None:
        return cfg
    path = Path(root) / CONFIG_RELATIVE
    try:
        if not path.is_file():
            return cfg
        data = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as e:
        cfg.warnings.append(f"ignored {CONFIG_RELATIVE}: {e}")
        return cfg
    for k, v in data.items():
        if k in _LIST_KEYS:
            if isinstance(v, list) and all(isinstance(x, str) for x in v) and (v or k != "protected_branches"):
                setattr(cfg, k, list(v))
            else:
                cfg.warnings.append(f"{k}: expected a list of strings")
        elif k in _BOOL_KEYS:
            if isinstance(v, bool):
                setattr(cfg, k, v)
            else:
                cfg.warnings.append(f"{k}: expected true/false")
        elif k == "safety_mode":
            if v in SAFETY_MODES:
                cfg.safety_mode = v
            else:
                cfg.warnings.append(f"safety_mode: expected one of {SAFETY_MODES}")
        elif k == "recorder_retention_days":
            if isinstance(v, int) and 0 <= v <= 3650:
                cfg.recorder_retention_days = v
            else:
                cfg.warnings.append("recorder_retention_days: expected integer 0-3650")
        else:
            cfg.warnings.append(f"unknown key: {k}")
    return cfg
