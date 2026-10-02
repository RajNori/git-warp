from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from git_warp import (  # noqa: E402
    ahead_behind,
    blame,
    changed_paths,
    commit_metadata,
    GitCommandError,
    GitResult,
    current_branch,
    diff,
    git_common_dir,
    git_dir,
    head_commit,
    local_branches,
    log,
    merge_base,
    repo_info,
    repo_root,
    remote_branches,
    remotes,
    reflog,
    run_git,
    show,
    staged_paths,
    stash_list,
    status_porcelain,
    untracked_paths,
    unstaged_paths,
    upstream,
    worktree_list,
)


TIMEOUT = 5.0


def setup_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


class GitCoreIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="git-warp-core-")
        self.root = Path(self.tempdir.name).resolve()
        setup_git(self.root, "init", "--quiet")
        setup_git(self.root, "config", "user.name", "Git Warp Test")
        setup_git(self.root, "config", "user.email", "git-warp-test@example.invalid")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_commit(self) -> str:
        (self.root / "tracked.txt").write_text("initial\n", encoding="utf-8")
        setup_git(self.root, "add", "tracked.txt")
        setup_git(self.root, "commit", "--quiet", "-m", "initial")
        return setup_git(self.root, "rev-parse", "HEAD").strip()

    def test_run_git_returns_complete_result_and_keeps_nonzero_as_data(self) -> None:
        result = run_git(("status", "--short"), cwd=self.root, timeout=TIMEOUT)
        self.assertIsInstance(result, GitResult)
        self.assertEqual(result.args, ("git", "status", "--short"))
        self.assertEqual(result.cwd, self.root)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertGreaterEqual(result.duration_ms, 0)

        missing = run_git(("not-a-git-subcommand",), cwd=self.root, timeout=TIMEOUT)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("not-a-git-subcommand", missing.stderr)

    def test_repository_paths_resolve_from_nested_directory(self) -> None:
        self.make_commit()
        nested = self.root / "subdir" / "nested"
        nested.mkdir(parents=True)

        self.assertEqual(repo_root(cwd=nested, timeout=TIMEOUT), self.root)
        self.assertEqual(git_dir(cwd=nested, timeout=TIMEOUT), self.root / ".git")
        self.assertEqual(git_common_dir(cwd=nested, timeout=TIMEOUT), self.root / ".git")

    def test_repository_paths_preserve_trailing_newline_in_directory_name(self) -> None:
        unusual = self.root / "repo\n"
        unusual.mkdir()
        setup_git(unusual, "init", "--quiet")
        self.assertEqual(repo_root(cwd=unusual, timeout=TIMEOUT), unusual)
        self.assertEqual(git_dir(cwd=unusual, timeout=TIMEOUT), unusual / ".git")
        self.assertEqual(git_common_dir(cwd=unusual, timeout=TIMEOUT), unusual / ".git")

    def test_branch_head_and_repo_info(self) -> None:
        expected_head = self.make_commit()

        branch = current_branch(cwd=self.root, timeout=TIMEOUT)
        self.assertTrue(branch)
        self.assertEqual(head_commit(cwd=self.root, timeout=TIMEOUT), expected_head)

        info = repo_info(cwd=self.root, timeout=TIMEOUT)
        self.assertEqual(info.root, self.root)
        self.assertEqual(info.git_dir, self.root / ".git")
        self.assertEqual(info.common_dir, self.root / ".git")
        self.assertEqual(info.branch, branch)
        self.assertEqual(info.head, expected_head)

    def test_unborn_repository_has_no_head(self) -> None:
        self.assertIsNone(head_commit(cwd=self.root, timeout=TIMEOUT))
        self.assertIsNone(repo_info(cwd=self.root, timeout=TIMEOUT).head)

    def test_checked_repository_query_raises_typed_error_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="not-a-repo-") as other:
            with self.assertRaises(GitCommandError) as caught:
                repo_root(cwd=Path(other), timeout=TIMEOUT)
        self.assertEqual(caught.exception.result.returncode, 128)
        self.assertEqual(caught.exception.cwd, Path(other).resolve())

    def test_status_preserves_porcelain_nul_delimiters(self) -> None:
        self.make_commit()
        (self.root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (self.root / "untracked file.txt").write_text("new\n", encoding="utf-8")

        output = status_porcelain(cwd=self.root, timeout=TIMEOUT)
        self.assertIn(" M tracked.txt\0", output)
        self.assertIn("?? untracked file.txt\0", output)

    def test_changed_path_helpers_separate_staged_unstaged_and_untracked(self) -> None:
        self.make_commit()
        (self.root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (self.root / "staged.txt").write_text("staged\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        setup_git(self.root, "add", "staged.txt")

        entries = changed_paths(cwd=self.root, timeout=TIMEOUT)
        self.assertEqual({entry.path for entry in entries}, {"tracked.txt", "staged.txt", "untracked.txt"})
        self.assertEqual({entry.path for entry in staged_paths(cwd=self.root, timeout=TIMEOUT)}, {"staged.txt"})
        self.assertEqual({entry.path for entry in unstaged_paths(cwd=self.root, timeout=TIMEOUT)}, {"tracked.txt"})
        self.assertEqual({entry.path for entry in untracked_paths(cwd=self.root, timeout=TIMEOUT)}, {"untracked.txt"})

    def test_read_only_history_helpers_return_structured_data(self) -> None:
        first = self.make_commit()
        (self.root / "tracked.txt").write_text("initial\nsecond line\n", encoding="utf-8")
        setup_git(self.root, "add", "tracked.txt")
        setup_git(self.root, "commit", "--quiet", "-m", "second commit")
        second = setup_git(self.root, "rev-parse", "HEAD").strip()

        metadata = commit_metadata(cwd=self.root, timeout=TIMEOUT)
        self.assertEqual(metadata.commit, second)
        self.assertEqual(metadata.subject, "second commit")
        self.assertEqual(metadata.parents, (first,))
        self.assertEqual([item.commit for item in log(cwd=self.root, timeout=TIMEOUT)], [second, first])
        with self.assertRaisesRegex(ValueError, "exceeded max_output_bytes"):
            log(cwd=self.root, timeout=TIMEOUT, max_output_bytes=16)
        self.assertEqual(merge_base(cwd=self.root, timeout=TIMEOUT, left=first, right=second), first)
        self.assertIn("second commit", show(cwd=self.root, timeout=TIMEOUT).stdout)
        self.assertEqual(len(blame(cwd=self.root, timeout=TIMEOUT, path="tracked.txt")), 2)
        self.assertEqual(blame(cwd=self.root, timeout=TIMEOUT, path="tracked.txt", start_line=2)[0].text, "second line")
        self.assertTrue(reflog(cwd=self.root, timeout=TIMEOUT))
        self.assertTrue(any(branch.name == current_branch(cwd=self.root, timeout=TIMEOUT) for branch in local_branches(cwd=self.root, timeout=TIMEOUT)))
        self.assertEqual(stash_list(cwd=self.root, timeout=TIMEOUT), ())
        self.assertEqual(len(worktree_list(cwd=self.root, timeout=TIMEOUT)), 1)

    def test_remote_and_upstream_helpers_use_local_fixture_remote(self) -> None:
        self.make_commit()
        branch = current_branch(cwd=self.root, timeout=TIMEOUT)
        assert branch is not None
        bare = self.root.parent / (self.root.name + "-origin.git")
        setup_git(self.root.parent, "clone", "--quiet", "--bare", str(self.root), str(bare))
        setup_git(self.root, "remote", "add", "origin", str(bare))
        setup_git(self.root, "fetch", "--quiet", "origin")
        setup_git(self.root, "branch", "--set-upstream-to", f"origin/{branch}", branch)

        self.assertEqual(remotes(cwd=self.root, timeout=TIMEOUT), ("origin",))
        self.assertIn(f"origin/{branch}", {item.name for item in remote_branches(cwd=self.root, timeout=TIMEOUT)})
        self.assertEqual(upstream(cwd=self.root, timeout=TIMEOUT), f"origin/{branch}")
        self.assertEqual(ahead_behind(cwd=self.root, timeout=TIMEOUT).ahead, 0)  # type: ignore[union-attr]
        self.assertEqual(ahead_behind(cwd=self.root, timeout=TIMEOUT).behind, 0)  # type: ignore[union-attr]

    def test_diff_caps_output_and_reports_truncation(self) -> None:
        self.make_commit()
        (self.root / "tracked.txt").write_text("x" * 500 + "\n", encoding="utf-8")
        result = diff(cwd=self.root, timeout=TIMEOUT, max_output_bytes=64)
        self.assertEqual(len(result.stdout.encode("utf-8")), 64)
        self.assertTrue(result.stdout_truncated)
        self.assertFalse(result.stderr_truncated)

    def test_stash_helper_returns_metadata_without_applying_stash(self) -> None:
        self.make_commit()
        (self.root / "tracked.txt").write_text("stashed change\n", encoding="utf-8")
        setup_git(self.root, "stash", "push", "--quiet", "-m", "fixture stash")
        entries = stash_list(cwd=self.root, timeout=TIMEOUT)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].subject, "On " + (current_branch(cwd=self.root, timeout=TIMEOUT) or "") + ": fixture stash")
        self.assertEqual((self.root / "tracked.txt").read_text(encoding="utf-8"), "initial\n")


if __name__ == "__main__":
    unittest.main()
