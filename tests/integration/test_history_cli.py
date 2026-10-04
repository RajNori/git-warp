"""End-to-end: run ``python3 scripts/warp.py ...`` as a subprocess and validate the JSON contract."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tests.conftest import PLUGIN, _ENV

WARP = str(PLUGIN / "scripts" / "warp.py")


def warp(*argv, cwd=None):
    env = dict(os.environ, **_ENV)
    p = subprocess.run([sys.executable, WARP, *map(str, argv)], capture_output=True, text=True, cwd=cwd, env=env)
    assert "Traceback" not in p.stderr + p.stdout
    return p.returncode, json.loads(p.stdout)


def test_rescue_roundtrip_via_subprocess(repo):
    lost = repo.commit("work to lose", {"w.txt": "w\n"})
    repo.git("reset", "--hard", "HEAD~1")
    code, out = warp("rescue", "--repo", repo.path)
    assert code == 0 and out["candidates"][0]["sha"] == lost
    code, out = warp("rescue", "preserve", lost, "--name", "rescue/test-branch", "--repo", repo.path)
    assert code == 0 and out["status"] == "created"
    assert repo.git("rev-parse", "rescue/test-branch") == lost
    code, out = warp("rescue", "preserve", lost, "--name", "rescue/test-branch", "--repo", repo.path)
    assert code == 0 and out["status"] == "already_preserved"
    code, out = warp("rescue", "preserve", repo.sha(), "--name", "rescue/test-branch", "--repo", repo.path)
    assert code == 2 and "already exists" in out["error"]
    default_name = re.compile(r"^rescue/\d{4}-\d{2}-\d{2}-[0-9a-f]{8}$")
    code, out = warp("rescue", "preserve", repo.sha(), "--dry-run", "--repo", repo.path)
    assert default_name.match(out["branch"])


def test_cwd_default_repo(repo):
    code, out = warp("archaeology", "f0.txt", cwd=repo.path)
    assert code == 0 and out["found"] is True
    code, out = warp("bisect", "status", cwd=repo.path)
    assert code == 0 and out["in_progress"] is False


def test_non_repo_and_usage_json(tmp_path):
    for argv in (["rescue"], ["archaeology", "x"], ["bisect", "status"], ["bisect", "plan", "--good", "a"]):
        code, out = warp(*argv, "--repo", tmp_path)
        assert code == 2 and "error" in out
    code, out = warp("bisect", "bogus", cwd=tmp_path)
    assert code == 2 and "error" in out
    code, out = warp("rescue", "preserve", cwd=tmp_path)
    assert code == 2 and "error" in out


def test_history_package_never_spawns_processes():
    src = "".join(p.read_text() for p in (PLUGIN / "scripts" / "gitwarp" / "history").glob("*.py"))
    assert "subprocess" not in src and "shell=True" not in src and "eval(" not in src and "exec(" not in src
    assert "os.system" not in src
