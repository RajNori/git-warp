from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.git_warp.analysis import (
    analyze_blast_radius, analyze_pr, analyze_xray, inspect_conflicts,
    propose_commit_groups, temporal_review,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Test User")
        git(self.root, "config", "user.email", "test@example.invalid")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, subject: str) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", subject, "-q")
        return git(self.root, "rev-parse", "HEAD")

    def test_xray_and_pr_include_changed_path_evidence(self) -> None:
        (self.root / "app.py").write_text("print('base')\n")
        base = self.commit("base")
        (self.root / "app.py").write_text("print('changed')\n")
        self.commit("change app")
        (self.root / "new.py").write_text("from app import main\n")
        xray = analyze_xray(self.root, base=base)
        self.assertEqual(set(xray.metadata["paths"]), {"app.py", "new.py"})
        pr = analyze_pr(self.root, base)
        self.assertEqual(pr.metadata["additions"], 1)
        self.assertTrue(any(e.detail == "app.py" for e in pr.evidence))

    def test_commit_cluster_is_proposal_and_has_sha_evidence(self) -> None:
        (self.root / "auth.py").write_text("a = 1\n")
        first = self.commit("fix auth token validation")
        (self.root / "auth.py").write_text("a = 2\n")
        self.commit("improve auth token validation")
        result = propose_commit_groups(self.root, revs="HEAD")
        self.assertTrue(result.proposals)
        self.assertIn(first, result.proposals[0]["commits"])
        self.assertIn("proposal", result.proposals[0]["action"])

    def test_blast_radius_resolves_python_and_relative_ts_imports(self) -> None:
        (self.root / "lib.py").write_text("value = 1\n")
        (self.root / "consumer.py").write_text("from lib import value\n")
        (self.root / "src").mkdir()
        (self.root / "src" / "util.ts").write_text("export const x = 1\n")
        (self.root / "src" / "use.ts").write_text("import { x } from './util'\n")
        self.commit("base")
        (self.root / "lib.py").write_text("value = 2\n")
        (self.root / "src" / "util.ts").write_text("export const x = 2\n")
        result = analyze_blast_radius(self.root)
        self.assertIn("consumer.py", result.metadata["dependants"]["lib.py"])
        self.assertIn("src/use.ts", result.metadata["dependants"]["src/util.ts"])

    def test_conflict_inspection_reads_stages_without_resolution(self) -> None:
        (self.root / "f.txt").write_text("base\n")
        self.commit("base")
        git(self.root, "checkout", "-b", "other", "-q")
        (self.root / "f.txt").write_text("theirs\n")
        self.commit("theirs")
        git(self.root, "checkout", "-", "-q")
        (self.root / "f.txt").write_text("ours\n")
        self.commit("ours")
        result = subprocess.run(["git", "merge", "other"], cwd=self.root, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        state_before = git(self.root, "status", "--porcelain")
        analysis = inspect_conflicts(self.root, base="other")
        self.assertEqual(analysis.metadata["paths"], ("f.txt",))
        self.assertEqual(set(analysis.proposals[0]["available_stages"]), {1, 2, 3})
        self.assertEqual(analysis.proposals[0]["semantic_status"], "unknown")
        self.assertIsNotNone(analysis.metadata["merge_base"])
        self.assertTrue(any(item.source == "ours_only branch commit" for item in analysis.evidence))
        self.assertEqual(git(self.root, "status", "--porcelain"), state_before)

    def test_temporal_history_cites_full_sha(self) -> None:
        (self.root / "a.py").write_text("1\n")
        sha1 = self.commit("initial file")
        (self.root / "a.py").write_text("2\n")
        sha2 = self.commit("update file")
        result = temporal_review(self.root, paths=("a.py",))
        shas = {e.sha for e in result.evidence if e.sha}
        self.assertEqual(shas, {sha1, sha2})
        self.assertTrue(all(len(sha) == 40 for sha in shas))

    def test_xray_nested_root_and_checkout_state(self) -> None:
        nested = self.root / "src" / "nested"
        nested.mkdir(parents=True)
        (self.root / "tracked.py").write_text("value = 1\n")
        self.commit("initial")
        git(self.root, "branch", "upstream")
        git(self.root, "branch", "--set-upstream-to=upstream")
        (self.root / "tracked.py").write_text("value = 2\n")
        git(self.root, "add", "tracked.py")
        (self.root / "tracked.py").write_text("value = 3\n")
        (self.root / "src" / "nested" / ".env.example").write_text("TOKEN=placeholder\n")
        result = analyze_xray(nested)
        self.assertEqual(Path(result.metadata["repository_root"]), self.root.resolve())
        self.assertEqual(result.metadata["branch"], git(self.root, "branch", "--show-current"))
        self.assertIsNotNone(result.metadata["upstream"])
        self.assertTrue(result.metadata["staged"])
        self.assertTrue(result.metadata["unstaged"])
        self.assertTrue(result.metadata["untracked"])
        self.assertTrue(result.metadata["reflog"])
        self.assertTrue(result.metadata["worktrees"])
        self.assertIn("sensitive", result.metadata["signals"])

    def test_pr_emits_diff_backed_risk_and_reviewer_signals(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("value = 1\n")
        base = self.commit("base")
        (self.root / "migrations").mkdir()
        (self.root / "migrations" / "001.sql").write_text("ALTER TABLE items ADD COLUMN flag INT;\n")
        (self.root / "package.json").write_text('{"dependencies":{"example":"1.0"}}\n')
        (self.root / "package-lock.json").write_text('{"lockfileVersion":3}\n')
        (self.root / "generated").mkdir()
        (self.root / "generated" / "api.ts").write_text("export const api = 1\n")
        (self.root / "src" / "app.py").write_text("value = 2\nprint('debug')\n")
        self.commit("add schema, deps, generated API")
        result = analyze_pr(self.root, base)
        for signal in ("migration", "dependency", "lockfile", "generated", "api"):
            self.assertIn(signal, result.metadata["signals"])
        self.assertTrue(result.metadata["reviewer_questions"])
        self.assertIn("migration", result.metadata["rollback_consideration"])
        self.assertTrue(any("Debug" in finding.title for finding in result.findings))

    def test_default_commit_clustering_uses_staged_and_unstaged_hunks(self) -> None:
        (self.root / "code.py").write_text("\n".join(f"item_{i} = {i}" for i in range(30)) + "\n")
        self.commit("base")
        (self.root / "code.py").write_text("\n".join(f"item_{i} = {i + 1 if i in (2, 25) else i}" for i in range(30)) + "\n")
        git(self.root, "add", "code.py")
        # Replace the index with one version while leaving a second change in the worktree.
        (self.root / "code.py").write_text("\n".join(f"item_{i} = {i + 1 if i == 2 else (i + 2 if i == 25 else i)}" for i in range(30)) + "\n")
        result = propose_commit_groups(self.root)
        self.assertEqual(result.metadata["mode"], "working-tree")
        self.assertGreaterEqual(result.metadata["staged_hunks"], 1)
        self.assertGreaterEqual(result.metadata["unstaged_hunks"], 1)
        self.assertTrue(result.proposals)

    def test_temporal_review_reports_revert_target_and_reintroduction_wording(self) -> None:
        (self.root / "value.py").write_text("value = 0\n")
        self.commit("base value")
        (self.root / "value.py").write_text("value = 1\n")
        target = self.commit("change value")
        subprocess.run(["git", "revert", "--no-edit", target], cwd=self.root, text=True, capture_output=True, check=True)
        (self.root / "value.py").write_text("value = 1\n")
        self.commit("reintroduce changed value")
        result = temporal_review(self.root, paths=("value.py",))
        kinds = {item["kind"] for item in result.metadata["revert_signals"]}
        self.assertIn("explicit-revert-target", kinds)
        self.assertIn("reintroduction-wording", kinds)
        self.assertTrue(any(e.sha == target and "reverted commit reference" == e.source for e in result.evidence))


if __name__ == "__main__":
    unittest.main()
