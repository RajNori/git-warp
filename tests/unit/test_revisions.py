"""core/revisions.py: the one revision-normalization path (GW-REV contracts, unit level)."""
import hashlib
import os
import subprocess

import pytest

from gitwarp.core import git, revisions
from gitwarp.core.revisions import Revision, RevisionError, resolve, resolve_range

FULL = 40


def reason_of(label, repo, **kw):
    with pytest.raises(RevisionError) as ei:
        resolve(label, repo.path, **kw)
    return ei.value.reason


# --------------------------------------------------------------------------- the normal syntax the product needs

def test_head_and_relative(repo):
    head = resolve("HEAD", repo.path)
    assert isinstance(head, Revision) and head.label == "HEAD" and head.sha == repo.sha()
    assert len(head.sha) == FULL and head.sha == head.sha.lower()
    assert resolve("HEAD~1", repo.path).sha == repo.sha("HEAD~1")
    assert resolve("HEAD^", repo.path).sha == repo.sha("HEAD~1")


def test_branch_with_and_without_slash(repo):
    repo.branch("feature/test", "HEAD~1")
    assert resolve("main", repo.path).sha == repo.sha("main")
    assert resolve("feature/test", repo.path).sha == repo.sha("HEAD~1")


def test_lightweight_and_annotated_tags_peel_to_commits(repo):
    repo.git("tag", "v1", "HEAD~1")
    repo.git("tag", "-a", "v1-annotated", "-m", "annotated", "HEAD~1")
    tag_object = repo.git("rev-parse", "v1-annotated")
    commit = repo.sha("HEAD~1")
    assert tag_object != commit
    assert resolve("v1", repo.path).sha == commit
    assert resolve("v1-annotated", repo.path).sha == commit          # peeled, not the tag object id
    assert resolve("v1-annotated", repo.path, kind="tag").sha == tag_object


def test_full_and_short_sha(repo):
    sha = repo.sha()
    assert resolve(sha, repo.path).sha == sha
    assert resolve(sha[:8], repo.path).sha == sha
    assert resolve(sha.upper()[:12], repo.path).sha == sha            # git accepts upper-case hex; we return lower-case


def test_other_object_kinds_are_required_explicitly(repo):
    tree = repo.git("rev-parse", "HEAD^{tree}")
    assert resolve("HEAD", repo.path, kind="tree").sha == tree
    blob = repo.git("rev-parse", "HEAD:f0.txt")
    assert reason_of(blob, repo) == "wrong_object_type"                 # a blob where a commit is required
    assert reason_of("HEAD:f0.txt", repo) == "unsupported_syntax"       # rev:path is not revision syntax here
    assert resolve(blob, repo.path, kind="blob").sha == blob
    assert reason_of("HEAD", repo, kind="blob") == "wrong_object_type"
    with pytest.raises(ValueError):
        resolve("HEAD", repo.path, kind="banana")


def test_revision_objects_are_immutable_and_idempotent(repo):
    r = resolve("HEAD", repo.path)
    with pytest.raises(Exception):
        r.sha = "0" * 40
    assert resolve(r, repo.path) is r
    assert str(r) == r.sha and r.short == r.sha[:8]
    with pytest.raises(RevisionError):
        resolve(r, repo.path, kind="tree")


# --------------------------------------------------------------------------- invalid input

def test_invalid_ref_is_not_found(repo):
    assert reason_of("no-such-branch", repo) == "not_found"
    assert reason_of("deadbeef" * 5, repo) == "not_found"
    assert revisions.try_resolve("no-such-branch", repo.path) is None
    assert git.rev_parse("no-such-branch", repo.path) is None


@pytest.mark.parametrize("label", [
    "-x", "--output=SENTINEL", "-oSENTINEL", "--help", "--version", "--git-dir=SENTINEL", "--work-tree=SENTINEL",
    "--exec-path=SENTINEL", "--upload-pack=evil", "-", "--", "-p",
])
def test_option_shaped_input_is_rejected_before_git_runs(repo, tmp_path, label):
    sentinel = tmp_path / "PWNED"
    value = label.replace("SENTINEL", str(sentinel))
    assert reason_of(value, repo) == "option_shaped"
    assert not sentinel.exists()
    # the rest of the toolbox refuses it too (no helper turns it into a Git argument)
    with pytest.raises(ValueError):
        git.rev_parse(value, repo.path)
    with pytest.raises(ValueError):
        git.log_commits(value, cwd=repo.path)
    with pytest.raises(ValueError):
        git.merge_base(value, "HEAD", repo.path)
    assert not sentinel.exists()


@pytest.mark.parametrize("label", ["HEAD\n--output=x", "HEAD\x00", "\nHEAD", "HE AD", "HEAD\t", "HEAD\x1b[0m", "HEAD\x7f"])
def test_newline_nul_and_control_characters(repo, label):
    assert reason_of(label, repo) == "control_characters"


@pytest.mark.parametrize("label,reason", [
    ("", "empty"), (None, "invalid_type"), (123, "invalid_type"), (b"HEAD", "invalid_type"),
    ("a" * 300, "too_long"), (":/fix", "unsupported_syntax"), (":0:f0.txt", "unsupported_syntax"),
    ("HEAD..main", "unsupported_syntax"), ("main*", "forbidden_characters"), ("ma[in]", "forbidden_characters"),
    ("a\\b", "forbidden_characters"),
])
def test_other_rejected_syntax(repo, label, reason):
    assert reason_of(label, repo) == reason


def test_ambiguous_short_object_id(repo):
    """Two blobs whose ids share a 4-digit prefix: the short id is ambiguous and must not be guessed."""
    seen = {}
    pair = None
    n = 0
    while pair is None:
        data = f"blob-{n}\n".encode()
        oid = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        if oid[:4] in seen:
            pair = (seen[oid[:4]], data, oid[:4])
        seen[oid[:4]] = data
        n += 1
    for data in pair[:2]:
        subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo.path, input=data, check=True, capture_output=True)
    assert reason_of(pair[2], repo, kind="blob") == "ambiguous"


def test_unborn_head(empty_repo):
    assert reason_of("HEAD", empty_repo) == "unborn"
    assert reason_of("@", empty_repo) == "unborn"
    assert git.rev_parse("HEAD", empty_repo.path) is None
    assert git.log_commits("HEAD", cwd=empty_repo.path) == []


def test_shallow_repository(repo, tmp_path):
    dest = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo.path}", str(dest)], check=True, capture_output=True)
    assert git.is_shallow(dest)
    assert len(resolve("HEAD", dest).sha) == FULL
    with pytest.raises(RevisionError) as ei:
        resolve("HEAD~1", dest)
    assert ei.value.reason == "not_found" and "shallow" in str(ei.value)
    # the commit that exists in the origin but not in the shallow clone: not found, not a crash
    with pytest.raises(RevisionError):
        resolve(repo.sha("HEAD~2"), dest)


def test_moved_ref_is_resolved_once_and_the_sha_is_used_afterwards(repo):
    repo.branch("topic", "HEAD~2")
    rev = resolve("topic", repo.path)
    old = rev.sha
    repo.git("branch", "-f", "topic", "HEAD")                  # the ref moves after resolution
    assert repo.sha("topic") != old
    assert resolve(rev, repo.path).sha == old                  # a Revision never changes
    assert git.commit_metadata(rev, repo.path).sha == old      # later commands receive the sha
    assert git.merge_base(rev, "HEAD", repo.path) == old
    assert git.rev_list(["HEAD"], [rev], cwd=repo.path) == [repo.sha(), repo.sha("HEAD~1")]
    assert resolve("topic", repo.path).sha != old              # ... while a fresh resolution sees the new position


# --------------------------------------------------------------------------- ranges

def test_ranges_normalize_each_endpoint(repo):
    repo.branch("feature/test", "HEAD~1")
    a = resolve_range("HEAD~2..HEAD", repo.path)
    assert a.arg == f"{repo.sha('HEAD~2')}..{repo.sha()}" and not a.symmetric
    b = resolve_range("main...feature/test", repo.path)
    assert b.arg == f"{repo.sha('main')}...{repo.sha('feature/test')}" and b.symmetric
    assert resolve_range("HEAD~1..", repo.path).right.sha == repo.sha()          # empty endpoint = HEAD
    assert [r.label for r in (a.left, a.right)] == ["HEAD~2", "HEAD"]
    # log_commits accepts a range string, normalized internally
    assert [c.sha for c in git.log_commits("HEAD~2..HEAD", cwd=repo.path)] == [repo.sha(), repo.sha("HEAD~1")]


@pytest.mark.parametrize("spec", ["--output=x..HEAD", "HEAD..-x", "HEAD...--output=x", "-o..-o", "HEAD..a\nb"])
def test_hostile_range_endpoints_are_rejected(repo, spec):
    with pytest.raises(RevisionError) as ei:
        resolve_range(spec, repo.path)
    assert ei.value.reason in revisions.SYNTAX_REASONS
    with pytest.raises(ValueError):
        git.log_commits(spec, cwd=repo.path)


def test_range_with_unknown_endpoint_and_non_range(repo):
    with pytest.raises(RevisionError) as ei:
        resolve_range("HEAD..nope", repo.path)
    assert ei.value.reason == "not_found"
    with pytest.raises(RevisionError):
        resolve_range("HEAD", repo.path)


def test_a_range_string_is_never_a_single_revision(repo):
    assert reason_of("HEAD~1..HEAD", repo) == "unsupported_syntax"
    assert reason_of("HEAD...main", repo) == "unsupported_syntax"


# --------------------------------------------------------------------------- misc

def test_error_carries_label_and_stable_reason(repo):
    with pytest.raises(RevisionError) as ei:
        resolve("--output=/tmp/x", repo.path)
    e = ei.value
    assert isinstance(e, ValueError) and e.reason == "option_shaped" and e.label == "--output=/tmp/x"


def test_sha_of_fast_path_only_for_full_object_ids(repo):
    sha = repo.sha()
    assert revisions.sha_of(sha, repo.path) == sha
    assert revisions.sha_of(sha[:8], repo.path) == sha
    with pytest.raises(RevisionError):
        revisions.sha_of("--output=x", repo.path)
    assert revisions.oid_or_none(sha) == sha and revisions.oid_or_none(sha[:8]) is None and revisions.oid_or_none("-x") is None


def test_normal_refs_survive_the_whole_toolbox(repo):
    repo.git("tag", "-a", "rel", "-m", "m", "HEAD~1")
    assert git.show_commit("rel", repo.path).startswith("commit ")
    assert git.tracked_files(repo.path, rev="rel")
    assert git.show_file("rel", "f0.txt", repo.path) == "0\n"
    assert git.blame("f0.txt", "rel", cwd=repo.path)
    assert git.changed_files(repo.path, base="rel", head="HEAD") == ["f2.txt"]
    assert git.ahead_behind(repo.path, "rel") == (1, 0)
