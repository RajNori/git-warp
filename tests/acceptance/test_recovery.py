"""Recovery fixtures (Disposable GW-RECOVERY-001..006): ``warp.py rescue scan`` must surface lost-looking work
WITHOUT moving refs, resetting HEAD, touching the index, cleaning files, or running gc / prune / reflog expire.

A ``git`` shim on PATH records every git invocation the tool makes, so "does not run gc/prune/reflog expire" is
observed rather than assumed.
"""
from __future__ import annotations

import json

import pytest

from tests.acceptance import helpers as h

# (kind, must_find).  A vanished worktree's branch still exists, so its commit is not "lost": the contract there is
# safety (no crash, no mutation) rather than discovery.
KINDS = [("deleted_branch", True), ("detached_commit", True), ("reflog_only", True), ("stash_lost", True),
         ("unreachable", True), ("lost_worktree", False)]


@pytest.fixture(scope="module", params=KINDS, ids=[f"GW-RECOVERY-{i:03d}-{k}" for i, (k, _) in enumerate(KINDS, 1)])
def scanned(request, tmp_path_factory):
    kind, must_find = request.param
    base = tmp_path_factory.mktemp(f"recovery-{kind}")
    fx = h.build_state(base / "fx", kind)
    log = base / "git-calls.log"
    env = h.install_git_shim(base / "bin", log)
    before = h.snapshot(fx.repo)
    runs = {
        "scan": h.run_warp(fx.repo, "rescue", "scan", env=env),
        "scan-all": h.run_warp(fx.repo, "rescue", "scan", "--include-reachable", "--limit=100", env=env),
        "inspect": h.run_warp(fx.repo, "rescue", "inspect", fx.meta["lost_sha"], env=env),
        "preserve-dry": h.run_warp(fx.repo, "rescue", "preserve", fx.meta["lost_sha"], "--dry-run", env=env),
    }
    after = h.snapshot(fx.repo)
    return {"kind": kind, "must_find": must_find, "fx": fx, "runs": runs, "log": log,
            "mutated": h.diff(before, after)}


def test_rescue_surfaces_lost_commit(scanned):
    lost = scanned["fx"].meta["lost_sha"]
    r = scanned["runs"]["scan"]
    assert not r.timed_out and not r.traceback and r.code == 0, r.stderr[-300:]
    data = json.loads(r.stdout)
    assert isinstance(data, dict) and not data.get("error"), data.get("error")
    if scanned["must_find"]:
        assert lost in r.stdout or lost[:7] in r.stdout, f"{scanned['kind']}: lost commit {lost[:12]} not surfaced"


def test_rescue_does_not_mutate_the_repository(scanned):
    assert scanned["mutated"] == [], scanned["mutated"]


def test_rescue_never_runs_gc_prune_or_reflog_expire(scanned):
    bad = h.forbidden_git_calls(scanned["log"])
    assert bad == [], bad
    assert scanned["log"].exists(), "git shim saw no invocations -- the check would be vacuous"


def test_rescue_inspect_and_dry_run_preserve_are_safe(scanned):
    for name in ("inspect", "preserve-dry", "scan-all"):
        r = scanned["runs"][name]
        assert not r.timed_out and not r.traceback, (name, r.stderr[-300:])
        json.loads(r.stdout)
    # the lost object is still there: nothing was pruned
    h.git(scanned["fx"].repo, "cat-file", "-e", scanned["fx"].meta["lost_sha"])
    if scanned["must_find"]:
        assert json.loads(scanned["runs"]["inspect"].stdout).get("error") is None


def test_shim_detector_flags_destructive_verbs(tmp_path):
    """The forbidden-call detector must itself recognise what it is meant to forbid."""
    log = tmp_path / "log"
    log.write_text("gc --prune=now\nreflog expire --expire=now --all\nprune\n-c a=b stash drop\nbranch -D x\n"
                   "update-ref -d refs/heads/x\nworktree prune\nstatus --porcelain\nlog --oneline\n"
                   "reflog show --format=%H\nstash list\nfsck --no-reflogs --unreachable\n")
    assert len(h.forbidden_git_calls(log)) == 7
