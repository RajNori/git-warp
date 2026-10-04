"""The mutation detector is itself a safety instrument: it must notice every kind of repository change that a
"read-only" operation could cause, and must not cry wolf for the plugin's own private state."""
from __future__ import annotations

import os

import pytest

from tests.acceptance import helpers as h


@pytest.fixture
def fx(tmp_path):
    return h.build_state(tmp_path / "fx", "clean")


def _changed(fx, action):
    before = h.snapshot(fx.repo)
    action(fx)
    return h.diff(before, h.snapshot(fx.repo))


def test_no_change_is_no_diff(fx):
    assert _changed(fx, lambda f: None) == []


def test_pure_status_reads_are_not_mutation(fx):
    def reads(f):
        for args in (("status",), ("log", "--oneline"), ("diff",), ("stash", "list"), ("for-each-ref",)):
            h.git(f.repo, *args)
    assert _changed(fx, reads) == []


def test_detects_new_commit(fx):
    d = _changed(fx, lambda f: f.g("commit", "-q", "--allow-empty", "-m", "x"))
    assert "head" in d and "refs" in d


def test_detects_new_branch_ref(fx):
    assert "refs" in _changed(fx, lambda f: f.g("branch", "newbranch"))


def test_detects_new_tag(fx):
    assert "refs" in _changed(fx, lambda f: f.g("tag", "v9"))


def test_detects_head_move_to_detached(fx):
    d = _changed(fx, lambda f: f.g("checkout", "-q", "--detach", "HEAD~1"))
    assert "head" in d and "head_ref" in d


def test_detects_index_change(fx):
    def stage(f):
        f.write("new.txt", "n\n")
        f.g("add", "new.txt")
    d = _changed(fx, stage)
    assert "index_entries" in d


def test_detects_tracked_file_edit(fx):
    d = _changed(fx, lambda f: f.write("README.md", "edited\n"))
    assert "tracked" in d and "status" in d


def test_detects_untracked_file(fx):
    d = _changed(fx, lambda f: f.write("stray.txt", "s\n"))
    assert "untracked" in d


def test_detects_stash(fx):
    def stash(f):
        f.write("README.md", "wip\n")
        f.g("stash", "push", "-q", "-m", "wip")
    assert "stash" in _changed(fx, stash)


def test_detects_config_change(fx):
    assert "config" in _changed(fx, lambda f: f.g("config", "core.fsmonitor", "false"))


def test_detects_worktree_add(fx):
    d = _changed(fx, lambda f: f.g("worktree", "add", "-q", str(f.root / "wt2"), "-b", "wt2"))
    assert "worktrees" in d


def test_detects_stray_file_in_git_dir(fx):
    d = _changed(fx, lambda f: (f.repo / ".git" / "STRAY").write_text("x"))
    assert any(x.startswith("gitdir:") for x in d)


def test_detects_reflog_expire_and_gc_effects(fx):
    fx.g("commit", "-q", "--allow-empty", "-m", "extra")
    fx.g("reset", "-q", "--hard", "HEAD~1")
    d = _changed(fx, lambda f: (f.g("reflog", "expire", "--expire=now", "--all"), f.g("gc", "-q", "--prune=now")))
    assert d, "reflog expire / gc must register as a change"


def test_ignores_plugin_private_state_dir(fx):
    def write_state(f):
        sd = f.repo / ".git" / h.GIT_STATE_DIRNAME
        sd.mkdir(exist_ok=True)
        (sd / "warp.db").write_bytes(b"x")
        (sd / "flight-recorder.jsonl").write_text("{}\n")
        (sd / "sub").mkdir(exist_ok=True)
        (sd / "sub" / "deep.bin").write_bytes(b"y")
    assert _changed(fx, write_state) == []


def test_ignores_lock_files_and_pure_index_stat_refresh(fx):
    def noise(f):
        (f.repo / ".git" / "something.lock").write_text("")
        past = 1_000_000_000
        os.utime(f.repo / "README.md", (past, past))   # forces a stat refresh of the index, content unchanged
        h.git(f.repo, "status", "--porcelain")
    assert _changed(fx, noise) == []


def test_detects_mutation_in_linked_worktree_common_dir(tmp_path):
    fx = h.build_state(tmp_path / "wt", "worktree")
    before = h.snapshot(fx.target)
    h.git(fx.target, "branch", "from-linked")
    assert "refs" in h.diff(before, h.snapshot(fx.target))


@pytest.mark.parametrize("kind", h.STATES + h.RECOVERY_KINDS + ["detached_commit"])
def test_every_fixture_builds_and_snapshots_stably(tmp_path, kind):
    """Fixtures are deterministic enough that two consecutive snapshots agree (so a diff means a real change)."""
    f = h.build_state(tmp_path / kind, kind)
    a = h.snapshot(f.target)
    b = h.snapshot(f.target)
    assert h.diff(a, b) == []
    if kind in h.RECOVERY_KINDS:
        assert len(f.meta["lost_sha"]) == 40
