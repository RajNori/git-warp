"""commits: output stays bounded and honest on huge change sets; small outputs are untouched."""
import json

from gitwarp.semantic import commits


def test_small_change_set_has_no_truncation_keys(repo):
    repo.write("f0.txt", "changed\n")
    out = commits.analyze(repo.path)
    assert "truncated" not in out and "omitted" not in out and "files_total" not in out


def test_large_change_set_is_capped_and_flagged(repo):
    for i in range(700):
        repo.write(f"bulk/f{i:04d}.txt", f"{i}\n")
    repo.write("f0.txt", "changed\n")
    out = commits.analyze(repo.path)
    assert out["truncated"] is True and out["omitted"]
    assert out["summary"]["files"] == 701                         # counts stay complete
    assert out["files_total"] == 701 and len(out["files"]) == commits.MAX_LISTED_FILES
    assert out["files"][0]["path"] == "f0.txt"                    # tracked changes are listed first
    assert all(len(c["paths"]) <= commits.MAX_PATHS_PER_LIST for c in out["clusters"])
    big = [c for c in out["clusters"] if c.get("paths_omitted")]
    assert big and big[0]["paths_total"] > commits.MAX_PATHS_PER_LIST
    assert any("truncated" in w for w in out["warnings"])
    assert len(json.dumps(out)) < 400_000
