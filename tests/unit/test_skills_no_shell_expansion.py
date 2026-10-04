"""Skill commands must be statically analysable by Claude Code's permission system.

Found by the live acceptance run (LIVE-203/204/205): the rescue, archaeology and bisect skills told Claude to run
``warp.py ... --repo "$PWD"``.  Claude Code reports "Contains shell syntax (string) that cannot be statically analyzed" for
``"$PWD"`` and prompts even though the ``warp.py`` prefix is pre-approved.  ``--repo`` defaults to the current directory, so no
skill needs a shell expansion; the only variable allowed on a ``warp.py`` command line is ``${CLAUDE_PLUGIN_ROOT}`` (which Claude
Code substitutes before matching) and the documented ``$ARGUMENTS`` placeholder.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_FILES = sorted((ROOT / "skills").rglob("*.md"))
ALLOWED = {"${CLAUDE_PLUGIN_ROOT}", "$ARGUMENTS"}


def _warp_command_lines():
    for f in SKILL_FILES:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if "warp.py" in line and "python3" in line:
                yield f, n, line


def test_skills_exist():
    assert SKILL_FILES and any(True for _ in _warp_command_lines())


def test_no_shell_expansion_in_warp_commands():
    offenders = []
    for f, n, line in _warp_command_lines():
        stripped = line
        for ok in ALLOWED:
            stripped = stripped.replace(ok, "")
        if re.search(r"\$\(|`[^`]*`\$|\$[A-Za-z_{(]|\$\{", stripped.replace("`python3", "python3")):
            offenders.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:140]}")
    assert offenders == []


def test_no_pwd_anywhere_in_skills():
    assert [str(f.relative_to(ROOT)) for f in SKILL_FILES if '"$PWD"' in f.read_text().replace('Do **not** pass `--repo "$PWD"`', "")] == []
