"""The ONE revision-normalization service.

Every revision-like value that reaches Git Warp from outside (``--base``, ``--good``, ``--bad``, a sha typed
by a user, a ref name read out of a repository) is converted here into an immutable full object id before it
is used in any later Git command.  Nothing else in the code base may hand a user-controlled revision string
to Git.

Pipeline for :func:`resolve`::

    syntax check      reject non-str, empty, over-long, NUL / newline / any control character or space,
                      a leading ``-`` (option shaped), a leading ``:`` (``:/regex`` search / index syntax),
                      ranges (``..``) and glob/escape characters
    git rev-parse     ``rev-parse --verify --quiet --end-of-options <input>^{<kind>}``  (kind = commit by default;
                      the object type the caller expects is *required*, annotated tags are peeled)
    output check      only a full 40- or 64-digit lower-case hex object id is accepted back
    result            ``Revision(label, sha, kind)``: ``label`` is the original human text (display only),
                      ``sha`` is what every later ``diff``/``log``/``show``/``merge-base``/``rev-list`` receives

The ref is resolved ONCE; later commands use the sha, so a ref that moves between two commands cannot make the
analysis inconsistent.  Ranges (``A..B``, ``A...B``) are accepted only by :func:`resolve_range`, which
normalizes each endpoint separately and rebuilds the range from the two full ids; an untrusted range string is
never passed on.

Failures raise :class:`RevisionError` (a ``ValueError``) with a stable machine-readable ``reason``:

``invalid_type`` ``empty`` ``too_long`` ``control_characters`` ``option_shaped`` ``forbidden_characters``
``unsupported_syntax`` ``not_found`` ``ambiguous`` ``unborn`` ``wrong_object_type`` ``unexpected_output``.

The first seven (:data:`SYNTAX_REASONS`) mean the *input* was rejected before Git saw it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

KINDS = ("commit", "tree", "blob", "tag")
MAX_LABEL = 256
SYNTAX_REASONS = frozenset({"invalid_type", "empty", "too_long", "control_characters", "option_shaped",
                            "forbidden_characters", "unsupported_syntax"})

_FULL_OID = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")        # NUL, newline, tab, ESC ... and the space character
_FORBIDDEN = re.compile(r"[\\*?\[]")             # escape / glob characters are not part of the supported syntax


class RevisionError(ValueError):
    """A revision could not be accepted or resolved; ``reason`` is a stable code."""

    def __init__(self, reason: str, label: object = "", message: str = ""):
        self.reason = reason
        self.label = label if isinstance(label, str) else repr(label)
        super().__init__(message or f"{reason}: {self.label[:80]!r}")


@dataclass(frozen=True)
class Revision:
    """An immutable, verified object id plus the human label it was typed as."""
    label: str
    sha: str
    kind: str = "commit"

    @property
    def short(self) -> str:
        return self.sha[:8]

    def __str__(self) -> str:  # str(rev) is the sha: it is what Git commands receive
        return self.sha


@dataclass(frozen=True)
class RevisionRange:
    label: str
    left: Revision
    right: Revision
    symmetric: bool = False

    @property
    def arg(self) -> str:
        """``<sha>..<sha>`` / ``<sha>...<sha>`` built from the two verified ids (safe to pass to Git)."""
        return f"{self.left.sha}{'...' if self.symmetric else '..'}{self.right.sha}"


# --------------------------------------------------------------------------- syntax

def is_full_oid(value: object) -> bool:
    return isinstance(value, str) and bool(_FULL_OID.match(value))


def check_syntax(label: object) -> str:
    """Return ``label`` when it is acceptable *input*; raise :class:`RevisionError` otherwise (no Git involved)."""
    if not isinstance(label, str):
        raise RevisionError("invalid_type", label, f"revision must be text, not {type(label).__name__}")
    if not label:
        raise RevisionError("empty", label, "revision is empty")
    if len(label) > MAX_LABEL:
        raise RevisionError("too_long", label[:40], f"revision is longer than {MAX_LABEL} characters")
    if _CONTROL.search(label):
        raise RevisionError("control_characters", label, "revision contains control characters or whitespace")
    if label.startswith("-"):
        raise RevisionError("option_shaped", label, "revision must not start with '-' (option-shaped input)")
    if label.startswith(":"):
        raise RevisionError("unsupported_syntax", label, "revision must not start with ':' (index / message-search syntax)")
    if ".." in label:
        raise RevisionError("unsupported_syntax", label, "ranges are only accepted through resolve_range()")
    if _FORBIDDEN.search(label):
        raise RevisionError("forbidden_characters", label, "revision contains glob or escape characters")
    return label


def check_refname(name: object) -> str:
    """Syntax check for a *ref name* that is not resolved to an object (e.g. ``reflog show <ref>``)."""
    return check_syntax(name)


def oid_or_none(value: object) -> Optional[str]:
    """``value`` when it already is a full object id (cannot be option-shaped, needs no resolving), else None."""
    return value if is_full_oid(value) else None


# --------------------------------------------------------------------------- resolution

def _classify_failure(label: str, kind: str, cwd, stderr: str) -> RevisionError:
    from . import git  # late import: git.py imports this module
    if "ambiguous" in stderr.lower():
        return RevisionError("ambiguous", label, f"{label!r} is ambiguous (more than one object matches)")
    base = re.split(r"[~^@]", label, maxsplit=1)[0]
    if (base == "HEAD" or label == "@") and git.head_sha(cwd) is None:
        return RevisionError("unborn", label, "HEAD is unborn: the repository has no commits yet")
    probe = git.run(["rev-parse", "--verify", "--quiet", "--end-of-options", label], cwd=cwd, timeout=10)
    if probe.ok and is_full_oid(probe.text):
        return RevisionError("wrong_object_type", label, f"{label!r} exists but is not a {kind}")
    hint = ""
    try:
        if git.is_shallow(cwd):
            hint = " (shallow repository: older history is not present)"
    except git.GitError:
        pass
    return RevisionError("not_found", label, f"{label!r} does not resolve to a {kind}{hint}")


def resolve(label: Union[str, Revision], cwd=None, kind: str = "commit", timeout: float = 10) -> Revision:
    """Normalize ``label`` to a verified :class:`Revision` of object type ``kind``.

    Accepts an existing :class:`Revision` (returned unchanged when its kind matches) so call chains can pass
    normalized values around.  Raises :class:`RevisionError`; Git execution problems (missing git, timeout,
    not a repository) propagate as the usual ``GitError`` subclasses.
    """
    from . import git
    if kind not in KINDS:
        raise ValueError(f"unknown object kind: {kind!r}")
    if isinstance(label, Revision):
        if label.kind != kind:
            raise RevisionError("wrong_object_type", label.label, f"{label.label!r} was resolved as a {label.kind}, not a {kind}")
        return label
    check_syntax(label)
    r = git.run(["rev-parse", "--verify", "--quiet", "--end-of-options", f"{label}^{{{kind}}}"], cwd=cwd, timeout=timeout)
    if not r.ok:
        raise _classify_failure(label, kind, cwd, r.stderr)
    sha = r.text
    if not is_full_oid(sha):
        raise RevisionError("unexpected_output", label, "git returned something other than a full object id")
    return Revision(label, sha, kind)


def try_resolve(label: Union[str, Revision, None], cwd=None, kind: str = "commit") -> Optional[Revision]:
    """Like :func:`resolve` but returns None for *any* RevisionError (use when absence is an ordinary outcome)."""
    if label is None:
        return None
    try:
        return resolve(label, cwd, kind)
    except RevisionError:
        return None


def sha_of(value: Union[str, Revision], cwd=None, kind: str = "commit", trust_full_oid: bool = True) -> str:
    """The full object id for ``value`` (a :class:`Revision` or a label).

    With ``trust_full_oid`` (default) an *already full object id* is returned without a Git round-trip: a
    40/64-digit hex string cannot be option-shaped and Git itself reports a missing object.  Anything else is
    always resolved with :func:`resolve`.
    """
    if isinstance(value, Revision):
        return resolve(value, cwd, kind).sha
    if trust_full_oid and is_full_oid(value):
        return value
    return resolve(value, cwd, kind).sha


def resolve_range(spec: str, cwd=None, kind: str = "commit") -> RevisionRange:
    """Normalize ``A..B`` / ``A...B`` by resolving each endpoint separately (an empty endpoint means HEAD)."""
    if not isinstance(spec, str) or not spec:
        check_syntax(spec)
    if "..." in spec:
        left, _, right = spec.partition("...")
        sym = True
    elif ".." in spec:
        left, _, right = spec.partition("..")
        sym = False
    else:
        raise RevisionError("unsupported_syntax", spec, "not a range (expected A..B or A...B)")
    if left.startswith("-") or right.startswith("-"):
        raise RevisionError("option_shaped", spec, "range endpoint must not start with '-' (option-shaped input)")
    if _CONTROL.search(spec):
        raise RevisionError("control_characters", spec, "revision contains control characters or whitespace")
    return RevisionRange(spec, resolve(left or "HEAD", cwd, kind), resolve(right or "HEAD", cwd, kind), sym)

