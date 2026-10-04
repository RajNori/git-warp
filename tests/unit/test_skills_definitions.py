"""Skill definitions: frontmatter validity and least-privilege ``allowed-tools``."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"
SKILLS = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
WARP_ENTRY = 'Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/warp.py":*)'

# The only raw git forms a skill may pre-approve.  Every one is a read-only query form: `branch` is limited to its
# listing flags, `stash`/`worktree`/`reflog`/`bisect` to their read-only subcommands.
READ_ONLY_GIT = {
    "git status", "git diff", "git log", "git show", "git blame", "git ls-files", "git rev-parse", "git merge-base",
    "git rev-list", "git cat-file", "git branch --list", "git branch --show-current", "git branch -vv",
    "git stash list", "git worktree list", "git reflog show", "git bisect log",
}
KNOWN_KEYS = {"name", "description", "argument-hint", "allowed-tools", "disable-model-invocation", "model", "version"}


def parse(path):
    m = re.match(r"---\n(.*?)\n---\n(.*)", path.read_text(encoding="utf-8"), re.S)
    assert m, f"{path}: no frontmatter"
    fm = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        assert sep and k.strip() and not k.startswith(" "), f"{path}: bad frontmatter line {line!r}"
        fm[k.strip()] = v.strip()
    return fm, m.group(2)


def split_tools(raw):
    return [t.strip() for t in re.split(r",\s*(?![^()]*\))", raw) if t.strip()]


def covered(tools, command):
    for t in tools:
        m = re.fullmatch(r"Bash\((.*?)(:\*)?\)", t)
        if m and command.startswith(m.group(1)):
            return True
    return False


def test_ten_skills():
    assert len(SKILLS) == 10


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_frontmatter(path):
    fm, body = parse(path)
    assert fm["name"] == path.parent.name
    assert len(fm["description"]) > 40
    assert set(fm) <= KNOWN_KEYS, set(fm) - KNOWN_KEYS
    assert body.strip().startswith("# ")


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_allowed_tools_well_formed_and_scoped(path):
    fm, _ = parse(path)
    tools = split_tools(fm["allowed-tools"])
    assert tools
    for t in tools:
        if t in ("Read", "Grep", "Glob"):
            continue
        assert t.startswith("Bash(") and t.endswith(":*)"), f"malformed or non-read tool entry {t!r}"
        assert t != "Bash" and t != "Bash(*)" and t != "Bash(:*)"
        if t == WARP_ENTRY:
            continue
        assert t[len("Bash("):-len(":*)")] in READ_ONLY_GIT, f"{path.parent.name}: {t} is not a known read-only git form"
    assert WARP_ENTRY in tools
    assert len(tools) == len(set(tools))


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_read_only_git_commands_in_body_are_pre_approved(path):
    """A read-only git command the body tells the model to run must be covered by allowed-tools (or it would prompt)."""
    fm, body = parse(path)
    tools = split_tools(fm["allowed-tools"])
    missing = []
    for m in re.finditer(r"`(git [^`]+)`", body):
        cmd = m.group(1)
        if any(cmd.startswith(ro) for ro in READ_ONLY_GIT) and not covered(tools, cmd):
            missing.append(cmd)
    assert missing == []


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_mutating_git_is_never_pre_approved(path):
    fm, _ = parse(path)
    for t in split_tools(fm["allowed-tools"]):
        for bad in ("add", "commit", "reset", "clean", "checkout", "switch", "restore", "rebase", "merge", "push",
                    "stash push", "stash drop", "stash pop", "branch -D", "branch -d", "config", "gc", "prune", "cherry-pick"):
            assert not re.match(rf"Bash\(git {re.escape(bad)}(?![\w-])", t), t
        assert "git branch:*" not in t and "git stash:*" not in t and "git reflog:*" not in t and "git worktree:*" not in t
