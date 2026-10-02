"""Argv-only, read-only Git subprocess primitives.

All entry points require an explicit working directory and timeout. Commands
return their exit status as data; only process-start and timeout failures raise
``GitError`` subclasses. No shell is used and this module exposes no ref/index
mutation operation.
"""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .models import (
    GitCommandError,
    GitExecutionError,
    GitResult,
    GitTimeoutError,
    RepoInfo,
)

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
MAX_RECORD_LIMIT = 1_000


@dataclass(frozen=True, slots=True)
class BoundedGitResult(GitResult):
    """A Git result whose captured text was capped for safe presentation."""

    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True, slots=True)
class AheadBehind:
    upstream: str
    ahead: int
    behind: int


@dataclass(frozen=True, slots=True)
class ChangedPath:
    path: str
    index_status: str
    worktree_status: str
    staged: bool
    unstaged: bool
    untracked: bool
    original_path: str | None = None


@dataclass(frozen=True, slots=True)
class CommitMetadata:
    commit: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: int
    committer_name: str
    committer_email: str
    committed_at: int
    subject: str


@dataclass(frozen=True, slots=True)
class ReflogEntry:
    commit: str
    selector: str
    subject: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class StashEntry:
    commit: str
    selector: str
    subject: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    path: Path
    head: str | None
    branch: str | None
    detached: bool
    bare: bool
    locked: bool
    prunable: bool


@dataclass(frozen=True, slots=True)
class BlameLine:
    commit: str
    original_line: int
    final_line: int
    author: str | None
    author_time: int | None
    summary: str | None
    filename: str | None
    text: str


@dataclass(frozen=True, slots=True)
class BranchInfo:
    name: str
    commit: str
    current: bool = False


def _resolve_cwd(cwd: str | Path) -> Path:
    """Resolve and validate cwd before passing it to a child process."""
    try:
        resolved = Path(cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Git working directory is not resolvable: {exc}") from exc
    if not resolved.is_dir():
        raise ValueError("Git working directory must be a directory")
    return resolved


def _validate_args(args: Sequence[str]) -> tuple[str, ...]:
    if isinstance(args, (str, bytes)):
        raise TypeError("args must be a sequence of individual strings")
    normalized = tuple(args)
    if any(not isinstance(arg, str) for arg in normalized):
        raise TypeError("every Git argument must be a string")
    if any("\x00" in arg for arg in normalized):
        raise ValueError("Git arguments cannot contain NUL bytes")
    return normalized


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _strip_output_newline(value: str) -> str:
    """Remove Git's one record terminator without trimming filename bytes."""
    return value[:-1] if value.endswith("\n") else value


def run_git(
    args: Sequence[str],
    *,
    cwd: str | Path,
    timeout: float,
) -> GitResult:
    """Run ``git`` with literal argv, returning stdout/stderr without trimming.

    Non-zero Git exit statuses are represented by ``GitResult.returncode`` so
    callers can decide whether they are expected. A timeout or inability to
    start Git raises a typed ``GitError``.
    """
    git_args = _validate_args(args)
    if not git_args:
        raise ValueError("at least one Git subcommand argument is required")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    workdir = _resolve_cwd(cwd)
    command = ("git", *git_args)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = (time.monotonic() - start) * 1000
        raise GitTimeoutError(
            args=command,
            cwd=workdir,
            timeout_seconds=timeout,
            duration_ms=duration_ms,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr),
        ) from exc
    except OSError as exc:
        duration_ms = (time.monotonic() - start) * 1000
        # Keep diagnostics useful without retaining exception reprs that may
        # include platform-specific paths or unrelated process details.
        raise GitExecutionError(
            args=command,
            cwd=workdir,
            duration_ms=duration_ms,
            cause=exc.strerror or exc.__class__.__name__,
        ) from exc

    return GitResult(
        args=command,
        cwd=workdir,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=(time.monotonic() - start) * 1000,
    )


def _checked(args: Sequence[str], *, cwd: str | Path, timeout: float) -> GitResult:
    result = run_git(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise GitCommandError(result)
    return result


def repo_root(*, cwd: str | Path, timeout: float) -> Path:
    """Return the worktree root, raising ``GitCommandError`` outside a repo."""
    result = _checked(("rev-parse", "--show-toplevel"), cwd=cwd, timeout=timeout)
    return Path(_strip_output_newline(result.stdout)).resolve(strict=True)


def git_dir(*, cwd: str | Path, timeout: float) -> Path:
    """Return the per-worktree Git directory (not necessarily the common dir)."""
    result = _checked(("rev-parse", "--absolute-git-dir"), cwd=cwd, timeout=timeout)
    return Path(_strip_output_newline(result.stdout)).resolve(strict=True)


def git_common_dir(*, cwd: str | Path, timeout: float) -> Path:
    """Return the common Git directory, resolving relative output from repo root."""
    root = repo_root(cwd=cwd, timeout=timeout)
    result = _checked(("rev-parse", "--git-common-dir"), cwd=root, timeout=timeout)
    common = Path(_strip_output_newline(result.stdout))
    if not common.is_absolute():
        common = root / common
    return common.resolve(strict=True)


def current_branch(*, cwd: str | Path, timeout: float) -> str | None:
    """Return the current branch, or ``None`` for a detached HEAD."""
    result = _checked(("branch", "--show-current"), cwd=cwd, timeout=timeout)
    branch = result.stdout.rstrip("\r\n")
    return branch or None


def head_commit(*, cwd: str | Path, timeout: float) -> str | None:
    """Return the current commit SHA, or ``None`` for an unborn repository."""
    result = run_git(
        ("rev-parse", "--verify", "--quiet", "--end-of-options", "HEAD^{commit}"),
        cwd=cwd,
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\r\n") or None


def status_porcelain(*, cwd: str | Path, timeout: float) -> str:
    """Return NUL-delimited porcelain-v1 status, preserving paths verbatim."""
    result = _checked(
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        cwd=cwd,
        timeout=timeout,
    )
    return result.stdout


def repo_info(*, cwd: str | Path, timeout: float) -> RepoInfo:
    """Resolve repository paths, branch, and HEAD from one requested location."""
    root = repo_root(cwd=cwd, timeout=timeout)
    return RepoInfo(
        root=root,
        git_dir=git_dir(cwd=root, timeout=timeout),
        common_dir=git_common_dir(cwd=root, timeout=timeout),
        branch=current_branch(cwd=root, timeout=timeout),
        head=head_commit(cwd=root, timeout=timeout),
    )


def _run_git_limited(
    args: Sequence[str], *, cwd: str | Path, timeout: float, max_output_bytes: int
) -> BoundedGitResult:
    """Run Git while draining both pipes and retaining only bounded output."""
    git_args = _validate_args(args)
    if not git_args:
        raise ValueError("at least one Git subcommand argument is required")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be greater than zero")
    workdir = _resolve_cwd(cwd)
    command = ("git", *git_args)
    start = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as exc:
        raise GitExecutionError(
            args=command,
            cwd=workdir,
            duration_ms=(time.monotonic() - start) * 1000,
            cause=exc.strerror or exc.__class__.__name__,
        ) from exc

    streams: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    expired = False
    try:
        while selector.get_map():
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                expired = True
                process.kill()
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                name = key.data
                remaining_capacity = max_output_bytes - len(streams[name])
                if remaining_capacity > 0:
                    streams[name].extend(chunk[:remaining_capacity])
                if len(chunk) > remaining_capacity:
                    truncated[name] = True
        if expired:
            # Reap the process and collect the final pipe bytes, retaining at
            # most the same per-stream cap.
            tail_out, tail_err = process.communicate()
            for name, tail in (("stdout", tail_out), ("stderr", tail_err)):
                capacity = max_output_bytes - len(streams[name])
                if capacity > 0:
                    streams[name].extend(tail[:capacity])
                if len(tail) > capacity:
                    truncated[name] = True
            duration_ms = (time.monotonic() - start) * 1000
            raise GitTimeoutError(
                args=command,
                cwd=workdir,
                timeout_seconds=timeout,
                duration_ms=duration_ms,
                stdout=bytes(streams["stdout"]).decode("utf-8", errors="replace"),
                stderr=bytes(streams["stderr"]).decode("utf-8", errors="replace"),
            )
        return BoundedGitResult(
            args=command,
            cwd=workdir,
            returncode=process.wait(),
            stdout=bytes(streams["stdout"]).decode("utf-8", errors="replace"),
            stderr=bytes(streams["stderr"]).decode("utf-8", errors="replace"),
            duration_ms=(time.monotonic() - start) * 1000,
            stdout_truncated=truncated["stdout"],
            stderr_truncated=truncated["stderr"],
        )
    finally:
        selector.close()
        for pipe in (process.stdout, process.stderr):
            if not pipe.closed:
                pipe.close()


def _checked_limited(
    args: Sequence[str], *, cwd: str | Path, timeout: float, max_output_bytes: int
) -> BoundedGitResult:
    result = _run_git_limited(args, cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes)
    if result.returncode != 0:
        raise GitCommandError(result)
    if result.stdout_truncated or result.stderr_truncated:
        raise ValueError("Git output exceeded max_output_bytes; increase the explicit bound")
    return result


def _validate_limit(limit: int, *, name: str = "limit") -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECORD_LIMIT:
        raise ValueError(f"{name} must be an integer from 1 to {MAX_RECORD_LIMIT}")


def _validate_ref(revision: str, *, name: str = "revision") -> str:
    if not isinstance(revision, str) or not revision or revision.startswith("-") or "\x00" in revision:
        raise ValueError(f"{name} must be a non-empty Git revision that does not start with '-' ")
    return revision


def _validate_paths(paths: Sequence[str]) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes)):
        raise TypeError("paths must be a sequence of individual path strings")
    normalized = tuple(paths)
    if any(not isinstance(path, str) for path in normalized):
        raise TypeError("every path must be a string")
    if any("\x00" in path for path in normalized):
        raise ValueError("paths cannot contain NUL bytes")
    return normalized


def upstream(*, cwd: str | Path, timeout: float, branch: str | None = None) -> str | None:
    """Return a branch's configured upstream ref, or ``None`` if absent."""
    if branch is None:
        revision = "@{upstream}"
    else:
        _validate_ref(branch, name="branch")
        revision = f"{branch}@{{upstream}}"
    result = run_git(
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "--verify", "--quiet", "--end-of-options", revision),
        cwd=cwd,
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\r\n") or None


def ahead_behind(
    *, cwd: str | Path, timeout: float, upstream_ref: str | None = None
) -> AheadBehind | None:
    """Count commits reachable only from HEAD and only from its upstream."""
    tracking = upstream_ref or upstream(cwd=cwd, timeout=timeout)
    if tracking is None:
        return None
    _validate_ref(tracking, name="upstream_ref")
    result = _checked(
        ("rev-list", "--left-right", "--count", f"HEAD...{tracking}"),
        cwd=cwd,
        timeout=timeout,
    )
    fields = result.stdout.split()
    if len(fields) != 2 or not all(field.isdecimal() for field in fields):
        raise ValueError("git rev-list returned an unexpected ahead/behind result")
    return AheadBehind(upstream=tracking, ahead=int(fields[0]), behind=int(fields[1]))


def changed_paths(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[ChangedPath, ...]:
    """Return parsed staged, unstaged, and untracked status entries.

    Git's NUL-delimited status format keeps spaces, tabs, and newlines in paths
    unambiguous. Rename/copy entries include the original path as the next NUL
    field, which is retained separately.
    """
    output = _checked_limited(
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
        cwd=cwd,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    ).stdout
    fields = output.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    found: list[ChangedPath] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4 or entry[2] != " ":
            raise ValueError("git status returned malformed porcelain-v1 output")
        x, y, path = entry[0], entry[1], entry[3:]
        original_path = None
        if x in ("R", "C") or y in ("R", "C"):
            if index >= len(fields):
                raise ValueError("git status omitted the original path for a rename/copy")
            original_path = fields[index]
            index += 1
        untracked = x == "?" and y == "?"
        found.append(
            ChangedPath(
                path=path,
                index_status=x,
                worktree_status=y,
                staged=not untracked and x not in (" ", "?"),
                unstaged=not untracked and y not in (" ", "?"),
                untracked=untracked,
                original_path=original_path,
            )
        )
    return tuple(found)


def staged_paths(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[ChangedPath, ...]:
    return tuple(path for path in changed_paths(cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes) if path.staged)


def unstaged_paths(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[ChangedPath, ...]:
    return tuple(path for path in changed_paths(cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes) if path.unstaged)


def untracked_paths(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[ChangedPath, ...]:
    return tuple(path for path in changed_paths(cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes) if path.untracked)


def diff(
    *,
    cwd: str | Path,
    timeout: float,
    staged: bool = False,
    paths: Sequence[str] = (),
    context_lines: int = 3,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> BoundedGitResult:
    """Return a no-color, no-external-diff patch with bounded captured text."""
    selected_paths = _validate_paths(paths)
    if isinstance(context_lines, bool) or not isinstance(context_lines, int) or not 0 <= context_lines <= 100:
        raise ValueError("context_lines must be an integer from 0 to 100")
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", f"--unified={context_lines}"]
    if staged:
        args.append("--cached")
    if selected_paths:
        args.extend(("--", *selected_paths))
    return _run_git_limited(args, cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes)


def merge_base(*, cwd: str | Path, timeout: float, left: str, right: str) -> str | None:
    """Return the best common ancestor of two revisions, or ``None`` if none."""
    left_ref = _validate_ref(left, name="left")
    right_ref = _validate_ref(right, name="right")
    result = run_git(("merge-base", "--", left_ref, right_ref), cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\r\n") or None


def _parse_commit_record(fields: Sequence[str]) -> CommitMetadata:
    if len(fields) != 9:
        raise ValueError("Git returned malformed commit metadata")
    commit, parents, author, author_email, authored, committer, committer_email, committed, subject = fields
    try:
        authored_at = int(authored)
        committed_at = int(committed)
    except ValueError as exc:
        raise ValueError("Git returned invalid commit timestamps") from exc
    return CommitMetadata(
        commit=commit,
        parents=tuple(parents.split()) if parents else (),
        author_name=author,
        author_email=author_email,
        authored_at=authored_at,
        committer_name=committer,
        committer_email=committer_email,
        committed_at=committed_at,
        subject=subject,
    )


_COMMIT_FORMAT = "%H%x00%P%x00%an%x00%ae%x00%at%x00%cn%x00%ce%x00%ct%x00%s%x00"


def commit_metadata(
    *, cwd: str | Path, timeout: float, revision: str = "HEAD",
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CommitMetadata:
    """Return selected commit metadata without patch or file contents."""
    selected = _validate_ref(revision)
    result = _checked_limited(
        ("show", "-s", f"--format=format:{_COMMIT_FORMAT}", "--no-show-signature", selected),
        cwd=cwd,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    fields = result.stdout.rstrip("\0\n\r").split("\0")
    return _parse_commit_record(fields)


def _parse_nul_records(output: str, field_count: int) -> tuple[tuple[str, ...], ...]:
    fields = output.split("\0")
    while fields and fields[-1] == "":
        fields.pop()
    if len(fields) % field_count:
        raise ValueError("Git returned malformed NUL-delimited records")
    # Git's pretty-format adds a newline between records unless the format
    # uses a record separator. Drop that separator only from each first field.
    for index in range(0, len(fields), field_count):
        fields[index] = fields[index].lstrip("\r\n")
    return tuple(tuple(fields[i : i + field_count]) for i in range(0, len(fields), field_count))


def reflog(
    *, cwd: str | Path, timeout: float, limit: int = 20,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[ReflogEntry, ...]:
    """Return a bounded list of local reflog records."""
    _validate_limit(limit)
    result = _checked_limited(
        ("reflog", "show", f"--format=format:%H%x00%gD%x00%gs%x00%ct%x00", "-n", str(limit)),
        cwd=cwd,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    entries = []
    for commit, selector, subject, timestamp in _parse_nul_records(result.stdout, 4):
        entries.append(ReflogEntry(commit, selector, subject, int(timestamp)))
    return tuple(entries)


def stash_list(
    *, cwd: str | Path, timeout: float, limit: int = 100,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[StashEntry, ...]:
    """Return a bounded list of stash metadata; does not inspect stash patches."""
    _validate_limit(limit)
    result = _checked_limited(
        ("stash", "list", f"--format=format:%H%x00%gd%x00%gs%x00%ct%x00", "-n", str(limit)),
        cwd=cwd,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    entries = []
    for commit, selector, subject, timestamp in _parse_nul_records(result.stdout, 4):
        entries.append(StashEntry(commit, selector, subject, int(timestamp)))
    return tuple(entries)


def worktree_list(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[WorktreeInfo, ...]:
    """Return worktree metadata using Git's NUL-delimited porcelain format."""
    result = _checked_limited(
        ("worktree", "list", "--porcelain", "-z"), cwd=cwd, timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    records: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] = {}
    for field in result.stdout.split("\0"):
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        if field == "bare":
            current["bare"] = True
        elif field == "detached":
            current["detached"] = True
        elif field == "locked" or field.startswith("locked "):
            current["locked"] = True
        elif field == "prunable" or field.startswith("prunable "):
            current["prunable"] = True
        elif " " in field:
            key, value = field.split(" ", 1)
            current[key] = value
    if current:
        records.append(current)
    worktrees: list[WorktreeInfo] = []
    for record in records:
        path = record.get("worktree")
        if not isinstance(path, str):
            raise ValueError("Git worktree record omitted its path")
        head = record.get("HEAD")
        branch = record.get("branch")
        worktrees.append(
            WorktreeInfo(
                path=Path(path),
                head=head if isinstance(head, str) else None,
                branch=branch if isinstance(branch, str) else None,
                detached=bool(record.get("detached", False)),
                bare=bool(record.get("bare", False)),
                locked=bool(record.get("locked", False)),
                prunable=bool(record.get("prunable", False)),
            )
        )
    return tuple(worktrees)


def blame(
    *,
    cwd: str | Path,
    timeout: float,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[BlameLine, ...]:
    """Return parsed line attribution for one path and an optional bounded range."""
    selected_paths = _validate_paths((path,))
    if start_line is not None or end_line is not None:
        start = 1 if start_line is None else start_line
        end = start if end_line is None else end_line
        if any(isinstance(line, bool) or not isinstance(line, int) or line < 1 for line in (start, end)):
            raise ValueError("blame line bounds must be positive integers")
        if end < start or end - start + 1 > MAX_RECORD_LIMIT:
            raise ValueError("blame line range must be ordered and at most 1000 lines")
        range_args = ("-L", f"{start},{end}")
    else:
        range_args = ("-L", "1,1000")
    result = _checked_limited(
        ("blame", "--line-porcelain", "--no-textconv", *range_args, "--", *selected_paths),
        cwd=cwd,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    lines: list[BlameLine] = []
    metadata: dict[str, str] = {}
    current_commit = ""
    original_line = final_line = 0
    for line in result.stdout.splitlines():
        header = line.split(" ")
        if len(header) == 3 and len(header[0]) in (40, 64) and all(ch in "0123456789abcdef" for ch in header[0]):
            current_commit = header[0]
            try:
                original_line, final_line = int(header[1]), int(header[2])
            except ValueError:
                metadata = {}
            else:
                metadata = {}
            continue
        if line.startswith("\t"):
            lines.append(
                BlameLine(
                    commit=current_commit,
                    original_line=original_line,
                    final_line=final_line,
                    author=metadata.get("author"),
                    author_time=int(metadata["author-time"]) if metadata.get("author-time", "").isdigit() else None,
                    summary=metadata.get("summary"),
                    filename=metadata.get("filename"),
                    text=line[1:],
                )
            )
            continue
        key, separator, value = line.partition(" ")
        if separator:
            metadata[key] = value
    return tuple(lines)


def show(
    *,
    cwd: str | Path,
    timeout: float,
    revision: str = "HEAD",
    paths: Sequence[str] = (),
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> BoundedGitResult:
    """Return a bounded commit patch with external diff/textconv disabled."""
    selected = _validate_ref(revision)
    selected_paths = _validate_paths(paths)
    args = ["show", "--no-ext-diff", "--no-textconv", "--no-color", "--format=fuller", "--stat", "--patch", selected]
    if selected_paths:
        args.extend(("--", *selected_paths))
    return _run_git_limited(args, cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes)


def log(
    *,
    cwd: str | Path,
    timeout: float,
    revision: str = "HEAD",
    limit: int = 50,
    paths: Sequence[str] = (),
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[CommitMetadata, ...]:
    """Return bounded commit metadata, optionally restricted to path history."""
    selected = _validate_ref(revision)
    selected_paths = _validate_paths(paths)
    _validate_limit(limit)
    args = ["log", f"--format=format:{_COMMIT_FORMAT}", "--no-show-signature", "-n", str(limit), selected]
    if selected_paths:
        args.extend(("--", *selected_paths))
    result = _checked_limited(args, cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes)
    records = _parse_nul_records(result.stdout, 9)
    return tuple(_parse_commit_record(record) for record in records)


def _branches(
    *, cwd: str | Path, timeout: float, remote: bool,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[BranchInfo, ...]:
    args = ["branch", "--format=%(refname:short)%00%(objectname)%00%(HEAD)"]
    if remote:
        args.append("--remotes")
    result = _checked_limited(args, cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes)
    records = []
    for line in result.stdout.splitlines():
        values = line.split("\0")
        if len(values) != 3:
            raise ValueError("git branch returned malformed branch metadata")
        name, commit, current = values
        records.append(BranchInfo(name=name, commit=commit, current=current == "*"))
    return tuple(records)


def local_branches(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[BranchInfo, ...]:
    """Return local branch names and tip commit IDs."""
    return _branches(cwd=cwd, timeout=timeout, remote=False, max_output_bytes=max_output_bytes)


def remote_branches(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[BranchInfo, ...]:
    """Return remote-tracking branch names and tip commit IDs."""
    return _branches(cwd=cwd, timeout=timeout, remote=True, max_output_bytes=max_output_bytes)


def remotes(
    *, cwd: str | Path, timeout: float, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> tuple[str, ...]:
    """Return configured remote names only; credential-bearing URLs are omitted."""
    result = _checked_limited(("remote",), cwd=cwd, timeout=timeout, max_output_bytes=max_output_bytes)
    return tuple(line for line in result.stdout.splitlines() if line)
