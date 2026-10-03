"""Integration coverage for the read-only forensics services."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.git_warp.forensics import analyze_history, bisect_preflight, discover_recovery


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=cwd, text=True, capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "Test User", "GIT_AUTHOR_EMAIL": "test@example.invalid",
             "GIT_COMMITTER_NAME": "Test User", "GIT_COMMITTER_EMAIL": "test@example.invalid"},
    )
    return result.stdout.strip()


def git_status(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args), cwd=cwd, text=True, capture_output=True, check=False,
        env={**os.environ, "GIT_AUTHOR_NAME": "Test User", "GIT_AUTHOR_EMAIL": "test@example.invalid",
             "GIT_COMMITTER_NAME": "Test User", "GIT_COMMITTER_EMAIL": "test@example.invalid"},
    )


class ForensicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.invalid")
        self.file = self.repo / "sample.txt"
        self.file.write_text("first\n", encoding="utf-8")
        git(self.repo, "add", "sample.txt")
        git(self.repo, "commit", "-q", "-m", "Add sample file")
        self.good = git(self.repo, "rev-parse", "HEAD")
        self.file.write_text("first\nsecond\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "Fix parser regression")
        self.bad = git(self.repo, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_recovery_discovers_branches_stashes_reflogs_without_mutating(self) -> None:
        self.file.write_text("temporary work\n", encoding="utf-8")
        git(self.repo, "stash", "push", "-m", "save experimental work")
        before_head = git(self.repo, "rev-parse", "HEAD")
        report = discover_recovery(self.repo)
        self.assertTrue(any(item.kind == "branch" and item.name == "refs/heads/main" for item in report.candidates))
        self.assertTrue(any(item.kind == "stash" and item.subject.endswith("save experimental work") for item in report.candidates))
        self.assertTrue(any(item.kind == "reflog" for item in report.candidates))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname)", "refs/heads"), "refs/heads/main")

    def test_recovery_finds_unreachable_commit(self) -> None:
        lost = git(self.repo, "commit-tree", self.bad + "^{tree}", "-m", "unreferenced candidate")
        report = discover_recovery(self.repo)
        found = [item for item in report.candidates if item.kind == "unreachable" and item.oid == lost]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].subject, "unreferenced candidate")

    def test_recovery_keeps_head_reflog_evidence_after_branch_deletion(self) -> None:
        git(self.repo, "checkout", "-qb", "lost-work")
        self.file.write_text("recovered feature\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "Add work from deleted branch")
        deleted_tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "branch", "-D", "lost-work")

        report = discover_recovery(self.repo)
        evidence = [candidate for candidate in report.candidates if candidate.oid == deleted_tip]
        self.assertTrue(evidence, "deleted branch tip should remain discoverable from HEAD reflog or fsck")
        self.assertTrue(any(candidate.kind in {"reflog", "unreachable"} for candidate in evidence))
        self.assertTrue(any("Add work from deleted branch" in candidate.subject for candidate in evidence))
        self.assertFalse(any(candidate.name == "refs/heads/lost-work" for candidate in report.candidates))

    def test_archaeology_reports_path_timeline_with_evidence_certainty(self) -> None:
        report = analyze_history(self.repo, path="sample.txt")
        self.assertEqual([item.subject for item in report.commits], ["Fix parser regression", "Add sample file"])
        self.assertTrue(all(item.oid and item.date and item.evidence for item in report.commits))
        self.assertIn("FACT", {item.certainty for item in report.findings})
        self.assertIn("INFERENCE", {item.certainty for item in report.findings})

    def test_archaeology_query_treats_path_as_literal_after_separator(self) -> None:
        report = analyze_history(self.repo, query="parser", path="sample.txt")
        self.assertEqual([item.subject for item in report.commits], ["Fix parser regression"])
        with self.assertRaises(ValueError):
            analyze_history(self.repo, path="--all")

    def test_bisect_preflight_is_read_only_and_requires_clean_ordered_endpoints(self) -> None:
        before_head = git(self.repo, "rev-parse", "HEAD")
        result = bisect_preflight(self.repo, good=self.good, bad=self.bad, predicate="python -m unittest")
        self.assertTrue(result.ready)
        self.assertEqual(result.good_oid, self.good)
        self.assertEqual(result.bad_oid, self.bad)
        self.assertTrue(any("does not execute" in item for item in result.predicate_guidance))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before_head)
        self.assertFalse((self.repo / ".git" / "BISECT_START").exists())

    def test_bisect_preflight_rejects_dirty_tree_and_invalid_endpoint(self) -> None:
        (self.repo / "dirty.txt").write_text("uncommitted", encoding="utf-8")
        result = bisect_preflight(self.repo, good="not-a-revision", bad=self.bad, predicate="test command")
        self.assertFalse(result.ready)
        self.assertIsNone(result.good_oid)
        self.assertTrue(any(item.level == "worktree" and "has changes" in item.claim for item in result.findings))

    def test_bisect_preflight_rejects_reversed_endpoints_and_missing_predicate(self) -> None:
        result = bisect_preflight(self.repo, good=self.bad, bad=self.good, predicate=" ")
        self.assertFalse(result.ready)
        self.assertTrue(any(item.level == "ancestry" and "not verified" in item.claim for item in result.findings))
        self.assertTrue(any(item.level == "predicate" for item in result.findings))

    def test_bisect_preflight_detects_existing_clean_bisect_session(self) -> None:
        started = git_status(self.repo, "bisect", "start", self.bad, self.good)
        self.assertEqual(started.returncode, 0, started.stderr)
        result = bisect_preflight(self.repo, good=self.good, bad=self.bad, predicate="python -m unittest")
        self.assertFalse(result.ready)
        self.assertTrue(any(marker.startswith("BISECT_") for marker in result.operation_state))
        self.assertTrue(any(item.level == "repository_operation" and "Active bisect" in item.claim for item in result.findings))

    def test_bisect_preflight_detects_conflicted_rebase_state(self) -> None:
        self.file.write_text("feature line\n", encoding="utf-8")
        git(self.repo, "checkout", "-qb", "feature")
        git(self.repo, "commit", "-qam", "Feature conflict change")
        feature = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "main")
        self.file.write_text("main line\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "Main conflict change")
        main_tip = git(self.repo, "rev-parse", "HEAD")
        rebase = git_status(self.repo, "rebase", "feature")
        self.assertNotEqual(rebase.returncode, 0, "conflicting rebase should stop for resolution")

        result = bisect_preflight(self.repo, good=self.good, bad=main_tip, predicate="python -m unittest")
        self.assertFalse(result.ready)
        self.assertTrue(set(result.operation_state) & {"rebase-merge", "rebase-apply", "REBASE_HEAD"})
        self.assertTrue(any(item.level == "repository_operation" and "rebase state" in item.claim for item in result.findings))
        self.assertIn(feature, git(self.repo, "rev-parse", "feature"))


if __name__ == "__main__":
    unittest.main()
