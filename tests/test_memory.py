from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from git_warp.memory import RepositoryIndex, record_event, redact_text  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="git-warp-memory-")
        self.root = Path(self.temp.name).resolve()
        git(self.root, "init", "--quiet")
        git(self.root, "config", "user.name", "Memory Test")
        git(self.root, "config", "user.email", "memory@example.invalid")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, message: str, files: dict[str, str]) -> str:
        for name, value in files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")
        git(self.root, "add", "--all")
        git(self.root, "commit", "--quiet", "-m", message)
        return git(self.root, "rev-parse", "HEAD").strip()

    def test_incremental_index_and_queries_store_only_history_metadata(self) -> None:
        first = self.commit("add service", {"src/service.py": "value = 1\n", "tests/test_service.py": "ok\n"})
        self.commit("adjust service", {"src/service.py": "value = 2\n"})

        index = RepositoryIndex(self.root)
        partial = index.index_history(batch_size=1)
        self.assertEqual(partial["indexed"], 1)
        self.assertEqual(partial["remaining"], 1)
        done = index.index_history(batch_size=10)
        self.assertEqual(done["indexed"], 1)
        self.assertEqual(done["remaining"], 0)
        self.assertEqual(index.index_history()["indexed"], 0)

        hotspots = index.hotspots()
        self.assertEqual(hotspots[0], {"path": "src/service.py", "commits": 2})
        self.assertEqual(index.cochanges("src/service.py")[0]["path"], "tests/test_service.py")
        history = index.commit_history("src/service.py")
        self.assertEqual(len(history), 2)
        self.assertIn(first[:12], {row["sha"][:12] for row in history})
        self.assertTrue((self.root / ".git" / "git-warp" / "warp.db").exists())

    def test_recorder_excludes_raw_tool_input_and_sensitive_paths(self) -> None:
        self.commit("initial", {"src/main.py": "ok\n"})
        (self.root / "src" / "main.py").write_text("changed\n", encoding="utf-8")
        (self.root / ".env").write_text("API_TOKEN=ghp_123456789012345678901234567890\n", encoding="utf-8")
        event = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"prompt": "do not retain this", "new_string": "secret=topsecret"},
            "cwd": str(self.root),
        }
        self.assertTrue(record_event(self.root, event))
        recorder = self.root / ".git" / "git-warp" / "flight-recorder.jsonl"
        line = recorder.read_text(encoding="utf-8")
        payload = json.loads(line)
        self.assertEqual(payload["tool_category"], "file_edit")
        self.assertEqual(payload["changed_paths"], ["src/main.py"])
        self.assertNotIn("tool_input", payload)
        self.assertNotIn("topsecret", line)
        self.assertNotIn(".env", line)

    def test_redaction_catches_common_token_and_assignment_forms(self) -> None:
        clean = redact_text("password=hunter2 ghp_123456789012345678901234567890")
        self.assertNotIn("hunter2", clean)
        self.assertNotIn("ghp_123456789012345678901234567890", clean)
        self.assertIn("[REDACTED]", clean)

    def test_runtime_directory_symlink_is_rejected(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / ".git" / "git-warp").symlink_to(outside, target_is_directory=True)
        event = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(self.root)}
        self.assertFalse(record_event(self.root, event))
        with self.assertRaises(OSError):
            RepositoryIndex(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_runtime_file_symlink_is_not_followed(self) -> None:
        self.commit("initial", {"src/main.py": "ok\n"})
        directory = self.root / ".git" / "git-warp"
        directory.mkdir(mode=0o700)
        outside = self.root / "outside.jsonl"
        outside.write_text("untouched\n", encoding="utf-8")
        (directory / "flight-recorder.jsonl").symlink_to(outside)
        event = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(self.root)}
        self.assertFalse(record_event(self.root, event))
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")


if __name__ == "__main__":
    unittest.main()
