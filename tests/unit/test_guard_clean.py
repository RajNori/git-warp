"""`git clean`: every option order / bundling follows real Git semantics (dry-run wins; -f without -n/-i deletes)."""
import itertools
import os
import subprocess

import pytest

from gitwarp.core.config import Config
from gitwarp.safety.classifier import classify_command

# each entry: (argv fragments, dry, force, interactive)
OPTS = [(["-n"], 1, 0, 0), (["--dry-run"], 1, 0, 0), (["-f"], 0, 1, 0), (["--force"], 0, 1, 0), (["-d"], 0, 0, 0), (["-x"], 0, 0, 0),
        (["-e", "pat"], 0, 0, 0), (["--exclude=pat"], 0, 0, 0), (["-i"], 0, 0, 1), (["--interactive"], 0, 0, 1), (["-fd"], 0, 1, 0),
        (["-nf"], 1, 1, 0), (["-fdn"], 1, 1, 0), (["-dnf"], 1, 1, 0), (["-ndf"], 1, 1, 0), (["-fdx"], 0, 1, 0), (["-q"], 0, 0, 0)]


def expected(combo):
    dry = any(o[1] for o in combo)
    force = any(o[2] for o in combo)
    inter = any(o[3] for o in combo)
    return "deny" if force and not dry and not inter else "defer"


def decide(args):
    return classify_command("git clean " + " ".join(args), Config(), "feature/x")


def _cases(sizes=(1, 2)):
    seen = set()
    for r in sizes:
        for combo in itertools.permutations(OPTS, r):
            args = tuple(a for o in combo for a in o[0])
            if args in seen:
                continue
            seen.add(args)
            yield pytest.param(list(args), expected(combo), id=" ".join(args))


@pytest.mark.parametrize("args,want", list(_cases()))
def test_every_option_order_follows_git_semantics(args, want):
    got = decide(args)
    if want == "deny":
        assert got.decision == "deny" and got.rule == "clean-force", args
    else:
        assert got.decision != "deny", args          # dry runs / interactive / no force: never DENY


def test_triple_option_permutations_follow_git_semantics():
    for param in _cases(sizes=(3,)):
        args, want = param.values
        assert (decide(args).decision == "deny") == (want == "deny"), args


@pytest.mark.parametrize("cmd", ["git clean -nfd", "git clean -n -f -d", "git clean -fdn", "git clean --dry-run -fd", "git clean -dnf", "git clean -ndf",
                                  "git clean -f -n", "git clean --force --dry-run", "git clean -fd --dry-run", "git clean -nxfd", "git clean -fdx -n"])
def test_dry_runs_defer(cmd):
    assert classify_command(cmd, Config(), "main").decision == "defer", cmd


@pytest.mark.parametrize("cmd", ["git clean -fd", "git clean -xfd", "git clean -f -d", "git clean --force -d", "git clean -f", "git clean -fdx",
                                  "git clean -d -f -x", "git clean --force --force", "git clean -ffd", "git clean -fd -e '*.log'",
                                  "git clean -fe n", "git clean -f -e n", "git clean -nx; git clean -fd", "git clean -fd -- -n"])
def test_real_clean_is_denied(cmd):
    assert classify_command(cmd, Config(), "main").decision == "deny", cmd


@pytest.mark.parametrize("cmd", ["git -C sub clean -fd", "git --no-pager clean -fd", "sudo git clean -fd", "env X=1 git clean -fd", "(git clean -fd)",
                                  "true && git clean -nfd && git clean -fd", "git clean -fd;"])
def test_wrapped_clean_still_denied_when_real(cmd):
    assert classify_command(cmd, Config(), "main").decision == "deny", cmd


def test_cross_check_against_real_git(tmp_path):
    """Real Git agrees: whenever the classifier does not DENY, nothing is deleted; whenever it DENIES, something is."""
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")

    def g(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)

    g("init", "-q", ".")
    (tmp_path / "a").write_text("x")
    g("add", "a")
    g("-c", "user.email=a@b", "-c", "user.name=n", "commit", "-qm", "i")
    seen = set()
    for r in (1, 2):
        for combo in itertools.permutations([o for o in OPTS if o[0] != ["-x"] or True], r):
            args = [a for o in combo for a in o[0]]
            if tuple(args) in seen:
                continue
            seen.add(tuple(args))
            (tmp_path / "u1").write_text("u")
            (tmp_path / "ud").mkdir(exist_ok=True)
            (tmp_path / "ud" / "f").write_text("u")
            (tmp_path / "x.pat").write_text("u")
            g("clean", *args)
            deleted = not (tmp_path / "u1").exists() or not (tmp_path / "ud" / "f").exists() or not (tmp_path / "x.pat").exists()
            got = decide(args).decision
            assert (got == "deny") == deleted, (args, got, deleted)
