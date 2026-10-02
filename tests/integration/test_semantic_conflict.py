import json
import subprocess
import sys
from pathlib import Path

from gitwarp.semantic import conflict

ROOT = Path(__file__).resolve().parent.parent.parent
WARP = str(ROOT / "scripts" / "warp.py")


def snap(r):
    return (r.git("status", "--porcelain=v1"), r.git("ls-files", "--stage"), r.git("for-each-ref"), (r.path / "f.txt").read_bytes() if (r.path / "f.txt").exists() else b"")


def base_repo(make_repo, content="a\nb\nc\n", name="f.txt"):
    r = make_repo()
    r.commit("base", {name: content})
    return r


def two_sides(r, ours, theirs, name="f.txt"):
    r.branch("topic", checkout=True)
    r.commit("topic edit", {name: theirs})
    r.checkout("main")
    r.commit("main edit", {name: ours})


def test_not_in_conflict_state(repo):
    res = conflict.analyze(repo.path)
    assert res["conflicts"] == [] and "message" in res


def test_content_conflict_with_history_and_blame(make_repo):
    r = base_repo(make_repo)
    two_sides(r, "a\nOURS\nc\n", "a\nTHEIRS\nc\n")
    r.git("merge", "topic", check=False)
    before = snap(r)
    res = conflict.analyze(r.path)
    assert snap(r) == before
    assert res["operation"] == "merge" and res["theirs"]["ref"] == "MERGE_HEAD" and res["merge_base"]
    c = res["conflicts"][0]
    assert c["conflict_type"] == "both-modified" and set(c["stages"]) == {"1", "2", "3"}
    reg = c["regions"][0]
    assert reg["ours"]["text"] == "OURS" and reg["theirs"]["text"] == "THEIRS" and reg["start_line"] == 2
    assert reg["relationship_hint"]["hint"] == "overlapping" and "hint only" in reg["relationship_hint"]["note"]
    assert c["history"]["ours"][0]["subject"] == "main edit" and c["history"]["theirs"][0]["subject"] == "topic edit"
    assert reg["blame"]["ours"][0]["summary"] == "main edit" and reg["blame"]["theirs"][0]["summary"] == "topic edit"
    assert res["swap_note"] is None


def test_diff3_markers_and_hints(make_repo):
    r = base_repo(make_repo)
    r.git("config", "merge.conflictstyle", "diff3")
    two_sides(r, "a\nOURS\nc\n", "a\nTHEIRS\nc\n")
    r.git("merge", "topic", check=False)
    res = conflict.analyze(r.path)
    reg = res["conflicts"][0]["regions"][0]
    assert reg["has_diff3_base"] and reg["base"]["text"] == "b" and reg["base_range"]


def test_region_hints_unit():
    from gitwarp.semantic.conflict import parse_markers, region_hint
    txt = "<<<<<<< HEAD\nx = 1\n=======\nx = 1\n>>>>>>> t\n<<<<<<< HEAD\nfoo( )\n=======\nfoo()\n>>>>>>> t\n"
    regs, w = parse_markers(txt)
    assert [region_hint(r)["hint"] for r in regs] == ["identical-change", "whitespace-only-difference"]
    regs, _ = parse_markers("<<<<<<< a\nnew1\n||||||| base\n=======\nnew2\n>>>>>>> b\n")
    assert region_hint(regs[0])["hint"] == "independent-hunks"
    regs, _ = parse_markers("<<<<<<< a\nsame\n||||||| base\nsame\n=======\nchanged\n>>>>>>> b\n")
    assert region_hint(regs[0])["hint"] == "only-theirs-changed"
    regs, w = parse_markers("<<<<<<< a\nx\n=======\ny\n")
    assert regs == [] and "unterminated" in w[0]


def test_crlf_conflict(make_repo):
    r = base_repo(make_repo, "a\r\nb\r\nc\r\n")
    r.git("config", "core.autocrlf", "false")
    two_sides(r, "a\r\nOURS\r\nc\r\n", "a\r\nTHEIRS\r\nc\r\n")
    r.git("merge", "topic", check=False)
    c = conflict.analyze(r.path)["conflicts"][0]
    assert c["line_endings"] == "crlf" and c["regions"][0]["ours"]["text"] == "OURS" and c["regions"][0]["theirs"]["text"] == "THEIRS"


def test_delete_modify(make_repo):
    r = base_repo(make_repo)
    r.branch("topic", checkout=True)
    r.git("rm", "-q", "f.txt")
    r.git("commit", "-qm", "delete")
    r.checkout("main")
    r.commit("modify", {"f.txt": "a\nB\nc\n"})
    r.git("merge", "topic", check=False)
    c = conflict.analyze(r.path)["conflicts"][0]
    assert c["conflict_type"] == "deleted-by-theirs" and c["relationship_hint"]["hint"] == "delete-vs-modify"
    assert set(c["stages"]) == {"1", "2"}


def test_add_add(make_repo):
    r = make_repo().seed(1)
    r.branch("topic", checkout=True)
    r.commit("t", {"new.txt": "theirs\n"})
    r.checkout("main")
    r.commit("m", {"new.txt": "ours\n"})
    r.git("merge", "topic", check=False)
    c = conflict.analyze(r.path)["conflicts"][0]
    assert c["conflict_type"] == "both-added" and c["relationship_hint"]["hint"] == "add-add-different" and set(c["stages"]) == {"2", "3"}


def test_rename_vs_delete_hint(make_repo):
    r = base_repo(make_repo, "one\ntwo\nthree\nfour\nfive\n")
    r.branch("topic", checkout=True)
    r.git("mv", "f.txt", "g.txt")
    r.git("commit", "-qm", "rename")
    r.checkout("main")
    r.commit("modify", {"f.txt": "one\nTWO\nthree\nfour\nfive\n"})
    r.git("merge", "topic", check=False)
    res = conflict.analyze(r.path)
    # git may auto-resolve a rename+modify; if it conflicted, hints must be sane
    for c in res["conflicts"]:
        assert c["relationship_hint"]["hint"]


def test_rebase_swap_note(make_repo):
    r = base_repo(make_repo)
    two_sides(r, "a\nOURS\nc\n", "a\nTHEIRS\nc\n")
    r.checkout("topic")
    r.git("rebase", "main", check=False)
    res = conflict.analyze(r.path)
    assert res["operation"] == "rebase" and "SWAPPED" in res["swap_note"] and res["theirs"]["ref"] == "REBASE_HEAD"
    c = res["conflicts"][0]
    assert c["history"]["theirs"][0]["subject"] == "topic edit" and c["history"]["ours"][0]["subject"] == "main edit"
    assert "upstream" in c["ours_label"] and "YOUR commit" in c["theirs_label"]
    assert res["rebase"]


def test_cherry_pick_and_revert(make_repo):
    r = base_repo(make_repo)
    two_sides(r, "a\nOURS\nc\n", "a\nTHEIRS\nc\n")
    r.git("cherry-pick", "topic", check=False)
    res = conflict.analyze(r.path)
    assert res["operation"] == "cherry-pick" and res["theirs"]["ref"] == "CHERRY_PICK_HEAD" and res["conflicts"]
    r.git("cherry-pick", "--abort")
    r.git("revert", "--no-edit", "HEAD~1", check=False)
    res = conflict.analyze(r.path)
    assert res["operation"] == "revert" or res["conflicts"] == []


def test_binary_and_missing_worktree(make_repo):
    r = make_repo()
    r.commit("base")
    (r.path / "b.bin").write_bytes(b"\x00\x01base")
    r.git("add", "-A"); r.git("commit", "-qm", "bin")
    r.branch("topic", checkout=True)
    (r.path / "b.bin").write_bytes(b"\x00\x02theirs")
    r.git("commit", "-qam", "t")
    r.checkout("main")
    (r.path / "b.bin").write_bytes(b"\x00\x03ours")
    r.git("commit", "-qam", "m")
    r.git("merge", "topic", check=False)
    c = conflict.analyze(r.path)["conflicts"][0]
    assert c["binary"] and c["relationship_hint"]["hint"] == "binary-choose-a-side" and c["regions"] == []
    (r.path / "b.bin").unlink()
    c = conflict.analyze(r.path)["conflicts"][0]
    assert c["worktree_missing"]


def test_cli_outside_and_inside(make_repo, repo):
    p = subprocess.run([sys.executable, WARP, "conflict", "--repo", str(repo.path)], capture_output=True, text=True)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["conflicts"] == [] and out["message"]
    r = make_repo().seed(1)
    r.merge_conflict()
    p = subprocess.run([sys.executable, WARP, "conflict", "--repo", str(r.path)], capture_output=True, text=True)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["conflicts"][0]["path"] == "conflict.txt" and out["read_only"] is True
    p = subprocess.run([sys.executable, WARP, "conflict", "--repo", str(r.path / "nonexistent")], capture_output=True, text=True)
    assert p.returncode != 0 and "error" in json.loads(p.stdout)
