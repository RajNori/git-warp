"""Typed results and errors shared by Git Warp's Git-facing services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class GitResult:
    """The complete, bounded result of one Git subprocess invocation."""

    args: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float


class GitError(RuntimeError):
    """Base class for a Git process that could not produce a normal result."""

    def __init__(
        self,
        message: str,
        *,
        args: Sequence[str],
        cwd: Path,
        duration_ms: float | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.args_vector = tuple(args)
        self.cwd = cwd
        self.duration_ms = duration_ms
        self.stdout = stdout
        self.stderr = stderr


class GitCommandError(GitError):
    """A Git command completed but returned a non-zero exit status."""

    def __init__(self, result: GitResult) -> None:
        self.result = result
        super().__init__(
            f"git command exited with status {result.returncode}",
            args=result.args,
            cwd=result.cwd,
            duration_ms=result.duration_ms,
            stdout=result.stdout,
            stderr=result.stderr,
        )


class GitTimeoutError(GitError):
    """A Git command exceeded its explicitly supplied timeout."""

    def __init__(
        self,
        *,
        args: Sequence[str],
        cwd: Path,
        timeout_seconds: float,
        duration_ms: float,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"git command exceeded timeout of {timeout_seconds:g} seconds",
            args=args,
            cwd=cwd,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
        )


class GitExecutionError(GitError):
    """Git could not be started, for example because it is not installed."""

    def __init__(
        self,
        *,
        args: Sequence[str],
        cwd: Path,
        duration_ms: float,
        cause: str,
    ) -> None:
        self.cause = cause
        super().__init__(
            f"could not execute git: {cause}",
            args=args,
            cwd=cwd,
            duration_ms=duration_ms,
        )


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """Resolved paths and identity for a repository and its current checkout."""

    root: Path
    git_dir: Path
    common_dir: Path
    branch: str | None
    head: str | None
