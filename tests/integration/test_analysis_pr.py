"""``warp.py pr`` integration tests on real temporary repositories."""
import contextlib
import io
import json
import subprocess
import sys
import time

import pytest

from tests.conftest import SCRIPTS, RepoBuilder
from gitwarp.analysis import cli

SECRET_BODY = "abcdefghijklmnop1234567890"
SECRET = f"sk-live-{SECRET_BODY}"


def pr(path, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["pr", "--repo", str(path), *args])
    raw = buf.getvalue()
    return code, json.loads(raw), raw


def risk_ids(d):
    return [x["id"] for x in d["risk"]["driver_details"]]


@pytest.fixture
def feature(make_repo):
    r = make_repo()
    r.commit("chore: initial", {
        "README.md": "# app\n", "package.json": '{"name": "a"}\n', "package-lock.json": "{}\n",
        "src/app.py": "def public_fn(a):\n    return a\n\ndef gone_fn():\n    return 1\n",
        "tests/test_app.py": "def test_a():\n    assert True\n",
        "web/lib.ts": "export function oldApi(a: number) { return a }\n",
        ".github/workflows/ci.yml": "on: push\n",
    })
    r.branch("feature/PAY-123-thing", checkout=True)
    r.commit("feat: add users migration", {"db/migrations/0001_users.sql": "CREATE TABLE users (id int);\nDROP TABLE old_users;\n"})
    r.commit("fixup! feat: add users migration", {"db/migrations/0001_users.sql": "CREATE TABLE users (id int, name text);\nDROP TABLE old_users;\n"})
    (r.path / "assets").mkdir()
    (r.path / "assets/logo.png").write_bytes(b"\x89PNG\x00\x01\x02" * 20)
    r.write("src/auth/login.py", f'API_KEY = "{SECRET}"\n\ndef login(user):\n    print("debug", user)\n    # TODO: remove\n    return True\n')
    r.write("src/app.py", "def public_fn(a, b):\n    return a\n\ndef added_fn():\n    return 2\n")
    r.write("package.json", '{"name": "a", "dependencies": {"left-pad": "1"}}\n')
    r.write("web/lib.ts", "export function newApi(a: number) { return a }\nconsole.log('x')\n")
    r.write("web/lib.test.ts", "it.only('runs', () => {})\n")
    r.commit("WIP stuff")
    return r


def test_feature_branch_full_analysis(feature):
    code, d, raw = pr(feature.path)
    assert code == 0 and d["status"] == "ok"
    assert d["base"]["ref"] == "main" and d["base"]["auto_detected"] is True
    assert d["merge_base"] == feature.sha("main")
    # commits and hygiene
    assert d["commits"]["count"] == 3
    issues = {i for h in d["hygiene"] if h["id"] == "commit-hygiene" for x in h["detail"] for i in x["issues"]}
    assert {"fixup-or-squash-commit", "wip-commit"} <= issues
    assert d["commits"]["conventional_commits"]["nonconforming_total"] >= 1
    # files, binary, tags
    paths = {f["path"] for f in d["files"]["items"]}
    assert "assets/logo.png" in paths and d["totals"]["binary_files"] == 1
    assert d["tags"]["migration"]["count"] == 1
    assert d["artifacts"]["binary_files"]["paths"] == ["assets/logo.png"]
    # dependencies
    assert d["dependencies"]["manifest_without_lockfile"][0]["manifest"] == "package.json"
    # debug leftovers (added lines only)
    counts = d["debug_leftovers"]["counts"]
    assert counts["console-log"] == 1 and counts["python-print"] == 1 and counts["todo-marker"] == 1 and counts["test-only"] == 1
    # secrets: location reported, value never echoed
    f = d["secrets"]["findings"][0]
    assert f["path"] == "src/auth/login.py" and f["line"] == 1 and f["value"] == "[REDACTED]"
    assert SECRET_BODY not in raw and SECRET not in raw
    # api heuristics
    api = d["api_surface"]
    assert api["heuristic"] is True
    assert any(x["symbol"] == "gone_fn" for x in api["possibly_breaking_removed"])
    assert any(x["symbol"] == "public_fn" for x in api["signature_changed"])
    assert any(x["symbol"] == "oldApi" for x in api["possibly_breaking_removed"])
    # tests, rollback, evidence, prose facts
    assert d["tests"]["test_files_changed"] == 1
    assert d["rollback"]["migrations_present"] and d["rollback"]["destructive_statements_added"] >= 1
    assert d["rollback"]["migrations"][0]["reversible_signal"] is False
    te = d["testing_evidence"]
    assert te["ran_by_git_warp"] is False and ".github/workflows/ci.yml" in te["found_in_repo"]["ci_config_files"]
    assert "tests" in te["found_in_repo"]["test_directories"]
    assert "PAY-123" in d["facts_for_prose"]["ticket_refs"]
    # risk
    assert d["risk"]["level"] == "HIGH"
    assert {"secret-material", "destructive-migration", "migration", "lockfile-drift", "api-surface", "tests-disabled"} <= set(risk_ids(d))
    # clusters
    assert d["clusters"]["items"] and d["unrelated_changes"]["flag"] in (True, False)


def test_removed_secret_line_is_not_reported(make_repo):
    r = make_repo().seed(1)
    r.commit("with secret", {"old.py": f'KEY = "{SECRET}"\n'})
    r.branch("feat", checkout=True)
    r.commit("remove secret", {"old.py": "KEY = None\n"})
    _, d, raw = pr(r.path)
    assert d["secrets"]["count"] == 0 and SECRET_BODY not in raw


def test_secret_file_in_range(make_repo):
    r = make_repo().seed(1)
    r.branch("feat", checkout=True)
    r.commit("oops", {".env": "A=1\n", ".env.example": "A=\n"})
    _, d, _ = pr(r.path)
    assert d["secrets"]["secret_file_paths"]["paths"] == [".env"]
    assert d["risk"]["level"] == "HIGH"


def test_uncommitted_changes_are_listed_not_analysed(feature):
    feature.write("src/uncommitted.py", "x = 1\n")
    feature.write("README.md", "changed\n")
    _, d, _ = pr(feature.path)
    ni = d["not_included"]
    assert ni["has_uncommitted_changes"] and "src/uncommitted.py" in ni["untracked"]["paths"] and "README.md" in ni["unstaged"]["paths"]
    assert "src/uncommitted.py" not in {f["path"] for f in d["files"]["items"]}


def test_base_equals_head(repo):
    code, d, _ = pr(repo.path)
    assert code == 0 and d["status"] == "no-commits" and "Nothing to put in a PR" in d["message"]
    code, d, _ = pr(repo.path, "main")
    assert d["status"] == "no-commits"


def test_branch_behind_base_and_upstream_state(make_repo):
    r = make_repo().seed(2)
    r.branch("feat", checkout=True)
    r.commit("feat work", {"x.txt": "1\n"})
    r.checkout("main")
    r.commit("main moves", {"m.txt": "1\n"})
    r.checkout("feat")
    _, d, _ = pr(r.path)
    assert d["behind_base"] == 1 and d["is_behind_base"] and "behind-base" in risk_ids(d)
    assert d["upstream"]["state"] == "none"
    # branch fully behind base
    r.checkout("main"); r.branch("old", "HEAD~1", checkout=True)
    _, d, _ = pr(r.path, "main")
    assert d["status"] == "no-commits" and "behind" in d["message"]


def test_no_base_detectable(make_repo):
    r = make_repo(branch="trunk").seed(2)
    code, d, _ = pr(r.path)
    assert code == 2 and "base" in d["error"] and "--base" in d["hint"]
    r.branch("feat", checkout=True); r.commit("w", {"a.txt": "1\n"})
    code, d, _ = pr(r.path, "--base", "trunk")
    assert code == 0 and d["totals"]["files"] == 1


@pytest.mark.parametrize("bad", ["--upload-pack=x", "-x"])
def test_invalid_base_rejected(repo, bad):
    code, d, _ = pr(repo.path, f"--base={bad}")
    assert code == 2 and "invalid base" in d["error"]
    code, d, _ = pr(repo.path, bad)  # as positional: argparse or check_ref rejects it
    assert code == 2 and "error" in d


def test_unknown_base_ref(repo):
    code, d, _ = pr(repo.path, "no-such-branch")
    assert code == 2 and "not found" in d["error"]


def test_unrelated_histories(make_repo):
    r = make_repo().seed(2)
    r.git("checkout", "-q", "--orphan", "island")
    r.git("rm", "-rf", "-q", ".")
    r.commit("island root", {"z.txt": "1\n"})
    code, d, _ = pr(r.path, "main")
    assert code == 2 and "unrelated histories" in d["error"]


def test_shallow_without_merge_base(make_repo, tmp_path):
    o = make_repo("origin").seed(3)
    o.branch("feat", checkout=True); o.commit("f", {"f.txt": "1\n"})
    c = RepoBuilder(tmp_path / "shallow", init=False)
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--no-single-branch", o.path.as_uri(), str(c.path)], check=True, capture_output=True)
    c.git("checkout", "-q", "feat")
    code, d, _ = pr(c.path, "origin/main")
    assert code == 2 and "shallow" in d["error"] and "unshallow" in d["hint"]


def test_detached_head(feature):
    feature.git("checkout", "-q", "--detach", "HEAD")
    code, d, _ = pr(feature.path)
    assert code == 0 and d["head"]["detached"] is True and d["head"]["branch"] is None and d["commits"]["count"] == 3


def test_base_as_sha_and_remote_ref(make_repo, tmp_path):
    o = make_repo("origin").seed(2)
    c = RepoBuilder(tmp_path / "cl", init=False)
    subprocess.run(["git", "clone", "-q", o.path.as_uri(), str(c.path)], check=True, capture_output=True)
    c.git("config", "user.name", "T"); c.git("config", "user.email", "t@e.com")
    c.branch("feat", checkout=True)
    c.commit("c1", {"a.txt": "1\n"}); c.commit("c2", {"b.txt": "1\n"})
    _, d, _ = pr(c.path, "origin/main")
    assert d["commits"]["count"] == 2 and d["base"]["ref"] == "origin/main"
    _, d, _ = pr(c.path, "--base", o.sha())
    assert d["commits"]["count"] == 2
    _, d, _ = pr(c.path)  # auto-detect picks origin/HEAD
    assert d["base"]["ref"].startswith("origin/")


def test_renames_spaces_unicode(make_repo):
    r = make_repo().seed(1)
    r.commit("add", {"old name/ü.txt": "".join(f"line {i}\n" for i in range(30))})
    r.branch("feat", checkout=True)
    (r.path / "new name").mkdir()
    r.git("mv", "old name/ü.txt", "new name/ünï.txt")
    r.commit("refactor: rename")
    _, d, _ = pr(r.path)
    item = d["files"]["items"][0]
    assert item["path"] == "new name/ünï.txt" and item["status"] == "renamed" and item["renamed_from"] == "old name/ü.txt"


def test_merge_commit_in_range_flagged(make_repo):
    r = make_repo().seed(1)
    r.branch("feat", checkout=True); r.commit("a", {"a.txt": "1\n"})
    r.branch("side", "HEAD~1", checkout=True); r.commit("b", {"b.txt": "1\n"})
    r.checkout("feat"); r.git("merge", "--no-ff", "-m", "Merge side", "side")
    _, d, _ = pr(r.path)
    assert d["commits"]["merge_commits"] == 1 and "merge-commits-in-range" in {h["id"] for h in d["hygiene"]}


def test_huge_diff_is_capped(make_repo):
    r = make_repo().seed(1)
    r.branch("feat", checkout=True)
    r.commit("big", {"big.py": "\n".join(f"v{i} = {i}" for i in range(6000)) + "\n", "gen/data.min.js": "x" * 10})
    code, d, _ = pr(r.path)
    assert code == 0 and d["patch_scan"]["truncated"] is True and any("per-file" in w for w in d["warnings"])
    assert d["totals"]["added"] >= 6000


def test_pr_is_read_only(feature, monkeypatch):
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")
    before = (feature.git("for-each-ref"), feature.git("status", "--porcelain"), (feature.path / ".git/index").read_bytes())
    pr(feature.path)
    after = (feature.git("for-each-ref"), feature.git("status", "--porcelain"), (feature.path / ".git/index").read_bytes())
    assert before == after


def test_cli_subprocess(feature):
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "pr", "main", "--repo", str(feature.path)], capture_output=True, text=True)
    assert p.returncode == 0 and p.stderr == ""
    assert json.loads(p.stdout)["command"] == "pr"
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "pr", "--base=--upload-pack=x", "--repo", str(feature.path)], capture_output=True, text=True)
    assert p.returncode == 2 and "error" in json.loads(p.stdout)
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "pr"], capture_output=True, text=True, cwd=feature.path.parent)
    assert p.returncode == 2 and "not a git repository" in json.loads(p.stdout)["error"]


def test_pr_unborn(empty_repo):
    code, d, _ = pr(empty_repo.path)
    assert code == 2 and "no commits" in d["error"]


def test_pr_performance(make_repo):
    from tests.integration.test_analysis_xray import _bulk_repo
    r = make_repo()
    _bulk_repo(r)
    r.branch("feat", "HEAD~150", checkout=True)
    t = time.time()
    code, d, _ = pr(r.path, "main")
    assert code == 0 and d["status"] == "no-commits"  # feat is behind main
    r.checkout("main")
    t = time.time()
    code, d, _ = pr(r.path, "feat")
    assert code == 0 and d["commits"]["count"] == 150 and time.time() - t < 5
