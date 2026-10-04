"""Shared helpers for the acceptance / crash / scale suites.

* ``snapshot`` / ``diff`` -- repository mutation detection (HEAD, refs, index entries, tracked file hashes, untracked
  files, stash, worktrees, config, and the set/size of files under the git dir).  ``.git/git-warp`` (the plugin's own
  private state) and ``*.lock`` files are deliberately ignored; a pure stat-refresh of the index is *not* mutation.
* ``build_state`` -- the repository-state fixtures (21 states + recovery fixtures), ported as behavioural fixtures
  from the neutral Disposable harness (https://github.com/RajNori/Disposable, ``cases``/``fixtures``).
* ``run_warp`` / ``run_hook`` -- subprocess wrappers with hard timeouts.

Everything operates strictly inside the directory it is given (always a pytest ``tmp_path``).  No network.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
WARP = SCRIPTS / "warp.py"
REAL_GIT = shutil.which("git") or "/usr/bin/git"
GIT_STATE_DIRNAME = "git-warp"

_BASE_ENV = {
    "GIT_AUTHOR_NAME": "QA Author", "GIT_AUTHOR_EMAIL": "qa@example.invalid",
    "GIT_COMMITTER_NAME": "QA Committer", "GIT_COMMITTER_EMAIL": "qa@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
}


def clean_env(extra: Optional[dict] = None) -> dict:
    """Deterministic environment for fixture construction and tool runs.  ``None`` values delete a variable."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("GIT_") and k not in ("GIT_EXEC_PATH",):
            env.pop(k)
    env.update(_BASE_ENV)
    for k, v in (extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    return env


def git(repo, *args, check=True, input=None, env=None, timeout=120) -> subprocess.CompletedProcess:
    cp = subprocess.run([REAL_GIT, *map(str, args)], cwd=str(repo), env=clean_env(env), capture_output=True, text=True,
                        errors="replace", input=input, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError(f"git {' '.join(map(str, args))} failed in {repo}: {cp.stderr.strip()[:300]}")
    return cp


def git_out(repo, *args) -> str:
    return git(repo, *args).stdout.strip()


# --------------------------------------------------------------------------- mutation detection

def _sha_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _g(repo, *args) -> str:
    return git(repo, "--no-optional-locks", *args, check=False).stdout


def snapshot(repo) -> dict:
    """Capture everything a read-only operation must leave untouched."""
    repo = Path(repo)
    gitdir = Path(_g(repo, "rev-parse", "--absolute-git-dir").strip() or repo / ".git")
    common = Path(os.path.normpath(gitdir / (_g(repo, "rev-parse", "--git-common-dir").strip() or ".")))
    if not common.is_absolute():
        common = Path(os.path.normpath(repo / common))
    tracked = {}
    for rel in _g(repo, "ls-files", "-z").split("\0"):
        if not rel:
            continue
        p = repo / rel
        try:
            tracked[rel] = ("link:" + os.readlink(p)) if p.is_symlink() else _sha_file(p)
        except OSError:
            tracked[rel] = None
    status = _g(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    gitfiles = {}
    for base in {gitdir, common}:
        for root, dirs, files in os.walk(base):
            rel_root = os.path.relpath(root, base)
            if rel_root.split(os.sep)[0] == GIT_STATE_DIRNAME:
                dirs[:] = []
                continue
            for f in files:
                if f.endswith(".lock") or (rel_root == "." and f == "index"):
                    continue
                full = os.path.join(root, f)
                try:
                    gitfiles[os.path.normpath(os.path.join(str(base), rel_root, f))] = os.path.getsize(full)
                except OSError:
                    pass
    cfgs = {str(p): _sha_file(p) for p in (gitdir / "config", common / "config", gitdir / "config.worktree")}
    return {
        "head": _g(repo, "rev-parse", "-q", "--verify", "HEAD").strip(),
        "head_ref": _g(repo, "symbolic-ref", "-q", "HEAD").strip(),
        "refs": _g(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
        "status": status,
        "index_entries": _g(repo, "ls-files", "-s", "-z"),
        "tracked": tracked,
        "untracked": sorted(p[3:] for p in status.split("\0") if p.startswith("??")),
        "stash": _g(repo, "stash", "list"),
        "worktrees": _g(repo, "worktree", "list", "--porcelain"),
        "config": cfgs,
        "gitfiles": gitfiles,
    }


def diff(before: dict, after: dict) -> list:
    """Names of the aspects that changed between two snapshots (empty list = no mutation)."""
    out = [k for k in ("head", "head_ref", "refs", "status", "index_entries", "tracked", "untracked", "stash",
                       "worktrees", "config") if before[k] != after[k]]
    if before["gitfiles"] != after["gitfiles"]:
        b, a = before["gitfiles"], after["gitfiles"]
        detail = ([f"+{os.path.basename(x)}" for x in sorted(set(a) - set(b))]
                  + [f"-{os.path.basename(x)}" for x in sorted(set(b) - set(a))]
                  + [f"~{os.path.basename(x)}" for x in sorted(k for k in b if k in a and b[k] != a[k])])
        if detail:
            out.append("gitdir:" + ",".join(detail[:8]))
    return out


# --------------------------------------------------------------------------- tool runners

@dataclass
class Run:
    argv: list
    code: Optional[int]
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False
    max_rss_kb: Optional[int] = None

    @property
    def traceback(self) -> bool:
        return "Traceback (most recent call last)" in (self.stderr + self.stdout)

    def json(self):
        return json.loads(self.stdout)


def _run(argv, *, cwd, env=None, input=None, timeout=60) -> Run:
    t = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=str(cwd), env=env or clean_env(), input=input, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return Run(list(map(str, argv)), p.returncode, p.stdout, p.stderr, time.monotonic() - t)
    except subprocess.TimeoutExpired as e:
        def s(x):
            return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
        return Run(list(map(str, argv)), None, s(e.stdout), s(e.stderr), time.monotonic() - t, timed_out=True)


def run_warp(repo, *args, env=None, timeout=90, with_repo=True) -> Run:
    argv = [sys.executable, str(WARP), *args] + (["--repo", str(repo)] if with_repo else [])
    return _run(argv, cwd=repo, env=clean_env(env), timeout=timeout)


HOOKS = ("session_start", "git_guard", "post_tool", "stop")
HOOK_EVENT_NAMES = {"session_start": "SessionStart", "git_guard": "PreToolUse", "post_tool": "PostToolUse", "stop": "Stop"}


def hook_event(name: str, cwd, command: str = "git status", tool: str = "Bash", file_path: Optional[str] = None) -> dict:
    ev = {"session_id": "qa-session", "transcript_path": os.devnull, "cwd": str(cwd),
          "hook_event_name": HOOK_EVENT_NAMES[name]}
    if name == "session_start":
        ev["source"] = "startup"
    elif name == "stop":
        ev["stop_hook_active"] = False
    elif name == "git_guard":
        ev.update(tool_name="Bash", tool_input={"command": command})
    else:
        if tool == "Bash":
            ev.update(tool_name="Bash", tool_input={"command": command}, tool_response={"stdout": "", "exit_code": 0})
        else:
            ev.update(tool_name=tool, tool_input={"file_path": file_path or str(Path(cwd) / "README.md"),
                                                  "content": "x\n"}, tool_response={"success": True})
    return ev


def run_hook(name: str, cwd, event=None, raw=None, env=None, timeout=60) -> Run:
    payload = raw if raw is not None else json.dumps(event if event is not None else hook_event(name, cwd))
    return _run([sys.executable, str(SCRIPTS / f"hook_{name}.py")], cwd=cwd, env=clean_env(env), input=payload, timeout=timeout)


def guard_decision(command: str, cwd, env=None) -> tuple:
    """-> (decision, Run).  decision in {'defer', 'ask', 'deny', 'allow'}; ``None`` if the hook output was not valid."""
    r = run_hook("git_guard", cwd, event=hook_event("git_guard", cwd, command=command), env=env)
    if r.code != 0 or r.timed_out or r.traceback:
        return None, r
    try:
        data = json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        return None, r
    if not isinstance(data, dict):
        return None, r
    return (data.get("hookSpecificOutput") or {}).get("permissionDecision", "defer"), r


def hook_timeouts() -> dict:
    """script-name -> timeout (seconds) as declared in hooks/hooks.json."""
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    out = {}
    for groups in cfg["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                script = Path(h["command"].split("scripts/")[1].split('"')[0]).stem
                out[script.replace("hook_", "")] = h["timeout"]
    return out


# --------------------------------------------------------------------------- git shim (records every git invocation)

def install_git_shim(bin_dir: Path, log: Path) -> dict:
    """Create a ``git`` wrapper that appends its argv to ``log`` then execs the real git.  Returns an env override."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{log}"\nexec "{REAL_GIT}" "$@"\n')
    shim.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}


FORBIDDEN_GIT_VERBS = ("gc", "prune", "repack", "update-ref", "reset", "clean", "checkout", "switch", "restore", "tag",
                       "commit", "merge", "rebase", "cherry-pick", "revert", "add", "rm", "mv", "fetch", "pull", "push")


def forbidden_git_calls(log: Path) -> list:
    """Invocations recorded by the shim that mutate history / the object store / the worktree."""
    bad = []
    if not log.exists():
        return bad
    for line in log.read_text(errors="replace").splitlines():
        words = line.split()
        # skip leading global options such as -c k=v / -C dir / --no-optional-locks
        i = 0
        while i < len(words) and words[i].startswith("-"):
            i += 2 if words[i] in ("-c", "-C", "--git-dir", "--work-tree") else 1
        verb = words[i] if i < len(words) else ""
        rest = words[i + 1:]
        if verb in FORBIDDEN_GIT_VERBS:
            bad.append(line)
        elif verb == "reflog" and rest[:1] in (["expire"], ["delete"]):
            bad.append(line)
        elif verb == "stash" and rest[:1] in (["drop"], ["clear"], ["pop"], ["push"], ["save"], ["apply"], ["branch"]):
            bad.append(line)
        elif verb == "branch" and any(a in ("-d", "-D", "-m", "-M", "-f", "--delete", "--force") for a in rest):
            bad.append(line)
        elif verb == "worktree" and rest[:1] in (["prune"], ["remove"], ["add"], ["move"], ["repair"]):
            bad.append(line)
        elif verb == "fsck" and "--lost-found" in rest:
            bad.append(line)
    return bad


# --------------------------------------------------------------------------- fixtures

@dataclass
class Fixture:
    root: Path
    kind: str
    meta: dict = field(default_factory=dict)

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    @property
    def outside(self) -> Path:
        return self.root / "outside"

    @property
    def markers(self) -> Path:
        return self.root / "markers"

    @property
    def target(self) -> Path:
        """Directory the tools should be pointed at (a linked worktree for the ``worktree`` state)."""
        return Path(self.meta.get("worktree", self.repo))

    def write(self, rel: str, text: str = "") -> Path:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
        return p

    def g(self, *args, **kw):
        return git(self.repo, *args, **kw)

    def commit(self, msg: str, n: int = 0) -> str:
        stamp = f"{1_700_000_000 + n * 60} +0000"
        self.g("add", "-A")
        self.g("commit", "-q", "-m", msg, env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        return self.g("rev-parse", "HEAD").stdout.strip()

    def marker_script(self, name: str, passthrough: bool = False) -> Path:
        """Executable that appends one line to markers/<name>.log (outside the repo) and does nothing else."""
        self.markers.mkdir(parents=True, exist_ok=True)
        log = self.markers / f"{name}.log"
        body = f'#!/bin/sh\nprintf \'%s\\n\' "{name} $*" >> "{log}"\n' + ('cat "$1" 2>/dev/null\n' if passthrough else "") + "exit 0\n"
        s = self.markers / f"{name}.sh"
        s.write_text(body)
        s.chmod(0o755)
        return s

    def fired(self) -> set:
        return {p.stem for p in self.markers.glob("*.log") if p.stat().st_size > 0} if self.markers.exists() else set()


def new_fixture(root: Path, kind: str) -> Fixture:
    root = Path(root)
    (root / "repo").mkdir(parents=True)
    (root / "markers").mkdir()
    (root / "outside").mkdir()
    return Fixture(root, kind)


BUILDERS: dict = {}


def builder(name):
    def deco(fn):
        BUILDERS[name] = fn
        return fn
    return deco


def _base(fx: Fixture, remote: bool = True) -> None:
    fx.g("init", "-q", "-b", "main")
    fx.write("README.md", "# demo\n")
    fx.write("src/app.py", "from src import util\n\ndef main():\n    return util.add(1, 2)\n")
    fx.write("src/util.py", "def add(a, b):\n    return a + b\n")
    fx.write("tests/test_app.py", "from src import app\n\ndef test_main():\n    assert app.main() == 3\n")
    fx.commit("feat: initial application", 1)
    fx.write("src/util.py", "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    fx.commit("feat: add sub helper", 2)
    fx.g("tag", "v1")
    fx.write("docs/guide.md", "guide\n")
    fx.commit("docs: add guide", 3)
    fx.g("branch", "feature/test")
    fx.g("checkout", "-q", "feature/test")
    fx.write("src/feature.py", "def feature():\n    return 'f'\n")
    fx.commit("feat: feature work", 4)
    fx.g("checkout", "-q", "main")
    if remote:
        bare = fx.root / "origin.git"
        git(fx.root, "init", "-q", "--bare", "-b", "main", str(bare))
        fx.g("remote", "add", "origin", str(bare))
        fx.g("push", "-q", "-u", "origin", "main")
        fx.g("push", "-q", "origin", "feature/test")
    fx.meta["head"] = fx.g("rev-parse", "HEAD").stdout.strip()


@builder("clean")
def _clean(fx): _base(fx)


@builder("dirty")
def _dirty(fx):
    _base(fx)
    fx.write("src/util.py", "def add(a, b):\n    return a + b  # staged edit\n\ndef sub(a, b):\n    return a - b\n")
    fx.g("add", "src/util.py")
    fx.write("src/app.py", "from src import util\n\ndef main():\n    return util.add(2, 3)  # unstaged edit\n")
    fx.write("notes/todo.txt", "untracked\n")


@builder("staged")
def _staged(fx):
    _base(fx); fx.write("src/staged.py", "x = 1\n"); fx.g("add", "src/staged.py")


@builder("unstaged")
def _unstaged(fx):
    _base(fx); fx.write("src/app.py", "from src import util\n\ndef main():\n    return 42\n")


@builder("untracked")
def _untracked(fx):
    _base(fx); fx.write("scratch/a.txt", "a\n"); fx.write("scratch/deep/b.txt", "b\n")


@builder("unborn")
def _unborn(fx): fx.g("init", "-q", "-b", "main")


@builder("detached")
def _detached(fx): _base(fx); fx.g("checkout", "-q", "--detach", "HEAD~1")


@builder("conflict")
def _conflict(fx):
    _base(fx)
    fx.g("checkout", "-q", "-b", "side")
    fx.write("src/util.py", "def add(a, b):\n    return a + b + 1  # side\n\ndef sub(a, b):\n    return a - b\n"); fx.commit("side change", 5)
    fx.g("checkout", "-q", "main")
    fx.write("src/util.py", "def add(a, b):\n    return a + b + 2  # main\n\ndef sub(a, b):\n    return a - b\n"); fx.commit("main change", 6)
    fx.g("merge", "side", check=False)


@builder("rebase")
def _rebase(fx):
    _base(fx)
    fx.g("checkout", "-q", "-b", "topic")
    fx.write("src/util.py", "def add(a, b):\n    return 'topic'\n\ndef sub(a, b):\n    return a - b\n"); fx.commit("topic change", 5)
    fx.g("checkout", "-q", "main")
    fx.write("src/util.py", "def add(a, b):\n    return 'main'\n\ndef sub(a, b):\n    return a - b\n"); fx.commit("main change", 6)
    fx.g("checkout", "-q", "topic"); fx.g("rebase", "main", check=False)


@builder("cherrypick")
def _cherry(fx):
    _base(fx)
    fx.g("checkout", "-q", "-b", "pick")
    fx.write("src/util.py", "def add(a, b):\n    return 'pick'\n\ndef sub(a, b):\n    return a - b\n"); sha = fx.commit("pick me", 5)
    fx.g("checkout", "-q", "main")
    fx.write("src/util.py", "def add(a, b):\n    return 'main2'\n\ndef sub(a, b):\n    return a - b\n"); fx.commit("main diverge", 6)
    fx.g("cherry-pick", sha, check=False)


@builder("stashes")
def _stashes(fx):
    _base(fx)
    fx.write("src/app.py", "# stash one\n"); fx.g("stash", "push", "-q", "-m", "first stash")
    fx.write("src/util.py", "# stash two\n"); fx.g("stash", "push", "-q", "-m", "second stash")
    fx.write("src/app.py", "# live edit\n")


@builder("branches")
def _branches(fx):
    _base(fx)
    for i, name in enumerate(("release/1.0", "hotfix/x", "wip/y")):
        fx.g("checkout", "-q", "-b", name); fx.write(f"src/b{i}.py", f"b={i}\n"); fx.commit(f"work on {name}", 10 + i)
    fx.g("checkout", "-q", "main")


@builder("deleted_branch")
def _deleted(fx):
    _base(fx)
    fx.g("checkout", "-q", "-b", "gone/work"); fx.write("src/gone.py", "gone = True\n")
    fx.meta["lost_sha"] = fx.commit("lost: precious work on gone branch", 7)
    fx.g("checkout", "-q", "main"); fx.g("branch", "-D", "gone/work")


@builder("detached_commit")
def _detached_commit(fx):
    _base(fx)
    fx.g("checkout", "-q", "--detach"); fx.write("src/detached.py", "d = 1\n")
    fx.meta["lost_sha"] = fx.commit("lost: detached head commit", 7)
    fx.g("checkout", "-q", "main")


@builder("reflog_only")
def _reflog_only(fx):
    _base(fx)
    fx.write("src/lost.py", "lost = 1\n"); fx.meta["lost_sha"] = fx.commit("lost: reset away commit", 7)
    fx.g("reset", "-q", "--hard", "HEAD~1")   # inside the tmp fixture only


@builder("stash_lost")
def _stash_lost(fx):
    _base(fx)
    fx.write("src/app.py", "# precious uncommitted work\n"); fx.g("stash", "push", "-q", "-m", "lost stash")
    fx.meta["lost_sha"] = fx.g("rev-parse", "refs/stash").stdout.strip()
    fx.g("stash", "drop", "-q")


@builder("unreachable")
def _unreachable(fx):
    _base(fx)
    tree = fx.g("rev-parse", "HEAD^{tree}").stdout.strip()
    fx.meta["lost_sha"] = fx.g("commit-tree", tree, "-p", "HEAD", "-m", "lost: dangling unreachable commit").stdout.strip()


@builder("worktree")
def _worktree(fx):
    _base(fx)
    wt = fx.root / "wt"
    fx.g("worktree", "add", "-q", str(wt), "feature/test")
    fx.meta["worktree"] = str(wt)


@builder("lost_worktree")
def _lost_worktree(fx):
    _base(fx)
    wt = fx.root / "wt-lost"
    fx.g("worktree", "add", "-q", "-b", "wt/work", str(wt))
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "wt.py").write_text("wt = 1\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "lost-looking: commit only in vanished worktree")
    fx.meta["lost_sha"] = git(wt, "rev-parse", "HEAD").stdout.strip()
    shutil.rmtree(wt)   # directory vanishes; branch + admin entry remain


@builder("submodule")
def _submodule(fx):
    sub = fx.root / "subsrc"
    sub.mkdir()
    git(sub, "init", "-q", "-b", "main")
    (sub / "lib.py").write_text("lib = 1\n")
    git(sub, "add", "-A")
    git(sub, "commit", "-q", "-m", "sub init")
    _base(fx)
    fx.g("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "deps/sub")
    fx.commit("add submodule", 5)


def fast_import_history(repo, n: int, files_per_commit: int = 2, distinct_files: int = 25, extra_prefix: str = "src/mod") -> None:
    """Create ``n`` linear commits on ``main`` via ``git fast-import`` (fast; deterministic)."""
    out = []
    for i in range(1, n + 1):
        body = f"v{i}\n"
        msg = f"commit {i}"
        out += ["commit refs/heads/main", f"mark :{i}", f"committer T <t@example.invalid> {1_700_000_000 + i * 60} +0000",
                f"data {len(msg.encode())}", msg]
        if i > 1:
            out.append(f"from :{i - 1}")
        for j in range(files_per_commit):
            f = f"{extra_prefix}{(i * (j + 3) + j) % distinct_files}.py" if j == 0 else f"docs/d{(i + j) % max(7, distinct_files // 4)}.md"
            out += [f"M 100644 inline {f}", f"data {len(body.encode())}", body.rstrip("\n")]
        out.append("")
    git(repo, "fast-import", "--quiet", input="\n".join(out) + "\n", timeout=300)
    git(repo, "checkout", "-q", "-f", "main")


@builder("large_history")
def _large(fx, n: int = 300):
    fx.g("init", "-q", "-b", "main")
    fast_import_history(fx.repo, n)


@builder("renames")
def _renames(fx):
    _base(fx)
    fx.g("mv", "src/util.py", "src/helpers.py"); fx.commit("refactor: rename util", 5)
    fx.g("mv", "src/app.py", "src/main_app.py")


@builder("spaces")
def _spaces(fx):
    _base(fx); fx.write("dir with space/my file.py", "x=1\n"); fx.commit("add spaced", 5)
    fx.write("dir with space/my file.py", "x=2\n"); fx.write("new file.txt", "n\n")


@builder("tabs")
def _tabs(fx):
    _base(fx); fx.write("tab\tname.py", "x=1\n"); fx.commit("add tab name", 5)
    fx.write("tab\tname.py", "x=2\n"); fx.write("other\ttab.txt", "o\n")


@builder("newlines")
def _newlines(fx):
    _base(fx); fx.write("new\nline.py", "x=1\n"); fx.commit("add newline name", 5)
    fx.write("new\nline.py", "x=2\n"); fx.write("also\nnew.txt", "o\n")


@builder("symlinks")
def _symlinks(fx):
    _base(fx)
    os.symlink("src/app.py", fx.repo / "link_to_file")
    os.symlink("src", fx.repo / "link_to_dir")
    os.symlink("does-not-exist", fx.repo / "dangling")
    os.symlink(str(fx.outside), fx.repo / "link_outside")
    fx.commit("add symlinks", 5)
    fx.write("src/app.py", "# edit behind symlink\n")


STATES = ["clean", "dirty", "staged", "unstaged", "untracked", "unborn", "detached", "conflict", "rebase", "cherrypick",
          "stashes", "branches", "deleted_branch", "worktree", "submodule", "large_history", "renames", "spaces", "tabs",
          "newlines", "symlinks"]
RECOVERY_KINDS = ["deleted_branch", "detached_commit", "reflog_only", "stash_lost", "unreachable", "lost_worktree"]


def build_state(root: Path, kind: str) -> Fixture:
    """Build fixture ``kind`` under ``root`` (a fresh directory inside tmp_path) and return it."""
    fx = new_fixture(Path(root), kind)
    BUILDERS[kind](fx)
    return fx


# --------------------------------------------------------------------------- the command battery

def warp_battery() -> list:
    """Every ``warp.py`` command in a read-only invocation -> list of (name, args)."""
    return [
        ("xray", ["xray"]),
        ("pr", ["pr", "--base=main"]),
        ("pr-head", ["pr", "--base=HEAD"]),
        ("blast", ["blast"]),
        ("commits", ["commits"]),
        ("conflict", ["conflict"]),
        ("temporal", ["temporal"]),
        ("rescue-scan", ["rescue", "scan"]),
        ("rescue-scan-nofsck", ["rescue", "scan", "--no-fsck"]),
        ("rescue-inspect", ["rescue", "inspect", "HEAD"]),
        ("rescue-preserve-dry", ["rescue", "preserve", "HEAD", "--dry-run"]),
        ("archaeology", ["archaeology", "src/util.py"]),
        ("archaeology-q", ["archaeology", "--question=feature"]),
        ("bisect-plan", ["bisect", "plan", "--good=HEAD~1", "--bad=HEAD"]),
        ("bisect-status", ["bisect", "status"]),
        ("memory-index", ["memory", "index"]),
        ("memory-status", ["memory", "status"]),
        ("memory-hotspots", ["memory", "hotspots"]),
        ("memory-churn", ["memory", "churn"]),
        ("memory-cochange", ["memory", "cochange", "src/util.py"]),
        ("memory-introduced", ["memory", "introduced", "src/util.py"]),
        ("memory-authors", ["memory", "authors", "src/util.py"]),
        ("memory-reverts", ["memory", "reverts"]),
        ("memory-sessions", ["memory", "sessions"]),
    ]


GUARD_CLI = ("guard-check", ["guard", "check", "git status"])
