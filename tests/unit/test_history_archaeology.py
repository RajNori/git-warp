from tests.unit._history_helpers import run_cli, snapshot

BODY = "".join(f"line {i}\n" for i in range(20))


def test_renamed_file_follow(repo):
    c1 = repo.commit("add widget", {"src/widget.py": BODY})
    repo.git("mv", "src/widget.py", "src/gadget.py")
    c2 = repo.commit("rename widget to gadget")
    c3 = repo.commit("tweak gadget", {"src/gadget.py": BODY + "extra\n"})
    before = snapshot(repo)
    code, out = run_cli("archaeology", "src/gadget.py", "--repo", repo.path)
    assert code == 0 and out["found"] is True and out["mode"] == "file"
    assert [t["short"] for t in out["timeline"]] == [c3[:8], c2[:8], c1[:8]]
    assert out["renames"][0]["from"] == "src/widget.py" and out["renames"][0]["to"] == "src/gadget.py"
    assert out["renames"][0]["tag"] == "fact"
    assert [p["path"] for p in out["path_history"]] == ["src/widget.py", "src/gadget.py"]
    assert out["introduction"]["commit"]["sha"] == c1 and out["introduction"]["tag"] == "fact"
    tags = {s["tag"] for s in out["statements"]}
    assert tags <= {"fact", "inference"} and "fact" in tags
    assert out["unknown"]
    assert out["blame_summary"]["total_lines"] == 21
    assert any(w["sha"] == c1 for w in out["commits_worth_reading"])
    assert len(out["commits_worth_reading"]) <= 7
    assert snapshot(repo) == before


def test_revert_pairing(repo):
    repo.commit("add feature", {"feat.py": "def feature():\n    return 1\n"})
    bad = repo.commit("risky change to feature", {"feat.py": "def feature():\n    return 2\n"})
    repo.git("revert", "--no-edit", bad)
    rev = repo.sha()
    _, out = run_cli("archaeology", "feat.py", "--repo", repo.path)
    rv = out["reverts"]
    assert len(rv) == 1
    assert rv[0]["revert"] == rev and rv[0]["reverted"] == bad and rv[0]["reverted_in_examined_history"] is True
    assert rv[0]["linked_via"] == "body" and rv[0]["tag"] == "fact"
    worth = {w["sha"]: w for w in out["commits_worth_reading"]}
    assert rev in worth and bad in worth
    assert any("reverted by" in r["reason"] for r in worth[bad]["reasons"])


def test_revert_by_subject_is_inference(repo):
    orig = repo.commit("add thing", {"t.txt": "1\n"})
    repo.commit('Revert "add thing"', {"t.txt": "0\n"})
    _, out = run_cli("archaeology", "t.txt", "--repo", repo.path)
    assert out["reverts"][0]["linked_via"] == "subject-match" and out["reverts"][0]["tag"] == "inference"
    assert out["reverts"][0]["reverted"] == orig


def test_deleted_file_and_restore(repo):
    c1 = repo.commit("add gone", {"gone.txt": "a\nb\n"})
    repo.git("rm", "-q", "gone.txt")
    c2 = repo.commit("remove gone")
    _, out = run_cli("archaeology", "gone.txt", "--repo", repo.path)
    assert out["found"] and out["currently_in_head"] is None
    assert out["currently_deleted"]["deleted_in"] == c2[:8]
    assert any("does not exist at HEAD" in w for w in out["warnings"])
    assert out["introduction"]["commit"]["sha"] == c1
    repo.commit("restore gone", {"gone.txt": "a\nb\n"})
    _, out = run_cli("archaeology", "gone.txt", "--repo", repo.path)
    assert out["currently_deleted"] is None
    assert out["deleted_then_restored"] and out["deleted_then_restored"][0]["deleted_in"] == c2[:8]


def test_missing_path(repo):
    code, out = run_cli("archaeology", "nope/never.txt", "--repo", repo.path)
    assert code == 0 and out["found"] is False and out["timeline"] == []
    assert out["unknown"]


def test_spaces_in_paths(repo):
    repo.commit("add spaced", {"my docs/read me.txt": "hello\n"})
    repo.commit("edit spaced", {"my docs/read me.txt": "hello\nworld\n"})
    _, out = run_cli("archaeology", "my docs/read me.txt", "--repo", repo.path)
    assert out["found"] and out["examined_commits"] == 2
    assert out["timeline"][0]["path"] == "my docs/read me.txt"
    _, out = run_cli("archaeology", "my docs", "--repo", repo.path)
    assert out["mode"] == "directory" and out["examined_commits"] == 2


def test_glob_chars_in_path_are_literal(repo):
    repo.commit("add star", {"a[1].txt": "x\n"})
    repo.commit("other", {"a1.txt": "y\n"})
    _, out = run_cli("archaeology", "a[1].txt", "--repo", repo.path)
    assert out["examined_commits"] == 1


def test_symbol_search(repo):
    c1 = repo.commit("add helper", {"lib.py": "def helper_fn():\n    pass\n"})
    repo.commit("unrelated", {"other.py": "x = 1\n"})
    c3 = repo.commit("rename helper", {"lib.py": "def helper_fn2():\n    pass\n"})
    _, out = run_cli("archaeology", "--symbol", "helper_fn()", "--repo", repo.path)
    assert out["mode"] == "symbol"
    assert [t["sha"] for t in out["timeline"]] == [c3, c1]
    assert out["introduction"]["commit"]["sha"] == c1 and out["introduction"]["tag"] == "inference"
    assert out["symbol_presence"]["in_head"] is False
    _, out = run_cli("archaeology", "--symbol", "helper_fn2", "--repo", repo.path)
    assert out["symbol_presence"]["in_head"] is True and out["symbol_presence"]["files"] == ["lib.py"]


def test_regex_and_question(repo):
    repo.commit("fix crash when parsing empty input", {"p.py": "if not s: return\n"})
    repo.commit("add docs", {"d.md": "doc\n"})
    _, out = run_cli("archaeology", "--regex", "if not [a-z]+", "--repo", repo.path)
    assert out["examined_commits"] == 1
    _, out = run_cli("archaeology", "--question", "why was the parsing crash fixed?", "--repo", repo.path)
    assert out["mode"] == "question" and out["examined_commits"] == 1
    assert out["fix_like_commits"][0]["tag"] == "inference"
    assert any(s["tag"] == "inference" for s in out["statements"])
    code, out = run_cli("archaeology", "--regex", "(", "--repo", repo.path)
    assert code == 2 and "error" in out


def test_large_rewrite_and_fix_detection(repo):
    repo.commit("add module", {"m.py": "".join(f"a{i}\n" for i in range(10))})
    repo.commit("small tweak", {"m.py": "".join(f"a{i}\n" for i in range(10)) + "b\n"})
    big = repo.commit("rewrite module", {"m.py": "".join(f"z{i}\n" for i in range(200))})
    fix = repo.commit("fix off-by-one bug in module", {"m.py": "".join(f"z{i}\n" for i in range(200)) + "fixed\n"})
    _, out = run_cli("archaeology", "m.py", "--repo", repo.path)
    assert out["large_rewrites"][0]["sha"] == big and out["large_rewrites"][0]["tag"] == "inference"
    assert [f["sha"] for f in out["fix_like_commits"]] == [fix]
    assert out["blame_summary"]["top_commits"][0]["sha"] == big
    worth = [w["sha"] for w in out["commits_worth_reading"]]
    assert big in worth and fix in worth
    assert out["authorship_timeline"][0]["author"] == "Test Author"


def test_merge_points_and_truncation(repo):
    repo.commit("base", {"s.txt": "1\n"})
    repo.branch("side", checkout=True)
    repo.commit("side edit", {"s.txt": "1\n2\n"})
    repo.checkout("main")
    repo.commit("main other", {"o.txt": "o\n"})
    repo.git("merge", "--no-ff", "-m", "merge side", "side")
    _, out = run_cli("archaeology", "s.txt", "--repo", repo.path)
    assert any(m["subject"] == "merge side" for m in out["merge_points"]) or out["first_parent_history"]
    _, out = run_cli("archaeology", "s.txt", "--limit", "1", "--repo", repo.path)
    assert out["truncated"] is True and out["examined_commits"] == 1
    assert out["introduction"]["tag"] == "inference"
    assert any("introduction cannot be determined" in u for u in out["unknown"])
    _, out = run_cli("archaeology", "s.txt", "--since", "2001-01-01", "--repo", repo.path)
    assert out["found"]


def test_unborn_and_non_repo(empty_repo, tmp_path):
    code, out = run_cli("archaeology", "x", "--repo", empty_repo.path)
    assert code == 0 and out["found"] is False and out["warnings"]
    code, out = run_cli("archaeology", "x", "--repo", tmp_path)
    assert code == 2 and "error" in out
    code, out = run_cli("archaeology", "--repo", empty_repo.path)
    assert code == 0   # unborn is reported before argument checks
    code, out = run_cli("archaeology", "--repo", tmp_path)
    assert code == 2


def test_needs_a_target(repo):
    code, out = run_cli("archaeology", "--repo", repo.path)
    assert code == 2 and "error" in out


def test_shallow_clone_warning(make_repo):
    import subprocess
    src = make_repo("src").seed(3)
    sh = make_repo("sh", init=False)
    subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + str(src.path), str(sh.path)], check=True)
    _, out = run_cli("archaeology", "f2.txt", "--repo", sh.path)
    assert any("shallow" in w for w in out["warnings"])
    assert out["found"]


def test_detached_head(repo):
    repo.git("checkout", "-q", "--detach", "HEAD~1")
    code, out = run_cli("archaeology", "f0.txt", "--repo", repo.path)
    assert code == 0 and out["found"] and out["state"]["detached"] is True
