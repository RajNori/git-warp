import math

from tests.unit._history_helpers import run_cli, snapshot


def linear(repo, n=8):
    shas = [repo.commit(f"step {i}", {"v.txt": f"{i}\n"}) for i in range(n)]
    return shas


def test_plan_ready(repo):
    shas = linear(repo, 8)
    before = snapshot(repo)
    code, out = run_cli("bisect", "plan", "--good", shas[0], "--bad", "HEAD", "--test", "pytest -x tests/test_a.py", "--repo", repo.path)
    assert code == 0 and out["ready"] is True and out["executed"] is False
    assert out["blockers"] == []
    assert out["range"]["commits_in_range"] == 7
    assert out["range"]["expected_steps"] == math.ceil(math.log2(7))
    cmds = [c["run"] for c in out["commands"]]
    assert cmds[0].startswith("git worktree add --detach ")
    assert any(c.startswith("git bisect start ") for c in cmds)
    assert any(c.startswith("git bisect run sh -c ") and "pytest -x tests/test_a.py" in c for c in cmds)
    assert "git bisect reset" in cmds
    assert out["test"]["valid"] is True and out["test"]["executed"] is False
    assert out["predicate_guidance"]["exit_codes"]["125"].startswith("SKIP")
    assert snapshot(repo) == before
    assert repo.git("worktree", "list").count("\n") == 0       # no worktree created
    assert not (repo.path / ".git" / "BISECT_LOG").exists()   # no bisect started


def test_test_command_never_executed(repo, tmp_path):
    shas = linear(repo, 3)
    marker = tmp_path / "ran"
    _, out = run_cli("bisect", "plan", "--good", shas[0], "--bad", "HEAD", "--test", f"touch {marker}", "--repo", repo.path)
    assert out["ready"] and not marker.exists()


def test_invalid_test_command(repo):
    shas = linear(repo, 3)
    for t in ("", "   ", "a\nb"):
        _, out = run_cli("bisect", "plan", "--good", shas[0], "--test", t, "--repo", repo.path)
        assert out["ready"] is False and out["blockers"][0]["code"] == "invalid_test_command"


def test_dirty_tree_blocks(repo):
    shas = linear(repo, 3)
    repo.write("v.txt", "dirty\n")
    code, out = run_cli("bisect", "plan", "--good", shas[0], "--bad", "HEAD", "--repo", repo.path)
    assert code == 0 and out["ready"] is False and out["commands"] is None
    b = out["blockers"][0]
    assert b["code"] == "dirty_working_tree" and "v.txt" in b["files"]
    assert any("git stash push -u" in s for s in b["suggest"]) and any("git worktree add" in s for s in b["suggest"])


def test_untracked_only_is_warning(repo):
    shas = linear(repo, 3)
    repo.write("scratch.txt", "x\n")
    _, out = run_cli("bisect", "plan", "--good", shas[0], "--repo", repo.path)
    assert out["ready"] and any("untracked" in w for w in out["warnings"])


def test_not_ancestor(repo):
    base = repo.commit("base", {"b.txt": "b\n"})
    repo.branch("other", checkout=True)
    other = repo.commit("other work", {"o.txt": "o\n"})
    repo.checkout("main")
    repo.commit("main work", {"m.txt": "m\n"})
    _, out = run_cli("bisect", "plan", "--good", "other", "--bad", "main", "--repo", repo.path)
    assert out["ready"] is False
    b = out["blockers"][0]
    assert b["code"] == "good_not_ancestor_of_bad" and b["merge_base"] == base
    # swapped refs get a specific hint
    _, out = run_cli("bisect", "plan", "--good", "HEAD", "--bad", base, "--repo", repo.path)
    assert "swapped" in out["blockers"][0]["message"]
    _, out = run_cli("bisect", "plan", "--good", "main", "--bad", "main", "--repo", repo.path)
    assert out["blockers"][0]["code"] == "good_equals_bad"


def test_unknown_ref_and_usage(repo):
    linear(repo, 2)
    _, out = run_cli("bisect", "plan", "--good", "nonexistent", "--repo", repo.path)
    assert out["ready"] is False and out["blockers"][0]["code"] == "unknown_ref"
    code, out = run_cli("bisect", "plan", "--repo", repo.path)
    assert code == 2 and "error" in out
    code, out = run_cli("bisect", "plan", "--good", "--evil", "--repo", repo.path)
    assert code == 2
    code, out = run_cli("bisect", "--repo", repo.path)
    assert code == 2 and "error" in out


def test_merge_commits_warning(repo):
    base = repo.commit("base", {"b.txt": "b\n"})
    repo.branch("side", checkout=True)
    repo.commit("side", {"s.txt": "s\n"})
    repo.checkout("main")
    repo.commit("main", {"m.txt": "m\n"})
    repo.git("merge", "--no-ff", "-m", "merge side", "side")
    _, out = run_cli("bisect", "plan", "--good", base, "--bad", "HEAD", "--repo", repo.path)
    assert out["ready"] and out["range"]["merge_commits"] == 1
    assert out["range"]["merge_commit_samples"][0]["subject"] == "merge side"
    assert any("--first-parent" in w for w in out["warnings"])


def test_reproducibility_risks(repo):
    good = repo.commit("good", {"app.py": "1\n"})
    repo.commit("deps", {"package-lock.json": "{}\n", "package.json": "{}\n"})
    repo.commit("db", {"db/migrations/001_init.sql": "create table t;\n", "schema.sql": "x\n"})
    _, out = run_cli("bisect", "plan", "--good", good, "--repo", repo.path)
    cats = {r["category"]: r for r in out["reproducibility_risks"]}
    assert "package-lock.json" in cats["lockfile"]["files"]
    assert "package.json" in cats["manifest"]["files"]
    assert "db/migrations/001_init.sql" in cats["migration"]["files"]
    assert "schema.sql" in cats["schema"]["files"]


def test_operation_in_progress_blocks(repo):
    repo.merge_conflict()
    _, out = run_cli("bisect", "plan", "--good", "HEAD", "--repo", repo.path)
    codes = {b["code"] for b in out["blockers"]}
    assert "operation_in_progress" in codes


def test_unborn_and_non_repo(empty_repo, tmp_path):
    code, out = run_cli("bisect", "plan", "--good", "x", "--repo", empty_repo.path)
    assert code == 0 and out["ready"] is False and out["blockers"][0]["code"] == "unborn_repository"
    code, out = run_cli("bisect", "status", "--repo", empty_repo.path)
    assert code == 0 and out["in_progress"] is False
    code, out = run_cli("bisect", "plan", "--good", "x", "--repo", tmp_path)
    assert code == 2 and "error" in out


def test_shallow_warning(make_repo):
    import subprocess
    src = make_repo("src")
    shas = linear(src, 4)
    sh = make_repo("sh", init=False)
    subprocess.run(["git", "clone", "-q", "--depth", "2", "file://" + str(src.path), str(sh.path)], check=True)
    head_prev = sh.git("rev-parse", "HEAD~1")
    _, out = run_cli("bisect", "plan", "--good", head_prev, "--repo", sh.path)
    assert any("shallow" in w for w in out["warnings"])


def test_status_not_in_progress(repo):
    linear(repo, 2)
    code, out = run_cli("bisect", "status", "--repo", repo.path)
    assert code == 0 and out["in_progress"] is False


def test_status_in_progress_and_finished(repo):
    shas = linear(repo, 8)      # culprit will be shas[4]
    repo.git("bisect", "start")
    repo.git("bisect", "bad", shas[7])
    repo.git("bisect", "good", shas[0])
    before = snapshot(repo)
    code, out = run_cli("bisect", "status", "--repo", repo.path)
    assert code == 0 and out["in_progress"] is True and out["phase"] == "searching"
    assert out["remaining"]["candidates"] == 7
    assert out["marked"]["bad"] == shas[7] and out["marked"]["good"] == [shas[0]]
    assert snapshot(repo) == before

    def good_or_bad():
        v = int((repo.path / "v.txt").read_text())
        repo.git("bisect", "bad" if v >= 4 else "good", check=False)

    for _ in range(6):
        if (repo.path / ".git" / "BISECT_LOG").read_text().find("first bad commit") != -1:
            break
        good_or_bad()
    code, out = run_cli("bisect", "status", "--repo", repo.path)
    assert out["phase"] == "finished"
    assert out["culprit"]["sha"] == shas[4] and "v.txt" in out["culprit"]["files"]
    assert [c["sha"] for c in out["follow_up_commits_touching_same_files"]][:1] == [shas[7]]
    assert not any("revert" in s and "git revert" in s for s in out["next_steps"])
    assert repo.sha() == shas[4]   # status did not move HEAD
