"""Configuration and the ONE trusted-policy model.

Policy files
------------
Two optional files with the same ``.claude/git-warp.local.md`` YAML-ish frontmatter format (Claude Code plugin-settings
convention).  The plugin works with no file at all.  The parser is intentionally tiny (scalars, inline lists and
``- item`` block lists) so there is no PyYAML dependency.

* user policy        ``$HOME/.claude/git-warp.local.md``      (HOME from the environment of the hook process)
* repository policy  ``<repo root>/.claude/git-warp.local.md``  (travels with a clone, so it is the LEAST trusted)

Precedence
----------
    built-in safety floor  <  user policy  <  repository policy

with ONE rule that makes the order safe: **a lower-trust source can only TIGHTEN, never relax.**  The effective policy is
therefore a monotone merge, not an override:

* ``protected_branches``  = built-in defaults  UNION  user additions  UNION  repository additions.  A repository (or user)
  can add protected refs but can never shrink or replace the set; ``protected_branches: []`` or
  ``[nothing-special]`` still leaves ``main`` protected.  Relaxation (removing a built-in protected branch) is NOT
  supported in v0.1.0 -- there is deliberately no higher-trust escape hatch.
* ``safety_mode``  ``standard`` < ``strict``: the effective mode is the strictest value found in any source.  A repository
  that says ``standard`` cannot lower a user's ``strict``.
* ``memory_enabled`` / ``recorder_enabled``: ``false`` from ANY source disables (privacy tightening is always allowed);
  ``true`` never re-enables what another source disabled.
* ``recorder_retention_days``: the user value (or the default 30) may be lowered by the repository, never raised by it.
* ``sensitive_paths`` / ``ignored_paths`` / ``test_paths`` / ``infra_paths``: unions.  A repository may provide project
  context (path classification); these keys never loosen a Guardian rule.
* Unconditional product rules are NOT configurable.  ``guard_enabled: false``, ``safety_mode: off`` or any other unknown
  value, and an empty ``protected_branches`` never switch the guard off or weaken ``git reset --hard``, forced push to a
  protected ref, ``git clean -f`` and the other DENY rules; each is reported in ``Config.warnings`` and ignored.

Robustness
----------
Every policy file is read with ``O_NOFOLLOW | O_NONBLOCK`` and must be a regular file of at most ``MAX_POLICY_BYTES``:
a symlink, FIFO/socket/directory, oversized, binary (NUL bytes) or unterminated file is ignored with a warning (the
other source and the floor still apply).  Lists are capped in length and item size.  Loading never raises and never
blocks.  Warnings are prefixed with the source (``user policy`` / ``repository policy``).
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

DEFAULT_PROTECTED = ("main", "master", "develop", "development", "production", "prod", "release")
SAFETY_MODES = ("standard", "strict")
CONFIG_RELATIVE = Path(".claude") / "git-warp.local.md"
MAX_POLICY_BYTES = 64 * 1024
MAX_LIST_ITEMS = 200
MAX_ITEM_CHARS = 256
MAX_LINES = 2000
DEFAULT_RETENTION_DAYS = 30


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
    recorder_retention_days: int = DEFAULT_RETENTION_DAYS
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
    for n, ln in enumerate(lines[1:], 1):
        if n > MAX_LINES:
            raise ValueError("frontmatter too long")
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


def _read_policy_file(path: Path) -> str:
    """Read one policy file safely.  Raises OSError/ValueError with a short reason (the caller turns it into a warning)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise ValueError("not a plain readable file (symlink or unreadable)" if getattr(e, "errno", 0) in (40, 62, 13) else str(e))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_size > MAX_POLICY_BYTES:
            raise ValueError(f"larger than {MAX_POLICY_BYTES} bytes")
        raw = os.read(fd, MAX_POLICY_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_POLICY_BYTES:
        raise ValueError(f"larger than {MAX_POLICY_BYTES} bytes")
    if b"\x00" in raw:
        raise ValueError("binary content")
    return raw.decode("utf-8", errors="replace")


def _clean_list(v) -> Optional[list]:
    if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
        return None
    return [x.strip()[:MAX_ITEM_CHARS] for x in v[:MAX_LIST_ITEMS] if x.strip()]


def _parse_source(label: str, path: Path, warnings: list) -> dict:
    """Validated settings of one source (only keys that were present and valid).  Never raises."""
    try:
        data = parse_frontmatter(_read_policy_file(path))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError) as e:
        if path.is_symlink() or path.exists():
            warnings.append(f"ignored {label} {path.name}: {e}")
        return {}
    out: dict = {}
    for k, v in data.items():
        if k in _LIST_KEYS:
            items = _clean_list(v)
            if items is None:
                warnings.append(f"{label}: {k}: expected a list of strings")
            elif k == "protected_branches" and not items:
                warnings.append(f"{label}: protected_branches: an empty list cannot remove the built-in protected branches (ignored)")
            else:
                out[k] = items
        elif k in _BOOL_KEYS:
            if isinstance(v, bool):
                out[k] = v
            else:
                warnings.append(f"{label}: {k}: expected true/false")
        elif k == "safety_mode":
            if v in SAFETY_MODES:
                out[k] = v
            else:
                warnings.append(f"{label}: safety_mode: expected one of {SAFETY_MODES}; the guard cannot be switched off (ignored)")
        elif k == "recorder_retention_days":
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 3650:
                out[k] = v
            else:
                warnings.append(f"{label}: recorder_retention_days: expected integer 0-3650")
        elif k == "guard_enabled":
            warnings.append(f"{label}: guard_enabled cannot disable the guard; unconditional safety rules are not configurable (ignored)")
        else:
            warnings.append(f"{label}: unknown key: {k}")
    return out


def _user_policy_path() -> Optional[Path]:
    home = os.environ.get("HOME")
    if not home:
        try:
            home = os.path.expanduser("~")
        except Exception:  # noqa: BLE001
            return None
    if not home or home == "~" or not os.path.isabs(home):
        return None
    return Path(home) / CONFIG_RELATIVE


def _union(base: list, *more: list) -> list:
    seen, out = set(), []
    for lst in (base, *more):
        for x in lst:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def load_config(root: Optional[Union[str, Path]]) -> Config:
    """Effective policy for the repository at ``root`` (None -> floor + user policy).  Never raises.

    See the module docstring for the precedence and the tighten-only rule."""
    cfg = Config()
    try:
        sources = []
        user_path = _user_policy_path()
        if user_path is not None:
            sources.append(("user policy", user_path))
        if root is not None:
            repo_path = Path(root) / CONFIG_RELATIVE
            if user_path is None or repo_path != user_path:      # a repo rooted at $HOME shares one file
                sources.append(("repository policy", repo_path))
        parsed = [(label, _parse_source(label, path, cfg.warnings)) for label, path in sources]
    except Exception as e:  # noqa: BLE001 - config must never take the guard down
        cfg.warnings.append(f"policy loading failed ({type(e).__name__}); built-in safety floor in effect")
        return cfg

    for _label, d in parsed:
        for k in _LIST_KEYS:
            if k in d:
                setattr(cfg, k, _union(getattr(cfg, k), d[k]))
        if d.get("safety_mode") == "strict":
            cfg.safety_mode = "strict"
        for k in _BOOL_KEYS:
            if d.get(k) is False:
                setattr(cfg, k, False)
    # retention: the user value (or default) may only be lowered by the repository
    user_d = next((d for label, d in parsed if label == "user policy"), {})
    repo_d = next((d for label, d in parsed if label == "repository policy"), {})
    days = user_d.get("recorder_retention_days", DEFAULT_RETENTION_DAYS)
    if "recorder_retention_days" in repo_d:
        days = min(days, repo_d["recorder_retention_days"])
    cfg.recorder_retention_days = days
    return cfg
