"""Shared test fixtures. ``RepoBuilder`` creates real temporary Git repositories."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

_ENV = {
    "GIT_AUTHOR_NAME": "Test Author", "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer", "GIT_COMMITTER_EMAIL": "committer@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "LC_ALL": "C",
}


class RepoBuilder:
    """Fluent helper around a temp repo. All commands use argv lists, never a shell."""

    def __init__(self, path: Path, branch: str = "main", init: bool = True):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._clock = 1_700_000_000
        if init:
            self.git("init", "-q", f"--initial-branch={branch}")
            self.git("config", "commit.gpgsign", "false")
            self.git("config", "user.name", "Test Author")
            self.git("config", "user.email", "author@example.com")

    def git(self, *args, check=True, input=None) -> str:
        env = dict(os.environ, **_ENV)
        self._clock += 60
        stamp = f"{self._clock} +0000"
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        p = subprocess.run(["git", *map(str, args)], cwd=self.path, capture_output=True, text=True, env=env, input=input)
        if check and p.returncode != 0:
            raise AssertionError(f"git {' '.join(map(str, args))} failed: {p.stderr}")
        return p.stdout.strip()

    def write(self, rel: str, content: str = "x\n") -> "RepoBuilder":
        f = self.path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        return self

    def commit(self, message: str = "commit", files: dict = None, all: bool = True) -> str:
        for rel, content in (files or {}).items():
            self.write(rel, content)
        self.git("add", "-A") if all else None
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.sha()

    def sha(self, ref: str = "HEAD") -> str:
        return self.git("rev-parse", ref)

    def branch(self, name: str, start: str = "HEAD", checkout: bool = False) -> "RepoBuilder":
        self.git("checkout", "-q", "-b", name, start) if checkout else self.git("branch", name, start)
        return self

    def checkout(self, ref: str) -> "RepoBuilder":
        self.git("checkout", "-q", ref)
        return self

    def merge_conflict(self, file: str = "conflict.txt") -> str:
        """Leave the repo mid-merge with a content conflict in ``file``; returns the other branch name."""
        base = self.git("symbolic-ref", "--short", "HEAD")
        self.commit("base for conflict", {file: "line\n"})
        self.branch("topic", checkout=True)
        self.commit("topic change", {file: "topic\n"})
        self.checkout(base)
        self.commit("main change", {file: "main\n"})
        self.git("merge", "topic", check=False)
        return "topic"

    def seed(self, n: int = 3) -> "RepoBuilder":
        for i in range(n):
            self.commit(f"commit {i}", {f"f{i}.txt": f"{i}\n"})
        return self


@pytest.fixture
def make_repo(tmp_path):
    """Factory: ``make_repo(name="r", branch="main", init=True) -> RepoBuilder``."""
    counter = [0]

    def factory(name=None, branch="main", init=True):
        counter[0] += 1
        return RepoBuilder(tmp_path / (name or f"repo{counter[0]}"), branch=branch, init=init)

    return factory


@pytest.fixture
def repo(make_repo):
    """A repo with three commits on ``main``."""
    return make_repo().seed(3)


@pytest.fixture
def empty_repo(make_repo):
    """Unborn repository (no commits)."""
    return make_repo()


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
