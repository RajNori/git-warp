"""Central Git execution layer: the ONE Git process boundary.

Every Git invocation in Git Warp goes through :func:`run`; no other module may import ``subprocess``
(enforced by ``tests/unit/test_architecture_invariants.py`` and ``tests/unit/test_core_git_boundary.py``).

What :func:`run` guarantees
---------------------------
* argv list only, ``shell=False``, explicit ``cwd`` (the current directory when none is given), finite timeout;
* stdin is ``/dev/null`` unless ``input`` is given; the child can never prompt (``GIT_TERMINAL_PROMPT=0``);
* stdout AND stderr are streamed into capped buffers (``max_output`` / ``max_stderr``); a command that exceeds
  the cap is killed and the :class:`Result` carries ``truncated=True`` (its ``returncode`` is then reported as 0
  because the captured prefix is valid output; callers that need completeness must check ``truncated``);
* the child runs in its own session/process group; on timeout, on over-long output and on exit with lingering
  descendants the WHOLE group is SIGKILLed, so helper processes (fsmonitor, textconv, filters, pagers, ssh)
  die with the git process;
* typed errors: :class:`GitNotFound`, :class:`NotARepository`, :class:`GitTimeout`, :class:`GitRefused`
  (a command outside the allowlist or carrying a forbidden option), :class:`GitError`.

Hostile ENVIRONMENT boundary (the child never inherits these)
-------------------------------------------------------------
``GIT_*`` is handled by ALLOWLIST: no ``GIT_*`` variable of the parent is passed on.  Git Warp sets only
``GIT_OPTIONAL_LOCKS=0``, ``GIT_TERMINAL_PROMPT=0``, ``GIT_PAGER=cat``, ``GIT_EDITOR=true`` and ``GIT_ATTR_NOSYSTEM=1``
(+ ``LC_ALL=C``, ``PAGER=cat``).  So ``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE``, ``GIT_OBJECT_DIRECTORY``,
``GIT_ALTERNATE_OBJECT_DIRECTORIES``, ``GIT_COMMON_DIR``, ``GIT_NAMESPACE``, ``GIT_CEILING_DIRECTORIES``,
``GIT_CONFIG``, ``GIT_CONFIG_COUNT``/``_KEY_n``/``_VALUE_n``, ``GIT_CONFIG_PARAMETERS``, ``GIT_EXTERNAL_DIFF``,
``GIT_DIFF_OPTS``, ``GIT_SSH``, ``GIT_SSH_COMMAND``, ``GIT_ASKPASS``, ``GIT_PROXY_COMMAND``, ``GIT_EXEC_PATH``,
``GIT_TRACE*`` (every variant), ``GIT_PAGER`` (replaced), ``GIT_ALLOW_PROTOCOL`` ... cannot redirect the repository
or execute anything.  The only config-redirection variables that survive are the inert forms
``GIT_CONFIG_GLOBAL`` / ``GIT_CONFIG_SYSTEM`` equal to the null device and a truthy ``GIT_CONFIG_NOSYSTEM`` (they
can only REMOVE configuration; this is how the test-suite isolates itself).  Any other value is dropped, so the
real ``~/.gitconfig`` / ``/etc/gitconfig`` apply: they are the user's own trusted files.  Also removed:
``PAGER``/``MANPAGER``/``LESS*`` (replaced), ``EDITOR``/``VISUAL``, ``SSH_ASKPASS*``, ``LANG``/``LANGUAGE``/``LC_*``
(replaced by ``LC_ALL=C``), ``LD_PRELOAD``/``LD_AUDIT``/``DYLD_INSERT_LIBRARIES``.  Everything else (``PATH``,
``HOME``, ``USER``, ``TMPDIR``, ``XDG_*`` ...) is kept because git needs it.  Environment VALUES are never logged
and never appear in error messages.

Hostile LOCAL CONFIG boundary (suppressed at invocation, whatever file defines it)
----------------------------------------------------------------------------------
Every command gets ``--no-pager --no-optional-locks`` and ``-c`` overrides (see ``_HARDENING``): ``core.fsmonitor``
(false), ``core.hooksPath`` (null device: no hook of any kind fires), ``core.pager``/``core.editor``, ``diff.external``,
``core.sshCommand``, ``core.askPass``, ``credential.helper``, ``protocol.allow``/``protocol.ext.allow`` (never),
``core.untrackedCache`` (false), ``log.showSignature`` (false; no gpg), ``gc.auto``/``maintenance.auto`` (off),
``color.ui``, ``core.quotePath``.  The ``-c`` options are inherited by git's own child processes (submodule
status etc.).  Patch-producing commands (diff, log, show, blame) additionally get ``--no-ext-diff`` and
``--no-textconv``, and callers cannot re-enable them (``--ext-diff``/``--textconv`` are refused).  Clean/smudge/
process FILTERS are run by git itself during ``status``/``diff`` (to compare working-tree content); filters
defined in the repository's own config (local/worktree/command scope) are therefore enumerated with
``git config --show-scope`` and emptied with ``-c filter.<name>.clean=`` etc. for commands that read the work
tree.  Aliases are never invoked: only builtin subcommands from the allowlist
(``ALLOWED_SUBCOMMANDS``) are accepted, and the mutating/dangerous ones are restricted to one exact form each
(``branch <name> <sha>``, ``stash list``, ``worktree list``, ``reflog show``, read-only ``config``, ``symbolic-ref
<name>``).  Options that write files or run programs (``--output``, ``--open-files-in-pager``/``grep -O``,
``--exec-path``, ``--git-dir=``, ``--work-tree=``, ``--lost-found``) are refused anywhere before ``--``.

What is NOT suppressed (be honest about it)
-------------------------------------------
* ``include.path`` / ``includeIf`` in any config file are still processed (they can only add the same
  suppressed keys, but they can alter other settings);
* attributes: ``.gitattributes`` drivers other than textconv/external diff/filter (e.g. ``diff=<driver>``
  funcname/word-regex settings, ``merge`` drivers - we never merge) are honoured; the global
  attributes file and ``info/attributes`` are read;
* filters defined in the USER's global/system config (e.g. git-lfs) keep working - they are trusted like the
  user's shell; only repository-scoped filter definitions are emptied (so an LFS repository configured with
  ``git lfs install --local`` may show its LFS files as modified in status/diff output);
* ``safe.directory`` / dubious-ownership checks are git's own and are not touched; a ``-c`` can't bypass them;
* replace refs/grafts (``refs/replace``), ``core.excludesFile``, ``core.worktree``/``core.bare`` of the
  repository itself, ``extensions.*``, submodule ``.git`` files and ``gitdir:`` pointers are used as git uses them;
* ``PATH`` is trusted (the ``git`` binary is found through it) and ``HOME``-relative config is trusted;
* a hostile repository can still make git CPU/IO heavy (huge packs, pathological history): the timeout and the
  output caps bound the damage, they do not prevent it.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from . import revisions
from .paths import check_pathspec
from .revisions import Revision, RevisionError

PathLike = Union[str, os.PathLike]

DEFAULT_TIMEOUT = 15
MAX_OUTPUT = 32 * 1024 * 1024      # stdout cap (bytes)
MAX_STDERR = 1 * 1024 * 1024       # stderr cap (bytes)
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


class GitRefused(GitError):
    """The command was refused before execution (not allowlisted, or carries a forbidden option)."""


@dataclass(frozen=True)
class Result:
    args: tuple
    returncode: int
    stdout: str
    stderr: str
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return self.stdout.strip()

    @property
    def lines(self) -> list:
        return [ln for ln in self.stdout.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- environment

# variables a caller's environment must not hand to git (non-GIT_ ones; every GIT_* is dropped by default)
_DROP_ENV = frozenset({
    "PAGER", "MANPAGER", "LESS", "LESSOPEN", "LESSCLOSE", "EDITOR", "VISUAL", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
    "LANG", "LANGUAGE", "LD_PRELOAD", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
})
_SAFE_GIT_ENV = {
    "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "GIT_EDITOR": "true",
    "GIT_ATTR_NOSYSTEM": "1", "LC_ALL": "C", "PAGER": "cat",
}


def scrubbed_env(base: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
    """Environment for a git child: ordinary variables kept, every ``GIT_*`` dropped, a small safe set added."""
    src = os.environ if base is None else base
    env = {}
    for k, v in src.items():
        if k in _DROP_ENV or k.startswith("LC_") or k.startswith("GIT_"):
            continue
        env[k] = v
    # inert config-redirection forms: they can only remove configuration
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        if src.get(k) == os.devnull:
            env[k] = os.devnull
    if str(src.get("GIT_CONFIG_NOSYSTEM", "")).lower() in ("1", "true", "yes", "on"):
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    env.update(_SAFE_GIT_ENV)
    for k, v in (extra or {}).items():
        if not str(k).startswith("GIT_") and str(k) not in _DROP_ENV and not str(k).startswith("LC_"):
            env[str(k)] = str(v)
    return env


# --------------------------------------------------------------------------- command policy

_HARDENING = (
    ("core.fsmonitor", "false"), ("core.hooksPath", os.devnull), ("core.pager", "cat"), ("core.editor", "true"),
    ("core.untrackedCache", "false"), ("core.quotePath", "false"), ("core.sshCommand", ""), ("core.askPass", ""),
    ("diff.external", ""), ("credential.helper", ""), ("protocol.allow", "never"), ("protocol.ext.allow", "never"),
    ("log.showSignature", "false"), ("gc.auto", "0"), ("maintenance.auto", "false"), ("color.ui", "false"),
    ("advice.detachedHead", "false"), ("status.submoduleSummary", "false"),
)
_CALLER_C_ALLOWED = frozenset({"core.quotepath"})
_CALLER_GLOBAL_FLAGS = frozenset({
    "--literal-pathspecs", "--no-literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
    "--no-pager", "--no-optional-locks", "--no-replace-objects",
})

#: Builtin subcommands Git Warp may run.  Everything else (aliases, push, commit, checkout, reset, clean, ...) is refused.
ALLOWED_SUBCOMMANDS = frozenset({
    "rev-parse", "rev-list", "log", "show", "diff", "diff-tree", "diff-index", "diff-files", "status", "ls-files",
    "ls-tree", "cat-file", "merge-base", "for-each-ref", "symbolic-ref", "reflog", "blame", "grep", "fsck",
    "config", "worktree", "stash", "check-ref-format", "branch",
})
_PATCH_CMDS = frozenset({"diff", "log", "show"})          # get --no-ext-diff --no-textconv
_FILTER_CMDS = frozenset({"status", "diff", "diff-files", "diff-index", "ls-files", "blame", "grep", "stash"})
_FORBIDDEN_NAMES = frozenset({"--open-files-in-pager", "--exec-path", "--lost-found", "--ext-diff", "--textconv",
                              "--upload-pack", "--receive-pack", "--config-env"})
_FORBIDDEN_WITH_VALUE = frozenset({"--git-dir", "--work-tree"})   # `rev-parse --git-dir` (a query) stays legal
_CONFIG_READ = ("--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch")
_CONFIG_WRITE = ("--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e", "--rename-section", "--remove-section",
                 "--set", "--file", "-f", "--global", "--system", "--local", "--worktree")
_FILTER_KEY = re.compile(r"^filter\.[^\n]+\.(clean|smudge|process)$", re.I)


def _refuse(msg: str, args: Sequence[str]) -> GitRefused:
    return GitRefused(msg, list(args), 129, "")


def _split_command(args: Sequence[str]) -> tuple:
    """-> (caller_c_pairs, caller_global_flags, subcommand, rest).  Refuses anything outside policy."""
    i, c_pairs, flags = 0, [], []
    n = len(args)
    while i < n and args[i].startswith("-"):
        a = args[i]
        if a == "-c":
            if i + 1 >= n:
                raise _refuse("git -c needs a key=value", args)
            kv = args[i + 1]
            key = kv.split("=", 1)[0].lower()
            if key not in _CALLER_C_ALLOWED:
                raise _refuse(f"config override not allowed: {key}", args)
            c_pairs.append(kv)
            i += 2
        elif a in _CALLER_GLOBAL_FLAGS:
            flags.append(a)
            i += 1
        else:
            raise _refuse(f"global git option not allowed: {a}", args)
    if i >= n:
        raise _refuse("no git subcommand given", args)
    return c_pairs, flags, args[i], list(args[i + 1:])


def _positionals(rest: Sequence[str]) -> list:
    return [a for a in rest if not a.startswith("-")]


def _check_subcommand(sub: str, rest: list, args: Sequence[str]) -> None:
    if sub not in ALLOWED_SUBCOMMANDS:
        raise _refuse(f"git subcommand not allowed: {sub}", args)
    skip = False
    for tok in rest:
        if tok in ("--", "--end-of-options"):
            break
        if skip:
            skip = False
            continue
        if sub == "grep" and tok == "-e":
            skip = True
            continue
        name, eq, _ = tok.partition("=")
        if name.startswith("--out") or name in _FORBIDDEN_NAMES or (eq and name in _FORBIDDEN_WITH_VALUE):
            raise _refuse(f"option not allowed: {name}", args)
        if sub == "grep" and tok.startswith("-O"):
            raise _refuse("option not allowed: -O", args)
    if sub == "branch":
        if len(rest) != 2 or any(a.startswith("-") for a in rest):
            raise _refuse("only `branch <name> <commit>` is allowed", args)
    elif sub == "stash":
        if rest[:1] != ["list"]:
            raise _refuse("only `stash list` is allowed", args)
    elif sub == "worktree":
        if rest[:1] != ["list"]:
            raise _refuse("only `worktree list` is allowed", args)
    elif sub == "reflog":
        if rest[:1] != ["show"]:
            raise _refuse("only `reflog show` is allowed", args)
    elif sub == "symbolic-ref":
        pos = _positionals(rest)
        if len(pos) != 1 or "-d" in rest or "--delete" in rest or "-m" in rest:
            raise _refuse("symbolic-ref is allowed for reading one ref only", args)
    elif sub == "config":
        if not any(t in _CONFIG_READ for t in rest) or any(t in _CONFIG_WRITE for t in rest):
            raise _refuse("config is allowed for read-only queries only", args)


def _filter_overrides(cwd, env: dict) -> list:
    """``-c filter.<n>.clean=`` etc. for filters defined by the repository's own config (not user/system scope).

    Without ``--show-scope`` support (git < 2.26) every filter is emptied (safe, possibly noisy).
    """
    base = ["git", "--no-pager", "--no-optional-locks", "config"]
    pattern = r"^filter\..*\.(clean|smudge|process)$"
    scoped = True
    try:
        rc, out, _, _ = _exec([*base, "--show-scope", "--null", "--get-regexp", pattern], cwd, env, 10, None, 1 << 20, 1 << 16)
        if rc not in (0, 1):
            scoped = False
            rc, out, _, _ = _exec([*base, "--null", "--get-regexp", pattern], cwd, env, 10, None, 1 << 20, 1 << 16)
    except (GitError, OSError, subprocess.TimeoutExpired):
        return []
    if rc not in (0, 1):
        return []
    keys, seen = [], set()
    for rec in out.split("\x00"):
        if scoped:
            scope, _, rec = rec.partition("\t")
            if scope.strip() in ("system", "global"):
                continue
        key = rec.split("\n", 1)[0]
        if not _FILTER_KEY.match(key) or key.lower() in seen:
            continue
        seen.add(key.lower())
        keys.append(key)
    return [x for k in keys for x in ("-c", f"{k}=")]


# --------------------------------------------------------------------------- process execution

def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _exec(argv: list, cwd, env: dict, timeout: float, input_bytes: Optional[bytes], max_out: int, max_err: int) -> tuple:
    """Run ``argv`` -> ``(returncode, stdout_text, stderr_text, truncated)``.  Raises on timeout / OS errors."""
    popen_kw = dict(cwd=cwd, env=env, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, shell=False)
    if hasattr(os, "setsid"):
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(argv, **popen_kw)
    bufs = {"out": bytearray(), "err": bytearray()}
    flags = {"truncated": False}

    def reader(stream, key: str, cap: int) -> None:
        buf = bufs[key]
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    return
                room = cap - len(buf)
                if len(chunk) > room:
                    buf += chunk[:max(room, 0)]
                    flags["truncated"] = True
                    _kill_group(proc)
                    return
                buf += chunk
        except (OSError, ValueError):
            return

    def writer() -> None:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=reader, args=(proc.stdout, "out", max_out), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, "err", max_err), daemon=True)]
    if input_bytes is not None:
        threads.append(threading.Thread(target=writer, daemon=True))
    for t in threads:
        t.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.wait()
        for t in threads:
            t.join(2)
        raise
    finally:
        for t in threads:
            t.join(1.0)
        if any(t.is_alive() for t in threads):
            _kill_group(proc)        # lingering descendants keep the pipes open: kill the group
            for t in threads:
                t.join(1.0)
        for s in (proc.stdout, proc.stderr, proc.stdin):
            try:
                if s:
                    s.close()
            except (OSError, ValueError):
                pass
    rc = proc.returncode
    if flags["truncated"] and rc is not None and rc < 0:
        rc = 0     # we ended it ourselves; the captured prefix is the result
    return (rc, bufs["out"].decode("utf-8", "replace"), bufs["err"].decode("utf-8", "replace"), flags["truncated"])


def run(
    args: Sequence[str],
    cwd: Optional[PathLike] = None,
    timeout: float = DEFAULT_TIMEOUT,
    check: bool = False,
    input: Optional[str] = None,
    env: Optional[dict] = None,
    max_output: int = MAX_OUTPUT,
    max_stderr: int = MAX_STDERR,
) -> Result:
    """Run ``git <args>`` under the policy described in the module docstring and return a :class:`Result`.

    ``check=True`` raises :class:`GitError` on a non-zero exit.  Timeouts and a missing git binary always
    raise.  Never uses a shell.  ``env`` may add ordinary variables; ``GIT_*`` entries in it are ignored.
    """
    args = [str(a) for a in args]
    work_dir = str(cwd) if cwd is not None else os.getcwd()
    environ = scrubbed_env(extra=env)
    if args == ["--version"]:
        argv = ["git", "--version"]
    else:
        c_pairs, flags, sub, rest = _split_command(args)
        _check_subcommand(sub, rest, args)
        argv = ["git", "--no-pager", "--no-optional-locks"]
        for k, v in _HARDENING:
            argv += ["-c", f"{k}={v}"]
        for kv in c_pairs:
            argv += ["-c", kv]
        argv += [f for f in flags if f not in ("--no-pager", "--no-optional-locks")]
        if sub in _FILTER_CMDS:
            argv += _filter_overrides(work_dir, environ)
        argv.append(sub)
        if sub in _PATCH_CMDS:
            argv += ["--no-ext-diff", "--no-textconv"]
        elif sub == "blame":
            argv.append("--no-textconv")
        argv += rest
    shown = args[:]
    try:
        rc, out, err, truncated = _exec(argv, work_dir, environ, timeout, input.encode("utf-8", "replace") if input is not None else None,
                                        max_output, max_stderr)
    except FileNotFoundError as e:
        if not Path(work_dir).is_dir():
            raise NotARepository(f"directory does not exist: {work_dir}", shown, 128, str(e)) from e
        raise GitNotFound("git executable not found", shown, 127, str(e)) from e
    except subprocess.TimeoutExpired as e:
        raise GitTimeout(f"git {' '.join(shown[:3])} timed out after {timeout}s", shown, 124) from e
    except NotADirectoryError as e:
        raise NotARepository(str(e), shown, 128, str(e)) from e
    except (OSError, ValueError) as e:
        raise GitError(str(e), shown, 1, str(e)) from e
    res = Result(tuple(shown), rc, out, err, truncated)
    if check and rc != 0:
        raise GitError(f"git {' '.join(shown[:3])} failed: {err.strip()[:300]}", shown, rc, err)
    return res


# --------------------------------------------------------------------------- revision plumbing

_SHAPE_REASONS = revisions.SYNTAX_REASONS


def rev_arg(value: Union[str, Revision], cwd: Optional[PathLike] = None) -> str:
    """Full object id for a revision argument (see :mod:`gitwarp.core.revisions`).

    The literal ``HEAD`` and an already-normalized full object id pass through without a Git round-trip;
    everything else is resolved.  Raises :class:`RevisionError` (a ``ValueError``).
    """
    if isinstance(value, str) and value == "HEAD":
        return "HEAD"
    return revisions.sha_of(value, cwd)


def _rev_item(item: Union[str, Revision, revisions.RevisionRange], cwd) -> str:
    if isinstance(item, revisions.RevisionRange):
        return item.arg
    if isinstance(item, str) and item.startswith("^") and not item.startswith("^{"):
        return "^" + rev_arg(item[1:], cwd)
    if isinstance(item, str) and ".." in item:
        return revisions.resolve_range(item, cwd).arg
    return rev_arg(item, cwd)


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


def rev_parse(ref: Union[str, Revision], cwd: Optional[PathLike] = None) -> Optional[str]:
    """Full commit id for ``ref`` or None when it does not resolve.

    Unsafe input (option-shaped, control characters, ...) raises :class:`RevisionError` (a ``ValueError``).
    """
    try:
        return revisions.resolve(ref, cwd).sha
    except RevisionError as e:
        if e.reason in _SHAPE_REASONS:
            raise
        return None


def upstream(cwd: Optional[PathLike] = None) -> Optional[str]:
    r = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=cwd)
    return r.text if r.ok and r.text else None


def ahead_behind(cwd: Optional[PathLike] = None, upstream_ref: Optional[str] = None) -> Optional[tuple]:
    """(ahead, behind) of HEAD relative to upstream, or None when there is none."""
    up = upstream_ref or upstream(cwd)
    if not up:
        return None
    try:
        up_sha = revisions.resolve(up, cwd).sha
    except RevisionError:
        return None
    r = run(["rev-list", "--left-right", "--count", f"HEAD...{up_sha}"], cwd=cwd)
    if not r.ok:
        return None
    try:
        a, b = r.text.split()
        return int(a), int(b)
    except ValueError:
        return None


def merge_base(a: Union[str, Revision], b: Union[str, Revision], cwd: Optional[PathLike] = None) -> Optional[str]:
    r = run(["merge-base", "--end-of-options", rev_arg(a, cwd), rev_arg(b, cwd)], cwd=cwd)
    return r.text if r.ok and r.text else None


def is_ancestor(a: Union[str, Revision], b: Union[str, Revision], cwd: Optional[PathLike] = None) -> Optional[bool]:
    """True/False, or None when it cannot be determined (missing objects, shallow history)."""
    r = run(["merge-base", "--is-ancestor", "--end-of-options", rev_arg(a, cwd), rev_arg(b, cwd)], cwd=cwd)
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None


def rev_list(include: Sequence[Union[str, Revision]] = ("HEAD",), exclude: Sequence[Union[str, Revision]] = (),
             max_count: Optional[int] = None, extra: Sequence[str] = (), cwd: Optional[PathLike] = None,
             timeout: float = 30) -> Optional[list]:
    """Commit ids reachable from ``include`` and not from ``exclude`` (each normalized); None on failure."""
    revs = [rev_arg(x, cwd) for x in include] + ["^" + rev_arg(x, cwd) for x in exclude]
    args = ["rev-list", *([f"--max-count={int(max_count)}"] if max_count is not None else []), *extra, "--end-of-options", *revs]
    r = run(args, cwd=cwd, timeout=timeout)
    return r.lines if r.ok else None


def count_commits(include: Sequence[Union[str, Revision]] = ("HEAD",), exclude: Sequence[Union[str, Revision]] = (),
                  cwd: Optional[PathLike] = None, timeout: float = 30, extra: Sequence[str] = ()) -> Optional[int]:
    revs = [rev_arg(x, cwd) for x in include] + ["^" + rev_arg(x, cwd) for x in exclude]
    r = run(["rev-list", "--count", *extra, "--end-of-options", *revs], cwd=cwd, timeout=timeout)
    return int(r.text) if r.ok and r.text.isdigit() else None


def left_right_count(a: Union[str, Revision], b: Union[str, Revision], cwd: Optional[PathLike] = None) -> Optional[tuple]:
    """``(only_in_a, only_in_b)`` for ``a...b`` (both endpoints normalized separately)."""
    rng = f"{revisions.sha_of(a, cwd)}...{revisions.sha_of(b, cwd)}"
    r = run(["rev-list", "--left-right", "--count", rng], cwd=cwd)
    parts = r.text.split()
    return (int(parts[0]), int(parts[1])) if r.ok and len(parts) == 2 and all(x.isdigit() for x in parts) else None


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
        rng = f"{revisions.sha_of(base, cwd)}...{revisions.sha_of(head, cwd)}"
        r = run(["diff", "--name-only", "-z", rng, "--"], cwd=cwd, timeout=30)
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
    r = run(["reflog", "show", f"-n{int(limit)}", "--format=%H" + _FS + "%gd" + _FS + "%gs" + _FS + "%cI", "--end-of-options",
             revisions.check_refname(ref)], cwd=cwd, timeout=30)
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
    items = [rev_range] if isinstance(rev_range, (str, Revision, revisions.RevisionRange)) else list(rev_range or ["HEAD"])
    try:
        revs = [_rev_item(x, cwd) for x in items]
    except RevisionError as e:
        if e.reason in _SHAPE_REASONS:
            raise
        return []
    fmt = _COMMIT_FMT + ("%x00" if with_files else "")
    args = ["log", f"-n{int(limit)}", "--format=" + fmt]
    if with_files:
        args += ["--name-only", "-z"]
    if paths:
        args.insert(0, "--literal-pathspecs")
    args += list(extra) + ["--end-of-options", *revs]
    if paths:
        args += ["--", *[check_pathspec(p) for p in paths]]
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
    r = run([*args, "--end-of-options", rev_arg(rev, cwd)], cwd=cwd, timeout=60)
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
    # no --end-of-options: blame's own parser does not handle it; the rev here is a verified full object id
    args += [revisions.sha_of(rev, cwd), "--", check_pathspec(path)]
    r = run(["--literal-pathspecs", *args], cwd=cwd, timeout=60)
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
    r = run(["ls-tree", "-r", "-z", "--name-only", "--end-of-options", rev_arg(rev, cwd)], cwd=cwd, timeout=60)
    return [p for p in r.stdout.split("\x00") if p] if r.ok else []


def show_file(rev: str, path: str, cwd: Optional[PathLike] = None, max_bytes: int = 1_000_000) -> Optional[str]:
    """Contents of ``path`` at ``rev`` (or ``:N`` index stage via rev like ':2')."""
    if isinstance(rev, str) and re.fullmatch(r":[0-3]", rev):
        spec = f"{rev}:{check_pathspec(path)}"              # index stage (conflict sides)
    else:
        spec = f"{rev_arg(rev, cwd) if rev != 'HEAD' else 'HEAD'}:{check_pathspec(path)}"
    r = run(["show", "--end-of-options", spec], cwd=cwd, timeout=30)
    return r.stdout[:max_bytes] if r.ok else None
