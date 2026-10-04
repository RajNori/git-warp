"""Agent definitions: frontmatter, enforced (not prompt-only) tool scoping, and body/tool consistency.

Claude Code's agent ``tools`` field takes tool names only (https://code.claude.com/docs/en/sub-agents): a scoped
``Bash(...)`` entry there would not restrict commands, so a read-only agent must simply not have Bash.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"
AGENTS = sorted((PLUGIN / "agents").glob("*.md"))
EXPECTED = {"git-forensic-analyst", "git-history-analyst", "git-risk-analyst"}
READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Agent", "Task"}
KNOWN_KEYS = {"name", "description", "tools", "model", "color", "disallowedTools", "skills"}


def parse(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, f"{path.name}: no frontmatter"
    fm = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        assert sep, f"{path.name}: bad frontmatter line {line!r}"
        fm[k.strip()] = v.strip()
    return fm, m.group(2)


def split_tools(raw):
    raw = raw.strip()
    if raw.startswith("["):
        raw = raw[1:-1]
    return [t.strip().strip("\"'") for t in re.split(r",\s*(?![^()]*\))", raw) if t.strip()]


def bash_permits(tools, command):
    """Would ``command`` be allowed by the Bash entries in ``tools``?  (prefix semantics of ``Bash(prefix:*)``)"""
    for t in tools:
        m = re.fullmatch(r"Bash\((.*?)(:\*)?\)", t)
        if m and command.startswith(m.group(1)):
            return True
    return "Bash" in tools


def test_expected_agents_exist():
    assert {p.stem for p in AGENTS} == EXPECTED


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_frontmatter_required_fields(path):
    fm, body = parse(path)
    assert fm["name"] == path.stem
    assert len(fm["description"].strip("\"")) > 40
    assert fm.get("tools"), "tools must be declared explicitly (omitting it would inherit every tool)"
    assert set(fm) <= KNOWN_KEYS, set(fm) - KNOWN_KEYS
    assert body.strip()


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_no_unscoped_or_unsupported_bash(path):
    fm, _ = parse(path)
    tools = split_tools(fm["tools"])
    assert "Bash" not in tools
    assert not [t for t in tools if t.startswith("Bash(")], "scoped Bash is not enforced in agent frontmatter"
    assert set(tools) <= READ_ONLY_TOOLS
    assert not set(tools) & MUTATING_TOOLS


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_declared_read_only_agents_have_no_mutating_tool(path):
    fm, body = parse(path)
    tools = set(split_tools(fm["tools"]))
    if "read-only" in (fm["description"] + body).lower():
        assert not tools & MUTATING_TOOLS


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_body_does_not_instruct_commands_the_tools_cannot_run(path):
    """Every shell command mentioned in the body must be framed as something the CALLER runs (or is forbidden)."""
    fm, body = parse(path)
    tools = split_tools(fm["tools"])
    framing = re.compile(r"caller|delegating assistant|parent|cannot run|no shell|never run|ask for|ask the user|wrap|name the exact"
                         r"|you never run it|not readable|propose", re.I)
    code_cmd = re.compile(r"`((?:python3 [^`]*warp\.py\"?|warp\.py|git)\b[^`]*)`")
    text = re.sub(r"<example>.*?</example>", "", body, flags=re.S)  # dialogue, not instructions to the agent
    for para in re.split(r"\n\s*\n|\n(?=[-0-9#])", text):
        for m in code_cmd.finditer(para):
            cmd = m.group(1)
            if bash_permits(tools, cmd):
                continue
            assert framing.search(para), f"{path.name}: tells the model to run {cmd!r} but has no Bash; paragraph: {para[:200]!r}"
    assert not re.search(r"^\s*(?:\$ |```(?:sh|bash))", text, re.M), "no shell code blocks in a shell-less agent"


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_body_states_the_permission_boundary(path):
    _, body = parse(path)
    assert "NO shell" in body and "Read, Grep, Glob" in body
