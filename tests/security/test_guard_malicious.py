"""Hostile / malformed / huge inputs: the guard must terminate quickly, never raise, and fail safe."""
import random
import string
import time

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import MAX_COMMAND_CHARS, classify_command


def run(cmd, branch="feature/x", cfg=None):
    t0 = time.monotonic()
    v = classify_command(cmd, cfg or Config(), branch)
    assert time.monotonic() - t0 < 3.0, "classification too slow"
    assert v.decision in ("defer", "ask", "deny")
    return v


def test_very_long_git_command_asks():
    v = run("git status " + "x " * 20000)
    assert v.decision == "ask" and v.rule == "too-complex"


def test_very_long_non_git_command_allowed():
    assert run("echo " + "a" * 100000).decision == "defer"


def test_just_under_limit_is_analysed():
    cmd = "echo " + "a" * (MAX_COMMAND_CHARS - 40) + "; git reset --hard"
    assert run(cmd).decision == "deny"


@pytest.mark.parametrize("n", [30, 100, 1000])
def test_deeply_nested_substitutions_terminate(n):
    cmd = "echo " + "$(" * n + "git reset --hard" + ")" * n
    v = run(cmd)
    assert v.decision in ("ask", "deny")


def test_moderately_nested_substitution_is_still_found():
    cmd = "echo " + "$(" * 10 + "git reset --hard" + ")" * 10
    assert run(cmd).rule == "reset-hard"


@pytest.mark.parametrize("n", [30, 200, 5000])
def test_deeply_nested_subshells_and_braces(n):
    run("(" * n + "git reset --hard" + ")" * n)
    run("{ " * n + "git reset --hard" + "; }" * n)
    run("bash -c '" * 3 + "git reset --hard" + "'" * 3)


def test_deeply_nested_backticks():
    s = "git reset --hard"
    for _ in range(9):
        s = "echo `" + s.replace("\\", "\\\\").replace("`", "\\`") + "`"
    assert run(s).decision in ("ask", "deny")


def test_nested_bash_c_recursion_is_bounded():
    s = "git reset --hard"
    for _ in range(5):
        s = "bash -c " + "'" + s.replace("'", "'\"'\"'") + "'"
    v = run(s)
    assert v.decision in ("ask", "deny")


def test_eval_chain_is_bounded():
    s = "git reset --hard"
    for _ in range(5):
        s = "eval '" + s.replace("'", "'\"'\"'") + "'"
    assert run(s).decision in ("ask", "deny")
    assert run("eval " * 60 + "git reset --hard").decision in ("ask", "deny")
    assert run("eval " * 5 + "git reset --hard").rule == "reset-hard"


@pytest.mark.parametrize("cmd", [
    "git reset --hard '", "git reset --hard \"", "git reset --hard $(", "git reset --hard `", "git reset --hard (", "git reset --hard )",
    "git reset --hard ${", "git reset --hard \\", "git reset --hard $'", "(git reset --hard", "git reset --hard)", "{ git reset --hard",
    "git clean -fd <<", "git clean -fd <<EOF",
])
def test_unbalanced_constructs_still_detect_destructive_git(cmd):
    assert run(cmd).decision in ("deny", "ask")


def test_nul_bytes_do_not_hide_commands():
    assert run("git\x00reset --hard").decision == "deny"
    assert run("echo a\x00; git clean -fd").decision == "deny"
    assert run("\x00" * 100).decision == "defer"


@pytest.mark.parametrize("cmd", [
    "gіt reset --hard",           # cyrillic i: not git
    "git reset －－hard",       # fullwidth hyphens
    "git reset --hard",            # nbsp is part of the word in a shell
    "\U0001f600 git status", "git commit -m '\U0001f600‮ reset --hard'",
])
def test_unicode_lookalikes_do_not_crash_and_do_not_false_positive(cmd):
    assert run(cmd).decision == "defer"


@pytest.mark.parametrize("cmd", [
    "git rеset --hard",                 # cyrillic e: not a git subcommand
    "git re​set --hard",           # zero width space inside word
])
def test_unicode_lookalike_subcommands_ask_as_possible_aliases(cmd):
    """Changed from defer: an unrecognised subcommand may be a configured alias, so it is ASK (never deny: it is not real reset)."""
    assert run(cmd).decision == "ask"


def test_unicode_bidi_does_not_hide_real_command():
    assert run("echo ‮; git reset --hard").decision == "deny"


def test_wide_input_many_commands_is_fast():
    cmd = " && ".join(["git status"] * 2000)
    assert len(cmd) < MAX_COMMAND_CHARS * 2
    run(cmd[:MAX_COMMAND_CHARS - 1] + "; git reset --hard") if len(cmd) >= MAX_COMMAND_CHARS else run(cmd)


def test_quote_bomb_is_linear():
    run("echo " + "'a' " * 4000)
    run("echo " + "\"$(echo a)\" " * 800)
    run("echo " + "`echo a` " * 800)


def test_escape_bomb():
    run("git status " + "\\" * 15000)
    run("echo " + "\\\n" * 5000 + "git reset --hard")


def test_non_string_input():
    for bad in (None, 5, [], b"git reset --hard"):
        assert classify_command(bad, Config(), None).decision == "defer"  # type: ignore[arg-type]


def test_hostile_config_values_do_not_break_matching():
    cfg = Config(protected_branches=["[", "*", ""])
    assert run("git push -f origin foo", cfg=cfg).decision in ("ask", "deny")
    cfg = Config(protected_branches=[])
    assert run("git push -f origin main", cfg=cfg).decision == "ask"


def test_branch_with_weird_characters():
    assert run("git push -f", branch="weird'; rm -rf /").decision == "ask"
    assert run("git push -f", branch="main\n").decision == "ask"


def test_destructive_git_hidden_in_every_nesting_style():
    inner = "git clean -fdx"
    for wrap in ["({})", "$({})", "`{}`", "bash -c '{}'", "sh -c \"{}\"", "eval '{}'", "echo a; {}", "true && {}", "false || {}",
                 "echo x | {}", "env A=b {}", "sudo {}", "xargs {}", "find . -exec {} \\;", "time {}", "! {}", "if true; then {}; fi",
                 "{{ {}; }}", "FOO=1 {}", "nohup {} &", "command {}", "exec {}"]:
        cmd = wrap.replace("{}", inner) if "{{" not in wrap else "{ " + inner + "; }"
        assert run(cmd).rule == "clean-force", cmd


def test_randomized_inputs_never_crash():
    rng = random.Random(1234)
    alphabet = ["git", "reset", "--hard", "push", "-f", "--force", "main", "clean", "-fd", "checkout", ".", "--", "'", '"', "`", "$(", ")",
                "(", "{", "}", ";", "&&", "||", "|", "\n", " ", "\\", "$", "${", "<<", "<<<", ">", "2>&1", "#", "bash -c", "eval", "sudo",
                "env", "xargs", "$'", "*", "[", "\x00", "‮", "-c", "=", "x=y", "rm -rf .git", "stash", "drop", "rebase", "-C", "/tmp"]
    for _ in range(3000):
        cmd = " ".join(rng.choice(alphabet) for _ in range(rng.randint(1, 25)))
        run(cmd)
    for _ in range(1500):
        n = rng.randint(1, 120)
        cmd = "".join(rng.choice(string.printable + "é‮\x00") for _ in range(n))
        run(cmd)


def test_randomized_known_destructive_stay_denied_under_whitespace_noise():
    rng = random.Random(99)
    for _ in range(300):
        ws = lambda: rng.choice([" ", "  ", "\t", " \\\n "])  # noqa: E731
        pre = rng.choice(["", "echo a; ", "env X=1 ", "sudo ", "( ", "true && ", "{ ", "command "])
        post = rng.choice(["", " ; echo done", " 2>&1", " > /dev/null", " | cat", " )"])
        if pre == "( " and not post.endswith(")"):
            post += " )"
        if pre == "{ ":
            post += " ; }"
        cmd = f"{pre}git{ws()}reset{ws()}--hard{post}"
        if pre == "( " and post == " )":
            cmd = "( git reset --hard )"
        assert run(cmd).decision == "deny", repr(cmd)
