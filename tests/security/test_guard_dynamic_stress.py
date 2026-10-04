"""Performance / termination of the dynamic-expression paths with hostile or huge input."""
import time

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import MAX_COMMAND_CHARS, classify_command

SEV = {"defer": 0, "ask": 1, "deny": 2}


def run(cmd, limit=3.0):
    t0 = time.monotonic()
    out = classify_command(cmd, Config(), "feature/x")
    assert time.monotonic() - t0 < limit, (cmd[:60], time.monotonic() - t0)
    return out


def test_thousands_of_assignments_then_a_destructive_use():
    n = 1000
    cmd = "; ".join(f"V{i}=val{i}" for i in range(n)) + "; F=--hard; git reset $F"
    assert run(cmd).decision == "deny"


def test_long_assignment_chain_terminates():
    cmd = "A0=--hard; " + "; ".join(f"A{i}=$A{i - 1}" for i in range(1, 800)) + "; git reset $A799"
    assert SEV[run(cmd).decision] >= 1


def test_many_substitutions_in_one_destructive_command():
    cmd = "git reset " + " ".join("$(echo x)" for _ in range(800))
    assert SEV[run(cmd).decision] >= 1


def test_many_substitutions_in_read_only_command_defer():
    cmd = "git log " + " ".join("$(git rev-parse HEAD)" for _ in range(800))
    assert run(cmd).decision == "defer"


def test_deeply_nested_substitution_fails_safe_for_git():
    cmd = "git reset " + "$(echo " * 200 + "--hard" + ")" * 200
    assert SEV[run(cmd).decision] >= 1


def test_many_redirect_writes_then_run():
    cmd = "; ".join(f"echo x > f{i}.sh" for i in range(900)) + "; bash f899.sh"
    assert run(cmd).decision == "ask"


def test_huge_variable_value_is_not_expanded_quadratically():
    big = "x" * 15000
    assert run(f"V={big}; git push origin $V").decision == "defer"
    assert SEV[run(f"V='--{big}'; git clean $V").decision] >= 1


def test_huge_command_mentioning_git_fails_safe():
    assert run("git status " + "$(echo a) " * (MAX_COMMAND_CHARS // 8)).decision == "ask"


@pytest.mark.parametrize("cmd", [
    "bash <(" * 100 + "echo git" + ")" * 100,
    "source /dev/stdin <<< " + "'" + "git status; " * 2000 + "'",
    "x=" + "$(" * 50 + ")" * 50 + "; git reset $x",
    "echo '" + "git reset --hard' > g.sh; bash g.sh " * 200,
    "for a in 1 2 3; do " * 50 + "git reset $a" + "; done" * 50,
])
def test_hostile_shapes_terminate_quickly(cmd):
    run(cmd, limit=4.0)
