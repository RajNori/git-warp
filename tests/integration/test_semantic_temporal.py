import json
import subprocess
import sys
from pathlib import Path

from gitwarp.semantic import temporal

ROOT = Path(__file__).resolve().parent.parent.parent
WARP = str(ROOT / "scripts" / "warp.py")

GUARDED = "def charge(x):\n    validate_amount_strictly(x)\n    return x * 2\n"
UNGUARDED = "def charge(x):\n    return x * 2\n"


def history(make_repo):
    r = make_repo()
    r.commit("add charge", {"pay.py": GUARDED})
    r.commit("fix: remove validation, it caused a race condition", {"pay.py": UNGUARDED})
    r.commit("unrelated", {"other.py": "x = 1\n"})
    return r


def test_reintroduced_removed_line_cited(make_repo):
    r = history(make_repo)
    r.write("pay.py", GUARDED)
    res = temporal.analyze(r.path)
    f = [x for x in res["findings"] if x["kind"] == "reintroduced-removed-code"]
    assert f and f[0]["match_quality"] == "exact-line"
    ev = f[0]["evidence"][0]
    assert ev["subject"].startswith("fix: remove validation") and ev["side"] == "removed" and len(ev["sha"]) == 40 and ev["date"]
    assert {"race", "validate"} <= set(f[0]["signals"])
    assert f[0]["caveat"] and "semantic" in f[0]["caveat"] and f[0]["verify_with"]
    assert res["probes"]["used"] <= res["probes"]["budget"]


def test_removed_lines_blame_fix_commit(make_repo):
    r = make_repo()
    r.commit("add", {"a.py": "def f(x):\n    return x\n"})
    r.commit("fix: sanitize input to stop injection", {"a.py": "def f(x):\n    x = sanitize_input_value(x)\n    return x\n"})
    r.write("a.py", "def f(x):\n    return x\n")
    res = temporal.analyze(r.path)
    rf = [x for x in res["findings"] if x["kind"] == "removed-fix-code"]
    assert rf and "injection" in rf[0]["signals"] and rf[0]["evidence"][0]["subject"].startswith("fix: sanitize")


def test_no_match_no_fabrication(make_repo):
    r = history(make_repo)
    r.write("brand_new_feature.py", "def completely_novel_function_name():\n    return compute_something_unseen_before()\n")
    res = temporal.analyze(r.path)
    assert res["findings"] == [] and "no historical evidence" in res["message"]


def test_base_range_excludes_own_commits(make_repo):
    r = history(make_repo)
    r.branch("feat", checkout=True)
    r.commit("remove again", {"pay.py": UNGUARDED})
    r.commit("restore guard", {"pay.py": GUARDED})
    res = temporal.analyze(r.path, base="main")
    # the removal that happened on this branch is part of the change set, not prior history
    assert all(e["sha"] != r.sha("HEAD~1") for f in res["findings"] for e in f["evidence"])
    assert res["mode"] == "base:main"


def test_dependency_flip_and_reverts(make_repo):
    r = make_repo()
    r.commit("init", {"package.json": '{\n  "dependencies": {\n    "left-pad": "1.0.0"\n  }\n}\n', "a.js": "x\n"})
    r.commit("drop left-pad", {"package.json": '{\n  "dependencies": {\n  }\n}\n'})
    r.commit('Revert "feature"', {"a.js": "y\n"})
    r.write("package.json", '{\n  "dependencies": {\n    "left-pad": "1.0.0"\n  }\n}\n')
    r.write("a.js", "z\n")
    res = temporal.analyze(r.path)
    kinds = {f["kind"] for f in res["findings"]}
    assert "dependency-previously-removed" in kinds and "touched-file-has-reverts" in kinds
    dep = next(f for f in res["findings"] if f["kind"] == "dependency-previously-removed")
    assert dep["dependency"] == "left-pad" and dep["direction"] == "added" and dep["evidence"][0]["subject"] == "drop left-pad"


def test_unborn_and_nonrepo_and_cli(make_repo, tmp_path):
    r = make_repo()
    r.write("a.py", "x = 1\n")
    res = temporal.analyze(r.path)
    assert res["findings"] == [] and res["message"]
    p = subprocess.run([sys.executable, WARP, "temporal", "--repo", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode != 0 and "error" in json.loads(p.stdout)
    r2 = history(make_repo)
    r2.write("pay.py", GUARDED)
    p = subprocess.run([sys.executable, WARP, "temporal", "--repo", str(r2.path), "--limit", "1"], capture_output=True, text=True)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and len(out["findings"]) == 1
    p = subprocess.run([sys.executable, WARP, "temporal", "--base", "--x", "--repo", str(r2.path)], capture_output=True, text=True)
    assert p.returncode != 0 and "error" in json.loads(p.stdout)


def test_diff_parser_and_distinctive_lines():
    d = temporal.parse_diff("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -3,2 +3,1 @@\n-old line one\n-old two\n+return compute_total_amount(items)\n")
    assert d["x.py"]["removed"] == [(3, "old line one"), (4, "old two")] and d["x.py"]["added"][0][0] == 3
    lines = temporal.distinctive_lines([(1, "}"), (2, "pass"), (3, "# comment here is long enough to pass"), (4, "x = 1"), (5, "result = compute_total_amount(items, tax)")])
    assert [l for _, l in lines] == ["result = compute_total_amount(items, tax)"]
