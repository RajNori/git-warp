import os
import subprocess
import sys

import pytest

from tests.unit._history_helpers import run_cli, snapshot


def cand(out, sha):
    return next((c for c in out["candidates"] if c["sha"] == sha), None)


def test_reset_hard_recovery_and_preserve(repo):
    lost = repo.commit("precious work", {"work.txt": "important\n"})
    repo.git("reset", "--hard", "HEAD~1")
    head_before = repo.sha()
    before = snapshot(repo)
    code, out = run_cli("rescue", "scan", "--repo", repo.path)
    assert code == 0
    c = cand(out, lost)
    assert c, out["candidates"]
    assert c["confidence"] == "high"
    assert "reset-abandoned" in c["kinds"]
    assert c["reachable_from"] == []
    assert c["subject"] == "precious work"
    assert "work.txt" in c["files"]
    assert c["safe_action"].startswith("git branch rescue/") and lost in c["safe_action"]
    assert any("reset: moving to" in e for e in c["why_candidate"])
    assert snapshot(repo) == before          # scan is read-only

    code, dry = run_cli("rescue", "preserve", lost, "--dry-run", "--repo", repo.path)
    assert code == 0 and dry["status"] == "dry_run" and dry["changed"] is False
    assert snapshot(repo) == before

    status_before = repo.git("status", "--porcelain")
    code, p = run_cli("rescue", "preserve", lost, "--repo", repo.path)
    assert code == 0 and p["status"] == "created" and p["verified"] is True
    assert repo.git("rev-parse", p["branch"]) == lost
    assert p["ran"] == f"git branch {p['branch']} {lost}"
    assert repo.sha() == head_before
    assert repo.git("status", "--porcelain") == status_before
    assert repo.git("symbolic-ref", "HEAD") == "refs/heads/main"
    assert not (repo.path / "work.txt").exists()      # working tree untouched

    # second scan: now reachable from the rescue branch, no longer a candidate
    _, out2 = run_cli("rescue", "scan", "--repo", repo.path)
    assert cand(out2, lost) is None
    # idempotent preserve
    code, again = run_cli("rescue", "preserve", lost, "--name", p["branch"], "--repo", repo.path)
    assert code == 0 and again["status"] == "already_preserved"


def test_preserve_refuses_overwrite_and_bad_names(repo):
    other = repo.sha("HEAD~1")
    target = repo.sha()
    repo.branch("keepme", other)
    code, out = run_cli("rescue", "preserve", target, "--name", "keepme", "--repo", repo.path)
    assert code == 2 and "already exists" in out["error"]
    assert repo.git("rev-parse", "keepme") == other
    for bad in ["--bad", "-x", "a..b", "has space", "x\ny", "a/", "/a", "a//b", "a.lock", ".hidden", "a/.b", "HEAD", "a~1", "a:b", "a^b", "", "a@{b"]:
        code, out = run_cli("rescue", "preserve", target, "--name=" + bad if bad.startswith("-") else "--name", *([] if bad.startswith("-") else [bad]), "--repo", repo.path)
        assert code == 2 and "error" in out, bad
    assert repo.git("branch", "--list").split() == ["*", "main", "keepme"] or "keepme" in repo.git("branch", "--list")
    assert repo.git("for-each-ref", "refs/heads").count("\n") == 1   # only main + keepme


def test_preserve_rejects_non_commits(repo):
    blob = repo.git("rev-parse", "HEAD:f0.txt")
    tree = repo.git("rev-parse", "HEAD^{tree}")
    for rev in (blob, tree, "deadbeefdeadbeefdeadbeef", "--force", "nonexistent-ref"):
        code, out = run_cli("rescue", "preserve", rev, "--repo", repo.path)
        assert code in (2,) and "error" in out, rev
    assert repo.git("for-each-ref", "refs/heads").count("\n") == 0


def test_deleted_branch(repo):
    repo.checkout("main")
    repo.branch("feature", checkout=True)
    tip = repo.commit("feature work", {"feat.txt": "f\n"})
    repo.checkout("main")
    repo.git("branch", "-D", "feature")
    code, out = run_cli("rescue", "scan", "--repo", repo.path)
    c = cand(out, tip)
    assert c and c["confidence"] == "high"
    assert "deleted-branch-tip" in c["kinds"] and c["deleted_branch"] == "feature"
    assert {"branch": "feature", "tip": tip, "short": tip[:8], "subject": "feature work"} in out["deleted_branch_candidates"]


def test_bad_rebase_orig_head(repo):
    repo.branch("topic", checkout=True)
    t1 = repo.commit("topic 1", {"t1.txt": "1\n"})
    t2 = repo.commit("topic 2", {"t2.txt": "2\n"})
    repo.checkout("main")
    repo.commit("main moves", {"m.txt": "m\n"})
    repo.checkout("topic")
    repo.git("rebase", "main")
    assert repo.sha() != t2
    code, out = run_cli("rescue", "scan", "--repo", repo.path)
    c = cand(out, t2)
    assert c, [x["subject"] for x in out["candidates"]]
    assert "rebase-original" in c["kinds"] and c["confidence"] == "high"
    assert out["state"]["orig_head"] == t2
    assert any("ORIG_HEAD" in r for r in c["confidence_reasons"])
    assert cand(out, t1) is None            # covered by the tip, not listed separately
    assert [x["subject"] for x in c["also_unreachable"]] == ["topic 1"]


def test_amend_is_medium(repo):
    old = repo.sha()
    repo.git("commit", "-q", "--amend", "-m", "amended")
    _, out = run_cli("rescue", "scan", "--repo", repo.path)
    c = cand(out, old)
    assert c and c["confidence"] == "medium" and "amend-original" in c["kinds"]


def test_dropped_stash(repo):
    repo.write("f0.txt", "changed\n")
    repo.git("stash", "push", "-m", "my stash")
    sha = repo.git("rev-parse", "stash@{0}")
    repo.git("stash", "drop")
    code, out = run_cli("rescue", "scan", "--repo", repo.path)
    c = cand(out, sha)
    assert c and "dropped-stash" in c["kinds"] and c["confidence"] == "high"
    assert "f0.txt" in c["files"]
    assert out["stashes"] == []
    # live stash is listed but not a candidate
    repo.write("f0.txt", "again\n")
    repo.git("stash", "push", "-m", "live")
    live = repo.git("rev-parse", "stash@{0}")
    _, out = run_cli("rescue", "scan", "--repo", repo.path)
    assert len(out["stashes"]) == 1 and cand(out, live) is None


def test_detached_head_orphans(repo):
    repo.git("checkout", "-q", "--detach")
    a = repo.commit("detached a", {"a.txt": "a\n"})
    b = repo.commit("detached b", {"b.txt": "b\n"})
    # still on detached HEAD: reported as at-risk, medium
    _, out = run_cli("rescue", "scan", "--repo", repo.path)
    assert out["state"]["detached"] is True
    c = cand(out, b)
    assert c and c["confidence"] == "medium" and c["reachable_from"] == ["HEAD (detached)"]
    repo.checkout("main")
    _, out = run_cli("rescue", "scan", "--repo", repo.path)
    c = cand(out, b)
    assert c and c["confidence"] == "high" and "detached-head-work" in c["kinds"] and c["reachable_from"] == []
    assert cand(out, a) is None and c["unreachable_commit_count"] == 2


def test_filters(repo):
    a = repo.commit("alpha feature", {"src/a.py": "a\n"})
    b = repo.commit("beta feature", {"docs/b.md": "b\n"})
    repo.git("reset", "--hard", "HEAD~2")
    _, out = run_cli("rescue", "--grep", "alpha", "--repo", repo.path)
    assert [c["sha"] for c in out["candidates"]] == [b] or cand(out, b)   # chain tip covers alpha
    assert cand(out, b)
    _, out = run_cli("rescue", "--grep", "nomatch-xyz", "--repo", repo.path)
    assert out["candidates"] == []
    _, out = run_cli("rescue", "--path", "src/a.py", "--repo", repo.path)
    assert cand(out, b)
    _, out = run_cli("rescue", "--path", "elsewhere", "--repo", repo.path)
    assert out["candidates"] == []
    _, out = run_cli("rescue", "--since", "2001-01-01", "--repo", repo.path)
    assert cand(out, b)
    _, out = run_cli("rescue", "--since", "2 hours ago", "--repo", repo.path)   # fake clock is in 2023
    assert out["candidates"] == []
    _, out = run_cli("rescue", "--no-fsck", "--repo", repo.path)
    assert any("fsck was skipped" in w for w in out["warnings"])


def test_force_push_overwritten_remote_ref(make_repo):
    up = make_repo("up")
    up.seed(1)
    old = up.commit("original", {"x.txt": "1\n"})
    clone = make_repo("clone", init=False)
    subprocess.run(["git", "clone", "-q", str(up.path), str(clone.path)], check=True, env=dict(os.environ))
    clone.git("config", "user.name", "T")
    clone.git("config", "user.email", "t@e.c")
    up.git("reset", "--hard", "HEAD~1")
    up.commit("rewritten", {"x.txt": "2\n"})
    clone.git("reset", "--hard", "-q", "HEAD~1")     # local main no longer holds `old`
    clone.git("fetch", "-q", "origin")               # origin/main is force-updated away from `old`
    _, out = run_cli("rescue", "--repo", clone.path)
    c = cand(out, old)
    assert c, out["candidates"]
    assert c["confidence"] == "high" and c["reachable_from"] == []
    assert {"force-push-overwritten", "reset-abandoned"} & set(c["kinds"])
    assert any("origin/main" in e for e in c["why_candidate"])


def test_unborn_repo(empty_repo):
    code, out = run_cli("rescue", "scan", "--repo", empty_repo.path)
    assert code == 0 and out["candidates"] == [] and out["state"]["unborn"] is True
    assert any("no commits" in w for w in out["warnings"])
    code, out = run_cli("rescue", "preserve", "HEAD", "--repo", empty_repo.path)
    assert code == 2 and "error" in out


def test_dangling_blob_reported_on_unborn(empty_repo):
    empty_repo.write("staged.txt", "never committed\n")
    empty_repo.git("add", "staged.txt")
    empty_repo.git("rm", "-q", "--cached", "staged.txt")
    _, out = run_cli("rescue", "--repo", empty_repo.path)
    assert out["dangling_objects"]["blobs"] >= 1 and out["dangling_objects"]["blob_samples"]


def test_non_repo(tmp_path):
    code, out = run_cli("rescue", "scan", "--repo", tmp_path)
    assert code == 2 and "error" in out
    code, out = run_cli("rescue", "inspect", "abc", "--repo", tmp_path / "missing")
    assert code == 2 and "error" in out


def test_shallow_clone(make_repo):
    src = make_repo("src").seed(4)
    sh = make_repo("shallow", init=False)
    subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + str(src.path), str(sh.path)], check=True)
    code, out = run_cli("rescue", "scan", "--repo", sh.path)
    assert code == 0 and out["state"]["shallow"] is True
    assert any("shallow" in w for w in out["warnings"])


def test_in_progress_operation_warning(repo):
    repo.merge_conflict()
    _, out = run_cli("rescue", "scan", "--repo", repo.path)
    assert out["state"]["operation"] == "merge"
    assert any("merge is in progress" in w for w in out["warnings"])


def test_inspect(repo):
    lost = repo.commit("lost one", {"l.txt": "l\n"})
    repo.git("reset", "--hard", "HEAD~1")
    before = snapshot(repo)
    code, out = run_cli("rescue", "inspect", lost, "--repo", repo.path)
    assert code == 0
    assert out["unreachable"] is True and out["reachable_from"] == []
    assert out["ancestry_vs_head"]["head_is_ancestor_of_commit"] is True
    assert out["ancestry_vs_head"]["is_ancestor_of_head"] is False
    assert out["ancestry_vs_head"]["candidate_only_commits"][0]["subject"] == "lost one"
    assert "l.txt" in out["files"] and "l.txt" in out["stat"]
    assert snapshot(repo) == before
    code, out = run_cli("rescue", "inspect", "0" * 40, "--repo", repo.path)
    assert code == 2 and "error" in out
    code, out = run_cli("rescue", "inspect", "--evil", "--repo", repo.path)
    assert code == 2 and "error" in out


def test_usage_errors_are_json(repo):
    code, out = run_cli("rescue", "preserve", "--repo", repo.path)
    assert code == 2 and "usage error" in out["error"]
    code, out = run_cli("rescue", "scan", "--limit", "abc", "--repo", repo.path)
    assert code == 2 and "error" in out
