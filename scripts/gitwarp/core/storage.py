"""Secure private-state storage: the single abstraction for everything Git Warp writes under ``.git/git-warp``.

Rules enforced for every writer (recorder, state.json, warp.db, rotation, quarantine, temp files):

* the state directory is ``0700`` and every file in it ``0600``; looser modes left by older versions are tightened on use;
* symlinks are never followed and only regular files are ever opened (FIFOs, sockets, devices and directories are
  refused with :class:`UnsafeStorage`, never deleted, never replaced, never blocked on);
* descriptor-based access (``O_NOFOLLOW | O_NONBLOCK`` + ``fstat`` + ``dir_fd``) narrows check/use races;
* replace-style writes go through a same-directory ``0600`` ``O_EXCL`` temp file, ``fsync`` and ``os.replace``;
* appends are one ``write`` of one whole line on an ``O_APPEND`` descriptor under an advisory lock.

:class:`UnsafeStorage` subclasses :class:`OSError`, so callers that already degrade on ``OSError`` stay silent.
Residual risk (documented): SQLite itself opens ``warp.db`` by path, so a same-user attacker winning a race between
:func:`prepare_file` and ``sqlite3.connect`` is not excluded; the 0700 directory limits that to the same account.
"""
from __future__ import annotations

import os
import secrets
import stat
import time
from pathlib import Path
from typing import Callable, Optional

try:  # POSIX advisory locking
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

STATE_DIRNAME = "git-warp"
DIR_MODE = 0o700
FILE_MODE = 0o600
MAX_LINE_BYTES = 256 * 1024
QUARANTINE_KEEP = 3                      # generations: <name>.corrupt, .corrupt.1, .corrupt.2
SIDECARS = ("-wal", "-shm", "-journal")

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_HAS_DIRFD = os.open in getattr(os, "supports_dir_fd", set())
_SWEPT: set = set()                      # directories already swept for loose modes in this process


class UnsafeStorage(OSError):
    """The storage target is a symlink / FIFO / socket / device / unexpected directory (operation refused)."""


# --------------------------------------------------------------------------- directory

def state_dir(common_git_dir: Path, create: bool = False) -> Path:
    """``<common git dir>/git-warp``.  With ``create`` the directory is made (0700) and verified."""
    d = Path(common_git_dir) / STATE_DIRNAME
    if create:
        ensure_dir(d)
    return d


def _describe(mode: int) -> str:
    for test, label in ((stat.S_ISLNK, "symlink"), (stat.S_ISFIFO, "FIFO"), (stat.S_ISSOCK, "socket"), (stat.S_ISDIR, "directory"),
                        (stat.S_ISCHR, "character device"), (stat.S_ISBLK, "block device"), (stat.S_ISREG, "regular file")):
        if test(mode):
            return label
    return "unknown object"


def ensure_dir(path) -> Path:
    """Create (0700) or verify the state directory; refuse symlinks/non-directories; tighten loose modes."""
    path = Path(path)
    created = False
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        try:
            os.mkdir(path, DIR_MODE)
            created = True
        except FileExistsError:
            pass
        st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafeStorage(f"{path} is a {_describe(st.st_mode)}, not a directory; refusing to use it")
    fd = _open_dir(path)
    try:
        if created:
            os.fchmod(fd, DIR_MODE)          # umask-proof for a directory we just made
        _tighten_fd(fd, DIR_MODE)
        key = str(path)
        if key not in _SWEPT:
            _SWEPT.add(key)
            _sweep(fd)
    finally:
        os.close(fd)
    return path


def _open_dir(path) -> int:
    try:
        fd = os.open(str(path), os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise UnsafeStorage(f"cannot safely open state directory {path}: {e.strerror or e}") from e
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise UnsafeStorage(f"{path} is not a directory")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _tighten_fd(fd: int, mode: int) -> None:
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() and os.geteuid() != 0:
            raise UnsafeStorage("state object is owned by another user; refusing to use it")
        cur = stat.S_IMODE(st.st_mode)
        if cur & ~mode:                      # only ever removes bits: a deliberately stricter mode (e.g. 0500) is kept
            os.fchmod(fd, cur & mode)
    except AttributeError:  # pragma: no cover - no fchmod/geteuid (non-POSIX)
        pass


def _sweep(dfd: int) -> None:
    """Tighten every regular file already in the directory (upgrade path from 0644 files)."""
    try:
        names = os.listdir(dfd) if _HAS_DIRFD else []
    except OSError:
        return
    for name in names:
        try:
            st = _lstat(dfd, name)
            if st is not None and stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o077:
                fd = os.open(name, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, dir_fd=dfd)
                try:
                    if stat.S_ISREG(os.fstat(fd).st_mode):
                        os.fchmod(fd, stat.S_IMODE(st.st_mode) & FILE_MODE)
                finally:
                    os.close(fd)
        except OSError:
            continue


class _Dir:
    """An open, verified state directory descriptor (context manager)."""

    def __init__(self, path, create: bool):
        self.path = Path(path)
        if create:
            ensure_dir(self.path)
        self.fd = _open_dir(self.path)
        if not create:
            try:
                _tighten_fd(self.fd, DIR_MODE)
            except UnsafeStorage:
                os.close(self.fd)
                raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        except OSError:
            pass


def _lstat(dfd: int, name: str):
    try:
        return os.stat(name, dir_fd=dfd, follow_symlinks=False) if _HAS_DIRFD else os.lstat(name)
    except FileNotFoundError:
        return None


def _check_name(name: str) -> str:
    if not name or "/" in name or name in (".", ".."):
        raise UnsafeStorage(f"invalid state file name: {name!r}")
    return name


def _require_regular_or_absent(d: _Dir, name: str):
    st = _lstat(d.fd, name)
    if st is not None and not stat.S_ISREG(st.st_mode):
        raise UnsafeStorage(f"{d.path / name} is a {_describe(st.st_mode)}, not a regular file; refusing to touch it")
    return st


def _open_file(d: _Dir, name: str, flags: int, create: bool = False) -> int:
    """Open a *regular* file privately: no symlink following, no blocking, fstat-verified, mode tightened."""
    _check_name(name)
    if not _HAS_DIRFD or not _O_NOFOLLOW:  # degrade: lstat check then plain open
        _require_regular_or_absent(d, name)
    try:
        for attempt in range(8):
            try:
                fd = os.open(name if _HAS_DIRFD else str(d.path / name), flags | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC | (os.O_CREAT if create else 0),
                             FILE_MODE, **({"dir_fd": d.fd} if _HAS_DIRFD else {}))
                break
            except FileNotFoundError:
                if not create or attempt == 7:      # O_CREAT can transiently report ENOENT while another process creates the file (macOS)
                    raise
                time.sleep(0.002 * (attempt + 1))
    except OSError as e:
        st = _lstat(d.fd, name)
        if st is not None and not stat.S_ISREG(st.st_mode):
            raise UnsafeStorage(f"{d.path / name} is a {_describe(st.st_mode)}, not a regular file; refusing to touch it") from e
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeStorage(f"{d.path / name} is a {_describe(st.st_mode)}, not a regular file; refusing to touch it")
        _tighten_fd(fd, FILE_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


# --------------------------------------------------------------------------- reads

def read_bytes(directory, name: str, max_bytes: int = 64 * 1024 * 1024) -> Optional[bytes]:
    """Contents of a state file, ``None`` when absent.  Raises :class:`UnsafeStorage` for unsafe targets."""
    try:
        with _Dir(directory, create=False) as d:
            fd = _open_file(d, name, os.O_RDONLY)
            try:
                chunks, total = [], 0
                while True:
                    b = os.read(fd, 1 << 20)
                    if not b:
                        break
                    total += len(b)
                    if total > max_bytes:
                        raise UnsafeStorage(f"{name} exceeds {max_bytes} bytes")
                    chunks.append(b)
                return b"".join(chunks)
            finally:
                os.close(fd)
    except FileNotFoundError:
        return None


def regular_file(directory, name: str) -> Optional[Path]:
    """Path of an existing regular state file (tightened to 0600), ``None`` if absent; refuses unsafe targets."""
    try:
        with _Dir(directory, create=False) as d:
            fd = _open_file(d, name, os.O_RDONLY)
            os.close(fd)
            return d.path / name
    except FileNotFoundError:
        return None


# --------------------------------------------------------------------------- writes

def write_atomic(directory, name: str, data: bytes) -> None:
    """Replace ``name`` atomically via a private same-directory temp file.  Never replaces a non-regular target."""
    with _Dir(directory, create=True) as d:
        _check_name(name)
        _require_regular_or_absent(d, name)
        tmp = f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        fd = _open_excl(d, tmp)
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            finally:
                os.close(fd)
            _require_regular_or_absent(d, name)       # re-check immediately before the swap (narrows the race)
            if _HAS_DIRFD:
                os.replace(tmp, name, src_dir_fd=d.fd, dst_dir_fd=d.fd)
            else:  # pragma: no cover
                os.replace(str(d.path / tmp), str(d.path / name))
        except BaseException:
            _unlink_quiet(d, tmp)
            raise


def _open_excl(d: _Dir, name: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    if _HAS_DIRFD:
        fd = os.open(name, flags, FILE_MODE, dir_fd=d.fd)
    else:  # pragma: no cover
        fd = os.open(str(d.path / name), flags, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)       # umask-proof
    except (AttributeError, OSError):
        pass
    return fd


def _unlink_quiet(d: _Dir, name: str) -> None:
    try:
        if _HAS_DIRFD:
            os.unlink(name, dir_fd=d.fd)
        else:  # pragma: no cover
            os.unlink(str(d.path / name))
    except OSError:
        pass


class LockTimeout(OSError):
    """The advisory lock could not be taken in time: the operation is refused (never performed unlocked)."""


def _lock(fd: int, timeout: Optional[float] = None) -> bool:
    if fcntl is None:
        return True
    deadline = time.monotonic() + (LOCK_WAIT if timeout is None else timeout)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)


# Drop semantics: every mutator of a state file (recorder append/rotation/compaction, state.json update, index creation)
# must HOLD its lock.  If the lock cannot be taken within LOCK_WAIT seconds the operation is refused with LockTimeout
# and NOT performed: a flight-recorder record is dropped explicitly (never appended into a file that a concurrent
# compaction is about to replace), a state.json update is skipped, a compaction is skipped until its next run.
# Nothing already stored is lost or modified by a refused operation.
LOCK_WAIT = 5.0


class _Locked:
    """Lock + data descriptors for an append-style file.

    Locking design: ALL mutators of ``name`` (append, rotation, compaction/rewrite) serialise on a separate, stable
    lock file ``<name>.lock`` (never replaced, so its inode never changes).  The lock is held across the whole
    operation, including the ``os.replace`` that swaps the data file's inode, so an append can never land between
    a compaction snapshot and its replacement, and nobody holds a lock on a replaced inode.
    """

    def __init__(self, d: _Dir, name: str):
        self.lfd = _open_file(d, name + ".lock", os.O_RDWR, create=True)
        if not _lock(self.lfd):
            os.close(self.lfd)
            raise LockTimeout(f"could not lock {name} within {LOCK_WAIT}s; operation refused")
        try:
            self.fd = _open_file(d, name, os.O_RDWR | os.O_APPEND, create=True)
        except BaseException:
            os.close(self.lfd)
            raise
        self.d, self.name = d, name

    def reopen(self) -> None:
        os.close(self.fd)
        self.fd = _open_file(self.d, self.name, os.O_RDWR | os.O_APPEND, create=True)

    def close(self) -> None:
        for fd in (self.fd, self.lfd):
            try:
                os.close(fd)
            except OSError:
                pass


def append_line(directory, name: str, line: bytes, max_bytes: int, rotate_suffix: str = ".1") -> None:
    """Append one whole line (single ``write`` on an O_APPEND fd) under the stable lock.  Rotates to ``name + rotate_suffix`` past ``max_bytes``."""
    if len(line) > MAX_LINE_BYTES:
        raise ValueError("line too large")
    with _Dir(directory, create=True) as d:
        old = name + rotate_suffix
        h = _Locked(d, name)
        try:
            if os.fstat(h.fd).st_size + len(line) > max_bytes:
                _require_regular_or_absent(d, old)       # never rotate over an unexpected object
                _move(d, name, old)
                h.reopen()
            size = os.fstat(h.fd).st_size
            if size and os.pread(h.fd, 1, size - 1) != b"\n":
                line = b"\n" + line      # a crash left a torn last line: keep the fragment isolated, keep this record whole
            n = os.write(h.fd, line)
            if n != len(line):  # pragma: no cover - O_APPEND on a regular file writes whole
                raise OSError("short write")
        finally:
            h.close()


def rewrite_locked(directory, name: str, transform: Callable[[bytes], Optional[bytes]]) -> None:
    """Under the append lock: read ``name``, pass it to ``transform`` and atomically replace it with the result (None = keep).

    The lock is held until after the replace, so concurrent appends wait and then land in the new file.
    """
    with _Dir(directory, create=True) as d:
        h = _Locked(d, name)
        try:
            chunks = []
            rfd = _open_file(d, name, os.O_RDONLY)
            try:
                while True:
                    b = os.read(rfd, 1 << 20)
                    if not b:
                        break
                    chunks.append(b)
            finally:
                os.close(rfd)
            new = transform(b"".join(chunks))
            if new is not None:
                write_atomic(directory, name, new)
        finally:
            h.close()


def update_locked(directory, name: str, fn: Callable[[Optional[bytes]], bytes]) -> None:
    """Serialized read-modify-write of ``name`` (lock file ``<name>.lock``) with an atomic replace."""
    with _Dir(directory, create=True) as d:
        lfd = _open_file(d, name + ".lock", os.O_WRONLY, create=True)
        try:
            if not _lock(lfd):
                raise LockTimeout(f"could not lock {name} within {LOCK_WAIT}s; update refused")
            cur = read_bytes(directory, name)
            write_atomic(directory, name, fn(cur))
        finally:
            os.close(lfd)


class file_lock:
    """Exclusive advisory lock on ``<name>.lock`` in the state directory (bounded wait; raises LockTimeout, never proceeds unlocked)."""

    def __init__(self, directory, name: str, timeout: float = 30.0):
        self.directory, self.name, self.timeout, self.fd = directory, name + ".lock", timeout, None

    def __enter__(self):
        with _Dir(self.directory, create=True) as d:
            self.fd = _open_file(d, self.name, os.O_WRONLY, create=True)
        if not _lock(self.fd, self.timeout):
            os.close(self.fd)
            self.fd = None
            raise LockTimeout(f"could not lock {self.name} within {self.timeout}s")
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)            # releases the flock
        return False


def unlink_regular(directory, name: str) -> bool:
    """Delete a regular file; refuses anything else.  Returns False when absent."""
    with _Dir(directory, create=False) as d:
        if _require_regular_or_absent(d, _check_name(name)) is None:
            return False
        if _HAS_DIRFD:
            os.unlink(name, dir_fd=d.fd)
        else:  # pragma: no cover
            os.unlink(str(d.path / name))
        return True


# --------------------------------------------------------------------------- SQLite files

def prepare_file(directory, name: str) -> Path:
    """Create ``name`` privately (0600, O_EXCL) if absent, tighten if present, refuse unsafe targets and sidecars.

    Call this before every ``sqlite3.connect`` of a writable database.  SQLite creates its -wal/-shm/-journal with the
    database's own mode, so a 0600 database yields 0600 sidecars; existing sidecars are tightened here as well.
    """
    with _Dir(directory, create=True) as d:
        _check_name(name)
        _require_regular_or_absent(d, name)
        try:
            fd = _open_excl_create(d, name)
        except FileExistsError:
            fd = _open_file(d, name, os.O_RDONLY)
        os.close(fd)
        for suffix in SIDECARS:
            if _require_regular_or_absent(d, name + suffix) is not None:
                os.close(_open_file(d, name + suffix, os.O_RDONLY))
        return d.path / name


def _open_excl_create(d: _Dir, name: str) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    fd = os.open(name, flags, FILE_MODE, dir_fd=d.fd) if _HAS_DIRFD else os.open(str(d.path / name), flags, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
    except (AttributeError, OSError):
        pass
    return fd


def tighten_sidecars(directory, name: str) -> None:
    """Best-effort chmod 0600 of SQLite sidecars created since :func:`prepare_file`."""
    try:
        with _Dir(directory, create=False) as d:
            for suffix in ("",) + SIDECARS:
                try:
                    if _require_regular_or_absent(d, name + suffix) is not None:
                        os.close(_open_file(d, name + suffix, os.O_RDONLY))
                except FileNotFoundError:
                    continue
    except OSError:
        pass


# --------------------------------------------------------------------------- quarantine

def quarantine(directory, name: str, keep: int = QUARANTINE_KEEP) -> Optional[str]:
    """Move an unusable ``name`` (and its SQLite sidecars) aside as ``name.corrupt`` (private, ``keep`` generations).

    Previous generations rotate (``.corrupt`` -> ``.corrupt.1`` -> ...); only the generation beyond ``keep`` is dropped.
    Unsafe targets (symlink/FIFO/...) raise :class:`UnsafeStorage` and are left exactly as they are.
    Returns the new quarantine file name, or ``None`` when ``name`` did not exist.
    """
    with _Dir(directory, create=True) as d:
        _check_name(name)
        if _require_regular_or_absent(d, name) is None:
            return None
        for suffix in SIDECARS:
            _require_regular_or_absent(d, name + suffix)
        for base in (name,) + tuple(name + s for s in SIDECARS):
            if _lstat(d.fd, base) is None:
                continue
            _rotate_generations(d, base + ".corrupt", keep)
            _move(d, base, base + ".corrupt")
            try:
                os.close(_open_file(d, base + ".corrupt", os.O_RDONLY))  # tighten
            except OSError:
                pass
        return name + ".corrupt"


def _move(d: _Dir, src: str, dst: str) -> None:
    if _HAS_DIRFD:
        os.replace(src, dst, src_dir_fd=d.fd, dst_dir_fd=d.fd)
    else:  # pragma: no cover
        os.replace(str(d.path / src), str(d.path / dst))


def _rotate_generations(d: _Dir, base: str, keep: int) -> None:
    """Shift ``base``, ``base.1`` ... up by one so ``base`` is free; the generation that would exceed ``keep`` is removed."""
    names = [base] + [f"{base}.{i}" for i in range(1, keep)]
    for n in names:
        _require_regular_or_absent(d, n)
    last = names[-1]
    if _lstat(d.fd, last) is not None:
        _unlink_quiet(d, last)
    for i in range(len(names) - 2, -1, -1):
        if _lstat(d.fd, names[i]) is not None:
            _move(d, names[i], names[i + 1])
