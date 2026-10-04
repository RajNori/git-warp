"""Central Git execution layer.

Every Git invocation in Git Warp goes through :func:`run`.  Arguments are
always passed as an argv list (never a shell string), with a timeout, a
neutral locale and without terminal prompts or optional index locks, so
analysis never mutates the repository as a side effect.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

PathLike = Union[str, os.PathLike]

DEFAULT_TIMEOUT = 15
_FS = "\x1f"  # field separator used in --format strings
_RS = "\x1e"  # record separator


class GitError(Exception):
    """A Git command failed."""

    def __init__(self, message: str, args_: Sequence[str] = (), returncode: int = 1, stderr: str = ""):
        super().__init__(message)
        self.git_args = list(args_)
        self.returncode = returncode
        self.stderr = stderr


class GitNotFound(GitError):
    """The git executable is not available."""


class NotARepository(GitError):
    """The directory is not inside a Git repository."""


class GitTimeout(GitError):
    """The Git command exceeded its timeout."""


@dataclass(frozen=True)
class Result:
    args: tuple
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return self.stdout.strip()

    @property
    def lines(self) -> list:
        return [ln for ln in self.stdout.splitlines() if ln.strip()]


def run(
    args: Sequence[str],
    cwd: Optional[PathLike] = None,
    timeout: float = DEFAULT_TIMEOUT,
    check: bool = False,
    input: Optional[str] = None,
    env: Optional[dict] = None,
) -> Result:
    """Run ``git <args>`` and return a :class:`Result`.

    ``check=True`` raises :class:`GitError` on a non-zero exit.  Timeouts and a
    missing git binary always raise.  Never uses a shell.
    """
    argv = ["git", *[str(a) for a in args]]
    environ = dict(os.environ)
    environ.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_PAGER": "cat"})
    if env:
        environ.update(env)
    try:
        p = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            input=input,
            env=environ,
            check=False,
        )
    except FileNotFoundError as e:
        if cwd is not None and not Path(cwd).is_dir():
            raise NotARepository(f"directory does not exist: {cwd}", argv[1:], 128, str(e)) from e
        raise GitNotFound("git executable not found", argv[1:], 127, str(e)) from e
    except subprocess.TimeoutExpired as e:
        raise GitTimeout(f"git {' '.join(map(str, args[:3]))} timed out after {timeout}s", argv[1:], 124) from e
    except NotADirectoryError as e:
        raise NotARepository(str(e), argv[1:], 128, str(e)) from e
    except OSError as e:
        raise GitError(str(e), argv[1:], 1, str(e)) from e
    res = Result(tuple(argv[1:]), p.returncode, p.stdout, p.stderr)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(argv[1:4])} failed: {p.stderr.strip()[:300]}", argv[1:], p.returncode, p.stderr)
    return res


def check_ref(ref: str) -> str:
    """Reject refs that could be parsed as options."""
    if not ref or ref.startswith("-") or "\x00" in ref or "\n" in ref:
        raise ValueError(f"unsafe ref: {ref!r}")
    return ref


# --------------------------------------------------------------------------- repo identity

def is_git_repository(cwd: Optional[PathLike] = None) -> bool:
    try:
        r = run(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    except (GitError, ValueError):
        return False
    return r.ok and r.text == "true"


def repo_root(cwd: Optional[PathLike] = None, timeout: float = DEFAULT_TIMEOUT) -> Optional[Path]:
    """Top level of the work tree, or None (bare repo / not a repo)."""
    try:
        r = run(["rev-parse", "--show-toplevel"], cwd=cwd, timeout=timeout)
    except GitError:
        return None
    return Path(r.text) if r.ok and r.text else None


def git_dir(cwd: Optional[PathLike] = None) -> Path:
    """Absolute per-worktree git dir."""
    r = run(["rev-parse", "--absolute-git-dir"], cwd=cwd)
    if not r.ok:
        raise NotARepository(r.stderr.strip() or "not a git repository", r.args, r.returncode, r.stderr)
    return Path(r.text)


def common_dir(cwd: Optional[PathLike] = None) -> Path:
    """Absolute git dir shared by all worktrees (where runtime state lives)."""
    r = run(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=cwd)
    if not r.ok:
        raise NotARepository(r.stderr.strip() or "not a git repository", r.args, r.returncode, r.stderr)
    return Path(r.text)


def state_dir(cwd: Optional[PathLike] = None, create: bool = False) -> Path:
    """``<common git dir>/git-warp`` — the only place Git Warp writes state.

    Path resolution only: how the directory is created, checked and protected is owned by
    :mod:`gitwarp.core.storage` (the single secure-storage abstraction).
    """
    from . import storage
    return storage.state_dir(common_dir(cwd), create=create)


def is_shallow(cwd: Optional[PathLike] = None) -> bool:
    r = run(["rev-parse", "--is-shallow-repository"], cwd=cwd)
    return r.ok and r.text == "true"


# --------------------------------------------------------------------------- refs

def current_branch(cwd: Optional[PathLike] = None, timeout: float = DEFAULT_TIMEOUT) -> Optional[str]:
    """Branch name; None when detached. Works on an unborn branch."""
    r = run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd, timeout=timeout)
    return r.text if r.ok and r.text else None


def head_sha(cwd: Optional[PathLike] = None) -> Optional[str]:
    """Full SHA of HEAD, or None on an unborn repository."""
    r = run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], cwd=cwd)
    return r.text if r.ok and r.text else None


def rev_parse(ref: str, cwd: Optional[PathLike] = None) -> Optional[str]:
    r = run(["rev-parse", "--verify", "--quiet", check_ref(ref) + "^{commit}"], cwd=cwd)
    return r.text if r.ok and r.text else None


def upstream(cwd: Optional[PathLike] = None) -> Optional[str]:
    r = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=cwd)
    return r.text if r.ok and r.text else None


def ahead_behind(cwd: Optional[PathLike] = None, upstream_ref: Optional[str] = None) -> Optional[tuple]:
    """(ahead, behind) of HEAD relative to upstream, or None when there is none."""
    up = upstream_ref or upstream(cwd)
    if not up:
        return None
    r = run(["rev-list", "--left-right", "--count", f"HEAD...{check_ref(up)}"], cwd=cwd)
    if not r.ok:
        return None
    try:
        a, b = r.text.split()
        return int(a), int(b)
    except ValueError:
        return None


def merge_base(a: str, b: str, cwd: Optional[PathLike] = None) -> Optional[str]:
    r = run(["merge-base", check_ref(a), check_ref(b)], cwd=cwd)
    return r.text if r.ok and r.text else None


def default_branch(cwd: Optional[PathLike] = None) -> Optional[str]:
    """Best-effort default base ref: origin/HEAD, else main/master if present."""
    r = run(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd=cwd)
    if r.ok and r.text:
        return r.text
    for cand in ("origin/main", "origin/master", "main", "master"):
        if rev_parse(cand, cwd):
            return cand
    return None


def branches(cwd: Optional[PathLike] = None, remote: bool = False) -> list:
    pattern = "refs/remotes" if remote else "refs/heads"
    r = run(["for-each-ref", "--format=%(refname:short)" + _FS + "%(objectname)", pattern], cwd=cwd)
    out = []
    for ln in r.lines:
        name, _, sha = ln.partition(_FS)
        out.append({"name": name, "sha": sha})
    return out


# --------------------------------------------------------------------------- state

@dataclass(frozen=True)
class StatusEntry:
    xy: str
    path: str
    orig_path: Optional[str] = None

    @property
    def staged(self) -> bool:
        return self.xy[0] not in " ?!"

    @property
    def unstaged(self) -> bool:
        return self.xy[1] not in " ?!"

    @property
    def untracked(self) -> bool:
        return self.xy == "??"

    @property
    def conflicted(self) -> bool:
        return self.xy in ("DD", "AU", "UD", "UA", "DU", "AA", "UU")


def working_tree_status(cwd: Optional[PathLike] = None, untracked: str = "normal") -> list:
    """Parsed ``status --porcelain=v1 -z`` (robust to spaces/newlines in paths)."""
    r = run(["status", "--porcelain=v1", "-z", f"--untracked-files={untracked}"], cwd=cwd, timeout=30)
    if not r.ok:
        raise NotARepository(r.stderr.strip() or "git status failed", r.args, r.returncode, r.stderr)
    parts = r.stdout.split("\x00")
    entries, i = [], 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        orig = None
        if xy[0] in "RC" or xy[1] in "RC":
            orig = parts[i] if i < len(parts) else None
            i += 1
        entries.append(StatusEntry(xy, path, orig))
    return entries


def changed_files(cwd: Optional[PathLike] = None, base: Optional[str] = None, head: str = "HEAD") -> list:
    """Paths changed. With ``base``: ``base...head`` (merge-base diff); else working tree vs HEAD incl. untracked."""
    if base:
        r = run(["diff", "--name-only", "-z", f"{check_ref(base)}...{check_ref(head)}"], cwd=cwd, timeout=30)
        return [p for p in r.stdout.split("\x00") if p] if r.ok else []
    seen, out = set(), []
    for e in working_tree_status(cwd):
        if e.path not in seen:
            seen.add(e.path)
            out.append(e.path)
    return out


def numstat(cwd: Optional[PathLike] = None, args: Sequence[str] = ()) -> list:
    """List of dicts ``{added, deleted, path, binary}`` for ``git diff --numstat <args>``."""
    r = run(["diff", "--numstat", "-z", *args], cwd=cwd, timeout=30)
    out = []
    if not r.ok:
        return out
    parts = r.stdout.split("\x00")
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if not item:
            continue
        a, _, rest = item.partition("\t")
        d, _, path = rest.partition("\t")
        if path == "":  # rename: path follows as two NUL-separated entries
            i += 1
            path = parts[i] if i < len(parts) else ""
            i += 1
        binary = a == "-" or d == "-"
        out.append({"added": 0 if binary else int(a or 0), "deleted": 0 if binary else int(d or 0), "path": path, "binary": binary})
    return out


def repo_operation(cwd: Optional[PathLike] = None) -> Optional[str]:
    """In-progress operation: merge, rebase, cherry-pick, revert, bisect, or None."""
    try:
        gd = git_dir(cwd)
    except GitError:
        return None
    if (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists():
        return "rebase"
    for marker, name in (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"), ("BISECT_LOG", "bisect")):
        if (gd / marker).exists():
            return name
    return None


def stashes(cwd: Optional[PathLike] = None) -> list:
    r = run(["stash", "list", "--format=%gd" + _FS + "%H" + _FS + "%cI" + _FS + "%gs"], cwd=cwd)
    out = []
    for ln in r.lines:
        sel, sha, date, subj = (ln.split(_FS) + ["", "", "", ""])[:4]
        out.append({"ref": sel, "sha": sha, "date": date, "subject": subj})
    return out


def worktrees(cwd: Optional[PathLike] = None) -> list:
    r = run(["worktree", "list", "--porcelain", "-z"], cwd=cwd)
    out, cur = [], {}
    for tok in r.stdout.split("\x00"):
        if not tok:
            if cur:
                out.append(cur)
                cur = {}
            continue
        key, _, val = tok.partition(" ")
        if key == "worktree":
            if cur:
                out.append(cur)
            cur = {"path": val}
        elif key in ("HEAD", "branch"):
            cur[key.lower()] = val
        elif key in ("bare", "detached", "locked", "prunable"):
            cur[key] = val or True
    if cur:
        out.append(cur)
    return out


def reflog(ref: str = "HEAD", limit: int = 100, cwd: Optional[PathLike] = None) -> list:
    """Reflog entries newest first: ``{sha, selector, message, date}``. Empty when none."""
    r = run(["reflog", "show", f"-n{int(limit)}", "--format=%H" + _FS + "%gd" + _FS + "%gs" + _FS + "%cI", check_ref(ref)], cwd=cwd, timeout=30)
    out = []
    if not r.ok:
        return out
    for ln in r.lines:
        sha, sel, msg, date = (ln.split(_FS) + ["", "", "", ""])[:4]
        out.append({"sha": sha, "selector": sel, "message": msg, "date": date})
    return out


# --------------------------------------------------------------------------- history

@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple
    author_name: str
    author_email: str
    author_date: str
    commit_date: str
    subject: str
    body: str = ""
    files: tuple = field(default_factory=tuple)

    @property
    def short(self) -> str:
        return self.sha[:8]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


_COMMIT_FMT = _RS + _FS.join(["%H", "%P", "%an", "%ae", "%aI", "%cI", "%s", "%b"]) + _FS


def _parse_commits(stdout: str, with_files: bool) -> list:
    commits = []
    for rec in stdout.split(_RS):
        if not rec.strip():
            continue
        fields = rec.split(_FS)
        if len(fields) < 8:
            continue
        sha, parents, an, ae, ad, cd, subj, body = fields[:8]
        files = ()
        if with_files:
            tail = fields[8] if len(fields) > 8 else ""
            files = tuple(f for f in tail.replace("\x00", "\n").split("\n") if f.strip())
        commits.append(Commit(sha.strip(), tuple(parents.split()), an, ae, ad, cd, subj, body.strip(), files))
    return commits


def log_commits(
    rev_range: Union[str, Sequence[str], None] = "HEAD",
    paths: Sequence[str] = (),
    limit: int = 50,
    with_files: bool = False,
    extra: Sequence[str] = (),
    cwd: Optional[PathLike] = None,
    timeout: float = 60,
) -> list:
    """Structured ``git log``. ``rev_range`` may be a string, a list of revs, or None (HEAD).

    Returns ``[]`` on unborn repositories / unknown revisions rather than raising.
    """
    revs = [rev_range] if isinstance(rev_range, str) else list(rev_range or ["HEAD"])
    for rv in revs:
        check_ref(rv)
    fmt = _COMMIT_FMT + ("%x00" if with_files else "")
    args = ["log", f"-n{int(limit)}", "--format=" + fmt]
    if with_files:
        args += ["--name-only", "-z"]
    args += list(extra) + revs
    if paths:
        args += ["--", *paths]
    r = run(args, cwd=cwd, timeout=timeout)
    if not r.ok:
        return []
    return _parse_commits(r.stdout, with_files)


def commit_metadata(rev: str = "HEAD", cwd: Optional[PathLike] = None, with_files: bool = False) -> Optional[Commit]:
    found = log_commits(rev, limit=1, with_files=with_files, extra=["--no-walk"], cwd=cwd)
    return found[0] if found else None


def show_commit(rev: str, cwd: Optional[PathLike] = None, stat: bool = True, patch: bool = False, max_bytes: int = 200_000) -> str:
    args = ["show", "--no-color", "--format=fuller"]
    if stat:
        args.append("--stat")
    if patch:
        args.append("--patch")
    r = run([*args, check_ref(rev)], cwd=cwd, timeout=60)
    return r.stdout[:max_bytes] if r.ok else ""


def diff(args: Sequence[str] = (), cwd: Optional[PathLike] = None, max_bytes: int = 400_000) -> str:
    """Raw ``git diff`` text (truncated to ``max_bytes``)."""
    r = run(["diff", "--no-color", *args], cwd=cwd, timeout=60)
    return r.stdout[:max_bytes] if r.ok else ""


def blame(path: str, rev: str = "HEAD", lines: Optional[tuple] = None, cwd: Optional[PathLike] = None) -> list:
    """Line blame: ``[{sha, line, author, summary}]``. Empty on failure (e.g. untracked/binary)."""
    args = ["blame", "--line-porcelain"]
    if lines:
        args += ["-L", f"{int(lines[0])},{int(lines[1])}"]
    args += [check_ref(rev), "--", path]
    r = run(args, cwd=cwd, timeout=60)
    out, cur = [], {}
    if not r.ok:
        return out
    for ln in r.stdout.splitlines():
        if ln.startswith("\t"):
            cur["text"] = ln[1:]
            out.append(cur)
            cur = {}
        elif len(ln) >= 40 and ln[:40].isalnum() and " " in ln and "sha" not in cur:
            parts = ln.split()
            cur = {"sha": parts[0], "line": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0}
        elif ln.startswith("author "):
            cur["author"] = ln[7:]
        elif ln.startswith("summary "):
            cur["summary"] = ln[8:]
    return out


def ls_files_stage(cwd: Optional[PathLike] = None, unmerged_only: bool = False) -> list:
    """Index entries ``{mode, sha, stage, path}`` (stage 1/2/3 = base/ours/theirs)."""
    r = run(["ls-files", "--stage", "-z"], cwd=cwd)
    out = []
    for item in r.stdout.split("\x00"):
        meta, _, path = item.partition("\t")
        bits = meta.split()
        if len(bits) != 3:
            continue
        mode, sha, stage = bits
        if unmerged_only and stage == "0":
            continue
        out.append({"mode": mode, "sha": sha, "stage": int(stage), "path": path})
    return out


def tracked_files(cwd: Optional[PathLike] = None, rev: str = "HEAD") -> list:
    r = run(["ls-tree", "-r", "-z", "--name-only", check_ref(rev)], cwd=cwd, timeout=60)
    return [p for p in r.stdout.split("\x00") if p] if r.ok else []


def show_file(rev: str, path: str, cwd: Optional[PathLike] = None, max_bytes: int = 1_000_000) -> Optional[str]:
    """Contents of ``path`` at ``rev`` (or ``:N`` index stage via rev like ':2')."""
    spec = f"{rev}:{path}" if not rev.startswith(":") else f"{rev}:{path}"
    if rev.startswith("-"):
        raise ValueError("unsafe rev")
    r = run(["show", spec], cwd=cwd, timeout=30)
    return r.stdout[:max_bytes] if r.ok else None
