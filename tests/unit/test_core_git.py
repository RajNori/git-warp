import pytest
from gitwarp.core import git


def test_unborn_repo(empty_repo):
    p = empty_repo.path
    assert git.is_git_repository(p)
    assert git.head_sha(p) is None
    assert git.current_branch(p) == "main"
    assert git.reflog("HEAD", cwd=p) == []
    assert git.log_commits(cwd=p) == []
    assert git.upstream(p) is None and git.ahead_behind(p) is None


def test_non_repo(tmp_path):
    assert not git.is_git_repository(tmp_path)
    assert git.repo_root(tmp_path) is None
    with pytest.raises(git.NotARepository):
        git.git_dir(tmp_path)
    with pytest.raises(git.NotARepository):
        git.working_tree_status(tmp_path)


def test_status_parsing_special_paths(repo):
    repo.write("with space.txt", "a").write("dir/üni.txt", "b")
    repo.git("add", "with space.txt")
    repo.write("f0.txt", "changed\n")
    st = {e.path: e for e in git.working_tree_status(repo.path, untracked="all")}
    assert st["with space.txt"].staged and not st["with space.txt"].untracked
    assert st["dir/üni.txt"].untracked
    assert st["f0.txt"].unstaged


def test_rename_status_and_numstat(repo):
    repo.git("mv", "f0.txt", "renamed.txt")
    e = [e for e in git.working_tree_status(repo.path) if e.path == "renamed.txt"][0]
    assert e.orig_path == "f0.txt"
    ns = git.numstat(repo.path, ["--cached", "-M"])
    assert ns and ns[0]["path"]


def test_branch_head_commits(repo):
    assert git.current_branch(repo.path) == "main"
    sha = git.head_sha(repo.path)
    assert len(sha) == 40
    commits = git.log_commits(cwd=repo.path, with_files=True)
    assert [c.subject for c in commits] == ["commit 2", "commit 1", "commit 0"]
    assert commits[0].files == ("f2.txt",)
    assert git.commit_metadata("HEAD", repo.path).sha == sha
    assert git.merge_base("main", "HEAD", repo.path) == sha


def test_detached_and_branches(repo):
    repo.git("checkout", "-q", "--detach")
    assert git.current_branch(repo.path) is None
    assert {b["name"] for b in git.branches(repo.path)} == {"main"}


def test_ahead_behind_with_upstream(make_repo, tmp_path):
    origin = make_repo("origin").seed(2)
    clone = tmp_path / "clone"
    import subprocess
    subprocess.run(["git", "clone", "-q", str(origin.path), str(clone)], check=True)
    from tests.conftest import RepoBuilder  # noqa
    c = RepoBuilder(clone, init=False)
    c.commit("local", {"l.txt": "1"})
    assert git.upstream(clone) == "origin/main"
    assert git.ahead_behind(clone) == (1, 0)


def test_operation_and_conflict_stages(repo):
    assert git.repo_operation(repo.path) is None
    repo.merge_conflict()
    assert git.repo_operation(repo.path) == "merge"
    stages = {s["stage"] for s in git.ls_files_stage(repo.path, unmerged_only=True)}
    assert stages == {1, 2, 3}
    assert any(e.conflicted for e in git.working_tree_status(repo.path))


def test_stash_worktree_reflog(repo):
    repo.write("f0.txt", "dirty\n")
    repo.git("stash")
    assert len(git.stashes(repo.path)) == 1
    wt = repo.path.parent / "wt"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    assert len(git.worktrees(repo.path)) == 2
    assert git.reflog("HEAD", 10, repo.path)


def test_blame_show_and_unsafe_refs(repo):
    b = git.blame("f0.txt", cwd=repo.path)
    assert b and b[0]["summary"] == "commit 0"
    assert git.show_file("HEAD", "f0.txt", repo.path) == "0\n"
    assert git.show_file("HEAD", "missing", repo.path) is None
    with pytest.raises(ValueError):
        git.rev_parse("--upload-pack=evil", repo.path)
    with pytest.raises(ValueError):
        git.log_commits("-p", cwd=repo.path)


def test_timeout_and_missing_git(repo, monkeypatch):
    with pytest.raises(git.GitTimeout):
        git.run(["-c", "alias.slow=!sleep 5", "slow"], cwd=repo.path, timeout=0.2)
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(git.GitNotFound):
        git.run(["--version"])


def test_state_dir_in_git_dir(repo):
    d = git.state_dir(repo.path, create=True)
    assert d.name == "git-warp" and d.parent.name == ".git" and d.is_dir()
    assert git.repo_root(repo.path) == repo.path.resolve() or git.repo_root(repo.path).samefile(repo.path)
