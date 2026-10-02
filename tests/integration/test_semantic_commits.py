import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WARP = str(ROOT / "scripts" / "warp.py")


def warp(*args, cwd=None):
    p = subprocess.run([sys.executable, WARP, *args], capture_output=True, text=True, cwd=cwd)
    return p.returncode, json.loads(p.stdout), p.stderr


def snapshot(r):
    return (r.git("status", "--porcelain=v1", "-uall"), r.git("ls-files", "--stage"), r.git("for-each-ref"), r.git("diff"))


def make_change_set(make_repo):
    r = make_repo()
    r.commit("init", {"src/pay/pay.py": "def pay(x):\n    return x\n" + "\n" * 60 + "def refund(x):\n    return -x\n",
                      "package.json": '{"name": "x"}\n', "README.md": "hi\n"})
    r.write("src/pay/pay.py", "def pay(x):\n    return x + 1\n" + "\n" * 60 + "def refund(x):\n    return -x - 1\n")
    r.write("tests/test_pay.py", "def test_pay(): pass\n")
    r.write("package.json", '{"name": "x", "dependencies": {"left-pad": "1.0.0"}}\n')
    r.write("package-lock.json", "{}\n")
    r.write("db/migrations/001_add.sql", "create table t(a int);\n")
    r.write("README.md", "hi there\n")
    r.write(".env", "TOKEN=abc\n")
    r.write("dist/out.js", "x\n")
    return r


def test_commits_read_only_and_proposals(make_repo):
    r = make_change_set(make_repo)
    before = snapshot(r)
    rc, out, _ = warp("commits", "--repo", str(r.path))
    assert rc == 0 and snapshot(r) == before
    kinds = [p["kind"] for p in out["proposals"]]
    assert kinds.index("deps") < kinds.index("migration") < kinds.index("source") < kinds.index("docs")
    src = next(p for p in out["proposals"] if p["kind"] == "source")
    assert "tests/test_pay.py" in src["files"] and src["needs_type_decision"] and "feat|fix?" in src["message"]
    assert any(c.startswith("git add -- ") for c in src["commands"]) and any(c.startswith("git commit -m") for c in src["commands"])
    # secrets flagged and never proposed; generated flagged
    assert ".env" in out["flags"]["secrets"] and "dist/out.js" in out["flags"]["generated"]
    assert all(".env" not in p["files"] and "dist/out.js" not in p["files"] for p in out["proposals"])
    assert not any(".env" in c for s in out["commands"] for c in s["commands"])
    assert any(".env" in w for w in out["warnings"])
    assert "NOTHING WAS STAGED" in out["notes"][0]
    # hunks listed for the multi-hunk file
    assert out["hunks"]["src/pay/pay.py"]["count"] == 2 and out["hunks"]["src/pay/pay.py"]["hunks"][0]["header"].startswith("@@")


def test_commits_staged_only_and_partial(make_repo):
    r = make_change_set(make_repo)
    r.git("add", "--", "src/pay/pay.py")
    r.write("src/pay/pay.py", "def pay(x):\n    return x + 2\n" + "\n" * 60 + "def refund(x):\n    return -x - 1\n")
    before = snapshot(r)
    rc, out, _ = warp("commits", "--staged", "--repo", str(r.path))
    assert rc == 0 and snapshot(r) == before
    assert out["mode"] == "staged" and [f["path"] for f in out["files"]] == ["src/pay/pay.py"]
    assert any("partially staged" in m["note"] for m in out["mixed_concerns"])


def test_commits_unborn_nonrepo_and_bad_args(make_repo, tmp_path):
    r = make_repo()
    r.write("a.py", "x=1\n")
    rc, out, _ = warp("commits", "--repo", str(r.path))
    assert rc == 0 and out["state"]["unborn"] and any("no commits" in w for w in out["warnings"])
    rc, out, err = warp("commits", "--repo", str(tmp_path))
    assert rc != 0 and "error" in out and "Traceback" not in err
    rc, out, err = warp("commits", "--bogus", "--repo", str(r.path))
    assert rc != 0 and "error" in out and "Traceback" not in err
    rc, out, err = warp("commits", "--repo", str(tmp_path / "missing"))
    assert rc != 0 and "error" in out


def test_commits_detached_and_rename_deletion(make_repo):
    r = make_repo().seed(2)
    r.git("checkout", "-q", "--detach")
    r.git("mv", "f0.txt", "g0.txt")
    r.git("rm", "-q", "f1.txt")
    rc, out, _ = warp("commits", "--repo", str(r.path))
    assert rc == 0 and out["state"]["detached"]
    st = {f["path"]: f["status"] for f in out["files"]}
    assert st.get("g0.txt") == "R" and st.get("f1.txt") == "D"
    add = [c for s in out["commands"] for c in s["commands"] if c.startswith("git add")]
    assert any("f0.txt" in c and "g0.txt" in c for c in add)
