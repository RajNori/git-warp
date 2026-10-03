from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from git_warp.safety.classifier import Decision, classify_command
from git_warp.safety.hook import output_for_event
from git_warp.safety.shell_lexer import lex_command


class ShellLexerTests(unittest.TestCase):
    def test_splits_compound_commands_and_respects_quoted_operators(self):
        parsed = lex_command('echo "hello && world"; git status && git log')
        self.assertEqual(parsed.segments, (("echo", "hello && world"), ("git", "status"), ("git", "log")))
        self.assertFalse(parsed.ambiguous)

    def test_unclosed_quote_is_ambiguous(self):
        parsed = lex_command("git reset --hard 'unfinished")
        self.assertTrue(parsed.ambiguous)
        self.assertIsNotNone(parsed.error)

    def test_newline_separates_commands(self):
        parsed = lex_command("echo ok\ngit status")
        self.assertEqual(parsed.segments, (("echo", "ok"), ("git", "status")))


class ClassifierTests(unittest.TestCase):
    def assertDecision(self, command, decision, code=None):
        result = classify_command(command)
        self.assertIsNotNone(result, command)
        self.assertEqual(result.decision, decision, command)
        if code:
            self.assertEqual(result.reason_code, code)
        return result

    def test_clear_destructive_forms_are_denied(self):
        commands = [
            "git reset --hard",
            "git -C repo reset --hard HEAD~2",
            "env MODE=prod /usr/bin/git clean -fd",
            "command git clean -xdf",
            "git clean --force -d",
            "git checkout -- .",
            "git restore .",
            "git restore --worktree -- src/file.py",
            "git reflog expire --all",
            "git gc --prune=now",
            "git gc --prune now",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertDecision(command, Decision.DENY)

    def test_compound_shell_does_not_hide_denial(self):
        self.assertDecision("echo checking; git status || git reset --hard", Decision.DENY)
        self.assertDecision("(git reset --hard)", Decision.DENY)

    def test_wrappers_and_absolute_git_path(self):
        self.assertDecision("env FOO=bar command /usr/bin/git reset --hard", Decision.DENY)
        self.assertDecision("bash -c 'git reset --hard'", Decision.DENY)

    def test_uncertain_wrapper_or_expansion_requires_review(self):
        self.assertDecision("python tool.py git", Decision.ASK, "ambiguous_git_invocation")
        self.assertDecision("$GIT_BIN reset --hard", Decision.ASK)
        self.assertDecision("bash -c 'echo ok; git reset --hard'", Decision.ASK)

    def test_protected_force_push_target_is_denied(self):
        cases = [
            "git push --force origin feature:main",
            "git push -f origin HEAD:refs/heads/main",
            "git push --force-with-lease=main origin x:main",
            "git push +feature:main",
            "git push origin +HEAD:main",
            "git push origin +HEAD:refs/heads/main",
            "git push origin feature:main +other:refs/heads/production",
        ]
        for command in cases:
            with self.subTest(command=command):
                self.assertDecision(command, Decision.DENY, "force_push_protected")

    def test_unprotected_or_unknown_force_push_requires_review(self):
        self.assertDecision("git push --force origin feature:feature", Decision.ASK, "force_push")
        self.assertDecision("git push --force origin HEAD", Decision.ASK, "force_push")
        self.assertDecision("git push -f", Decision.ASK, "force_push")

    def test_other_history_rewrites_and_deletions_require_review(self):
        for command, code in [
            ("git rebase origin/main", "rebase"),
            ("git commit --amend", "commit_amend"),
            ("git branch -D old", "forced_branch_delete"),
            ("git push origin --delete old", "remote_branch_delete"),
        ]:
            with self.subTest(command=command):
                self.assertDecision(command, Decision.ASK, code)

    def test_safe_or_unrelated_commands_defer_to_claude(self):
        for command in ["git status", "git diff --stat", "python -c 'print(1)'", "echo git"]:
            with self.subTest(command=command):
                self.assertIsNone(classify_command(command))

    def test_unknown_git_subcommands_are_reviewed_as_possible_configured_aliases(self):
        self.assertDecision("git deploy", Decision.ASK, "unknown_git_subcommand")
        self.assertDecision("git -c alias.deploy='!git reset --hard' deploy", Decision.ASK, "unknown_git_subcommand")

    def test_parser_limits_fail_toward_review_for_git_text(self):
        self.assertDecision("git status " + "x" * 16_500, Decision.ASK)


class HookTests(unittest.TestCase):
    def test_emits_deny_for_dangerous_bash_command(self):
        result = output_for_event({"tool_name": "Bash", "tool_input": {"command": "git reset --hard"}})
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")

    def test_safe_and_non_bash_events_do_not_auto_allow(self):
        self.assertEqual(output_for_event({"tool_name": "Bash", "tool_input": {"command": "git status"}}), {})
        self.assertEqual(output_for_event({"tool_name": "Read", "tool_input": {}}), {})

    def test_malformed_events_return_ask_not_allow(self):
        for event in [None, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": {"command": 3}}]:
            with self.subTest(event=event):
                output = output_for_event(event)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_launcher_reads_stdin_and_emits_json(self):
        event = {"tool_name": "Bash", "tool_input": {"command": "git push -f origin x:main"}}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "git_guard.py")],
            input=json.dumps(event), text=True, capture_output=True, timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
