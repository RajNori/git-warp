"""X-Ray integration tests on real temporary repositories."""
import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import SCRIPTS, RepoBuilder
from gitwarp.analysis import cli

SECRET = "sk-live-abcdefghijklmnop1234567890"


def xray(path, *args):
    """In-process CLI call -> (exit_code, parsed_json, raw_text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["xray", "--repo", str(path), *args])
    raw = buf.getvalue()
    return code, json.loads(raw), raw


def snapshot(r: RepoBuilder):
    gd = r.path / ".git"
    idx = gd / "index"
    return {
        "status": r.git("status", "--porcelain=v1", "-uall"),
        "refs": r.git("for-each-ref"),
        "head": r.git("rev-parse", "HEAD", check=False),
        "index_bytes": idx.read_bytes() if idx.exists() else None,
        "index_mtime": idx.stat().st_mtime_ns if idx.exists() else None,
        "stash": r.git("stash", "list"),
        "tree": sorted(str(p.relative_to(r.path)) for p in r.path.rglob("*") if ".git" not in p.parts),
    }


def ids(d):
    return [x["id"] for x in d["risk"]["driver_details"]]


def test_clean_repo_is_low(repo):
    code, d, _ = xray(repo.path)
    assert code == 0
    assert d["working_tree"]["clean"] is True
    assert d["risk"]["level"] == "LOW" and d["risk"]["drivers"]
    assert d["state"]["branch"] == "main" and not d["state"]["detached"] and not d["state"]["unborn"]
    assert d["state"]["reflog"]["available"] and d["state"]["upstream"] is None
    assert [x["id"] for x in d["recommendation"]] == ["no-action-needed"]
    assert len(d["recent_commits"]) == 3
    for key in ("state", "working_tree", "recent_commits", "high_churn", "sensitive_paths", "signals", "clusters", "recovery", "risk", "recommendation", "warnings"):
        assert key in d


def test_dirty_counts(repo):
    repo.write("f0.txt", "changed\n")            # unstaged
    repo.write("new_staged.txt", "a\n"); repo.git("add", "new_staged.txt")
    repo.write("untracked.txt", "u\n")
    _, d, _ = xray(repo.path)
    wt = d["working_tree"]
    assert wt["staged"]["count"] == 1 and wt["unstaged"]["count"] == 1 and wt["untracked"]["count"] == 1
    assert wt["conflicted"]["count"] == 0 and not wt["clean"]
    assert wt["numstat_totals"]["files"] == 2
    assert wt["largest_changes"]


def test_path_lists_are_capped(repo):
    for i in range(70):
        repo.write(f"many/f{i}.txt", "x\n")
    _, d, _ = xray(repo.path, "--untracked-all")
    u = d["working_tree"]["untracked"]
    assert u["count"] == 70 and len(u["paths"]) == 50 and u["truncated"] is True


def test_untracked_dir_collapsed_but_expanded_for_signals(repo):
    repo.write("secrets/.env", "A=1\n")
    _, d, _ = xray(repo.path)
    assert d["working_tree"]["untracked"]["collapsed_dirs"] == 1
    assert d["risk"]["level"] == "HIGH" and "secret-material" in ids(d)


def test_unborn(empty_repo):
    code, d, _ = xray(empty_repo.path)
    assert code == 0 and d["state"]["unborn"] is True and d["state"]["head"] is None
    assert d["risk"]["level"] == "LOW" and d["recent_commits"] == []
    assert d["state"]["reflog"]["available"] is False
    empty_repo.write("a.py", "x = 1\n"); empty_repo.git("add", "a.py")
    _, d, _ = xray(empty_repo.path)
    assert d["working_tree"]["staged"]["count"] == 1 and d["working_tree"]["numstat_totals"]["added"] == 1


def test_detached(repo):
    repo.git("checkout", "-q", "--detach", "HEAD~1")
    _, d, _ = xray(repo.path)
    assert d["state"]["detached"] is True and d["state"]["branch"] is None
    repo.write("f0.txt", "dirty\n")
    _, d, _ = xray(repo.path)
    assert "detached-dirty" in ids(d) and d["risk"]["level"] == "MEDIUM"
    assert "create-branch-for-detached-work" in [x["id"] for x in d["recommendation"]]


def test_no_upstream_ahead_of_base(repo):
    repo.branch("feature", checkout=True)
    repo.commit("work", {"x.txt": "1\n"})
    _, d, _ = xray(repo.path)
    assert d["state"]["upstream"] is None
    assert "no-upstream-ahead" in ids(d) and d["risk"]["level"] == "MEDIUM"
    assert "set-upstream-before-push" in [x["id"] for x in d["recommendation"]]
    repo.checkout("main")
    _, d, _ = xray(repo.path)
    assert "no-upstream-ahead" not in ids(d)


def _clone(make_repo, origin, name="clone", extra=()):
    c = RepoBuilder(origin.path.parent / name, init=False)
    subprocess.run(["git", "clone", "-q", *extra, origin.path.as_uri(), str(c.path)], check=True, capture_output=True)
    c.git("config", "user.name", "T"); c.git("config", "user.email", "t@e.com")
    return c


def test_with_upstream_ahead_behind_diverged(repo, make_repo):
    c = _clone(make_repo, repo)
    _, d, _ = xray(c.path)
    assert d["state"]["upstream"] == "origin/main" and (d["state"]["ahead"], d["state"]["behind"]) == (0, 0)
    c.commit("local", {"l.txt": "1\n"})
    repo.commit("remote", {"r.txt": "1\n"})
    c.git("fetch", "-q")
    _, d, _ = xray(c.path)
    assert (d["state"]["ahead"], d["state"]["behind"]) == (1, 1)
    assert "diverged" in ids(d) and d["risk"]["level"] == "MEDIUM"
    assert "rebase-or-merge-upstream" in [x["id"] for x in d["recommendation"]]


def test_upstream_not_diverged_is_low(repo, make_repo):
    c = _clone(make_repo, repo)
    c.commit("local", {"l.txt": "1\n"})
    _, d, _ = xray(c.path)
    assert d["state"]["ahead"] == 1 and "diverged" not in ids(d) and d["risk"]["level"] == "LOW"


def test_shallow(repo, make_repo):
    repo.seed(3)
    c = _clone(make_repo, repo, extra=["--depth", "1"])
    code, d, _ = xray(c.path)
    assert code == 0 and d["state"]["shallow"] is True
    assert any("shallow" in w for w in d["warnings"])


def test_mid_merge_conflict(make_repo):
    r = make_repo().seed(1)
    r.merge_conflict()
    code, d, _ = xray(r.path)
    assert code == 0
    assert d["state"]["operation"]["type"] == "merge"
    assert d["working_tree"]["conflicted"]["paths"] == ["conflict.txt"]
    assert d["risk"]["level"] == "HIGH" and "conflicted-paths" in ids(d)
    assert d["recommendation"][0]["id"] == "resolve-conflicts-first"


def test_mid_rebase_with_conflict(make_repo):
    r = make_repo().seed(1)
    r.commit("base", {"c.txt": "line\n"})
    r.branch("topic", checkout=True)
    r.commit("topic", {"c.txt": "topic\n"})
    r.checkout("main")
    r.commit("main", {"c.txt": "main\n"})
    r.checkout("topic")
    r.git("rebase", "main", check=False)
    _, d, _ = xray(r.path)
    assert d["state"]["operation"]["type"] == "rebase" and d["state"]["operation"].get("branch") == "topic"
    assert d["state"]["detached"] is True
    assert d["risk"]["level"] == "HIGH" and "rebase-dirty" in ids(d)


def test_mid_merge_without_conflict_is_medium(make_repo):
    r = make_repo().seed(1)
    r.branch("topic", checkout=True)
    r.commit("t", {"t.txt": "1\n"})
    r.checkout("main")
    r.commit("m", {"m.txt": "1\n"})
    r.git("merge", "--no-commit", "--no-ff", "topic")
    _, d, _ = xray(r.path)
    assert d["state"]["operation"]["type"] == "merge" and "operation-in-progress" in ids(d)
    assert d["risk"]["level"] == "MEDIUM"


def test_stashes_and_recovery(repo):
    repo.write("f0.txt", "stash me\n")
    repo.git("stash", "push", "-m", "my stash")
    _, d, _ = xray(repo.path)
    assert d["state"]["stashes"]["count"] == 1 and "my stash" in d["state"]["stashes"]["items"][0]["subject"]
    assert d["recovery"]["stashes"] == 1 and d["recovery"]["reflog_available"]
    assert "fsck" in d["recovery"]["dangling_commits"]


def test_worktrees(repo, tmp_path):
    wt = tmp_path / "linked wt"
    repo.git("worktree", "add", "-q", "-b", "wtbranch", str(wt))
    _, d, _ = xray(repo.path)
    assert d["state"]["worktrees"]["count"] == 2
    code, d2, _ = xray(wt)
    assert code == 0 and d2["state"]["branch"] == "wtbranch"
    assert sum(1 for w in d2["state"]["worktrees"]["items"] if w["current"]) == 1


def test_spaces_and_unicode_paths(repo):
    repo.write("dir with space/ünï côde.py", "x = 1\n")
    repo.write("dir with space/staged ü.py", "y = 1\n")
    repo.git("add", "dir with space/staged ü.py")
    repo.write("f0.txt", "mod\n")
    _, d, _ = xray(repo.path, "--untracked-all")
    assert "dir with space/ünï côde.py" in d["working_tree"]["untracked"]["paths"]
    assert "dir with space/staged ü.py" in d["working_tree"]["staged"]["paths"]
    assert d["clusters"]["items"]


def test_non_repo_and_bad_path(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    code, d, raw = xray(plain)
    assert code == 2 and "not a git repository" in d["error"]
    code, d, _ = xray(tmp_path / "does-not-exist")
    assert code == 2 and "error" in d
    code, d, _ = xray(plain, "--bogus")
    assert code == 2 and "error" in d


def test_secret_file_and_example_env(repo):
    repo.write(".env.example", "KEY=\n")
    _, d, _ = xray(repo.path)
    assert d["sensitive_paths"]["secret_files"]["count"] == 0
    repo.write(".env", "TOKEN=whatever\n")
    repo.write("certs/server.pem", "x\n")
    _, d, _ = xray(repo.path, "--untracked-all")
    assert d["risk"]["level"] == "HIGH" and "secret-material" in ids(d)
    assert set(d["sensitive_paths"]["secret_files"]["paths"]) == {".env", "certs/server.pem"}
    assert "review-secrets" in [x["id"] for x in d["recommendation"]]


def test_secret_content_never_echoed(repo):
    repo.write("config_loader.py", f'API_KEY = "{SECRET}"\n')
    repo.git("add", "config_loader.py")
    repo.write("other.py", f'x = "{SECRET}"\n')  # untracked
    _, d, raw = xray(repo.path)
    assert d["signals"]["secrets"]["content_findings"]["count"] >= 2
    assert "abcdefghijklmnop1234567890" not in raw
    assert d["risk"]["level"] == "HIGH"


def test_signals_migration_sensitive_lockfile_tests(repo):
    repo.commit("deps", {"package.json": "{}\n", "package-lock.json": "{}\n", "src/auth/login.py": "x=1\n"})
    repo.write("db/migrations/0002_x.sql", "ALTER TABLE a ADD b int;\n")
    repo.write("package.json", '{"dependencies": {"a": "1"}}\n')
    repo.write("src/auth/login.py", "x=2\n")
    repo.write(".github/workflows/ci.yml", "on: push\n")
    _, d, _ = xray(repo.path, "--untracked-all")
    s = d["signals"]
    assert s["migrations"]["count"] == 1 and s["ci"]["count"] == 1 and s["infra"]["count"] >= 1
    assert s["dependency_drift"]["manifest_without_lockfile"][0]["manifest"] == "package.json"
    assert s["tests"]["potential_missing_tests"] is True and "0 test files" in s["tests"]["reasoning"]
    assert {"migration", "sensitive-paths", "lockfile-drift"} <= set(ids(d))
    assert {"review-migration", "sync-lockfile", "add-tests"} <= {x["id"] for x in d["recommendation"]}
    assert d["clusters"]["items"]


def test_tests_changed_clears_missing_tests(repo):
    repo.write("src/mod.py", "x=1\n")
    repo.write("tests/test_mod.py", "def test_x(): pass\n")
    _, d, _ = xray(repo.path, "--untracked-all")
    assert d["signals"]["tests"]["potential_missing_tests"] is False


def test_high_churn(repo):
    for i in range(4):
        repo.commit(f"hot {i}", {"hot.py": f"v={i}\n"})
    _, d, _ = xray(repo.path)
    top = d["high_churn"]["files"][0]
    assert top["path"] == "hot.py" and top["commits"] == 4


def test_clusters_atomic_opportunities(repo):
    repo.write("backend/api.py", "x=1\n"); repo.write("docs/guide.md", "hi\n"); repo.write("web/app.tsx", "export {}\n")
    _, d, _ = xray(repo.path, "--untracked-all")
    assert len(d["clusters"]["items"]) >= 2
    assert len(d["clusters"]["atomic_commit_opportunities"]) == len(d["clusters"]["items"])
    for c in d["clusters"]["items"]:
        assert {"id", "label", "kind", "paths", "added", "deleted"} <= set(c)


def test_xray_is_read_only(make_repo, monkeypatch):
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")  # the snapshot's own `git status` must not refresh the index
    r = make_repo().seed(2)
    r.write("a.py", "x=1\n"); r.git("add", "a.py")
    r.write("f0.txt", "mod\n"); r.write("secrets/.env", "T=1\n")
    r.git("stash", "list")
    before = snapshot(r)
    time.sleep(0.05)
    xray(r.path); xray(r.path, "--untracked-all")
    assert snapshot(r) == before
    assert not (r.path / ".git" / "git-warp").exists()
    # also while in a conflicted merge
    r2 = make_repo().seed(1); r2.merge_conflict()
    b2 = snapshot(r2)
    xray(r2.path)
    assert snapshot(r2) == b2


def test_cli_subprocess_json(repo):
    repo.write("a.txt", "1\n")
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "xray", "--repo", str(repo.path)], capture_output=True, text=True)
    assert p.returncode == 0 and p.stderr == ""
    assert json.loads(p.stdout)["command"] == "xray"
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "xray"], capture_output=True, text=True, cwd=repo.path)
    assert p.returncode == 0 and json.loads(p.stdout)["state"]["branch"] == "main"
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "xray"], capture_output=True, text=True, cwd=repo.path.parent)
    assert p.returncode == 2 and "error" in json.loads(p.stdout) and "Traceback" not in p.stderr


def _bulk_repo(r: RepoBuilder, commits=300, files=500):
    """300 commits touching 500 distinct files (+ one hot file), built with fast-import."""
    stream = []
    for i in range(commits):
        body = f"commit {i}"
        stream.append(f"commit refs/heads/main\ncommitter T <t@e.com> {1_700_000_000 + i * 60} +0000\ndata {len(body)}\n{body}")
        names = [f"src/mod{n % 20}/file{n}.py" for n in range(i * 2, i * 2 + 2) if n < files]
        for nm in names + ["src/hot.py"]:
            content = f"v{i}\n"
            stream.append(f"M 100644 inline {nm}\ndata {len(content)}\n{content}")
        stream.append("")
    # remaining files in one extra commit so the tree has exactly `files` + hot file
    present = {f"src/mod{n % 20}/file{n}.py" for n in range(0, commits * 2) if n < files}
    body = "rest"
    stream.append(f"commit refs/heads/main\ncommitter T <t@e.com> {1_700_000_000 + commits * 60} +0000\ndata {len(body)}\n{body}")
    for n in range(files):
        nm = f"src/mod{n % 20}/file{n}.py"
        if nm not in present:
            stream.append(f"M 100644 inline {nm}\ndata 2\nx\n")
    r.git("fast-import", "--quiet", input="\n".join(stream) + "\n")
    r.git("reset", "-q", "--hard", "main")


def test_performance_sanity(make_repo):
    r = make_repo()
    _bulk_repo(r)
    assert int(r.git("rev-list", "--count", "HEAD")) >= 300
    assert len(r.git("ls-files").splitlines()) >= 500
    for i in range(40):
        r.write(f"src/mod{i % 20}/file{i}.py", "changed\n")
    t = time.time()
    code, d, _ = xray(r.path)
    elapsed = time.time() - t
    assert code == 0 and elapsed < 5, elapsed
    assert d["high_churn"]["files"] and d["working_tree"]["unstaged"]["count"] == 40
