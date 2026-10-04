"""Trusted policy: built-in floor < user policy < repository policy; a lower-trust source can only TIGHTEN."""
import os
import socket
import time

import pytest

from gitwarp.core.config import DEFAULT_PROTECTED, MAX_POLICY_BYTES, Config, load_config
from gitwarp.safety.classifier import classify_command

REL = os.path.join(".claude", "git-warp.local.md")


def write(base, body, raw=None):
    p = base / REL
    p.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        p.write_bytes(raw)
    else:
        p.write_text(f"---\n{body}\n---\n")
    return p


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home, repo


def verdict(cmd, cfg, branch="feature/x"):
    return classify_command(cmd, cfg, branch)


# ---------------------------------------------------------------- floor only
def test_floor_only(dirs):
    home, repo = dirs
    cfg = load_config(repo)
    assert cfg.protected_branches == list(DEFAULT_PROTECTED) and cfg.safety_mode == "standard" and cfg.warnings == []
    assert cfg.memory_enabled and cfg.recorder_enabled and cfg.recorder_retention_days == 30
    assert load_config(None).protected_branches == list(DEFAULT_PROTECTED)


# ---------------------------------------------------------------- user only / repo only
def test_user_only(dirs):
    home, repo = dirs
    write(home, "protected_branches: [staging]\nsafety_mode: strict\nsensitive_paths: [secrets/*]")
    cfg = load_config(repo)
    assert cfg.protected_branches == list(DEFAULT_PROTECTED) + ["staging"] and cfg.safety_mode == "strict"
    assert cfg.sensitive_paths == ["secrets/*"] and cfg.warnings == []
    assert load_config(None).safety_mode == "strict"          # user policy applies outside a repository too


def test_repo_only(dirs):
    home, repo = dirs
    write(repo, "protected_branches: [trunk]\nsafety_mode: strict\ntest_paths: [spec/*]\ninfra_paths: [ops/*]\nignored_paths: [gen/*]")
    cfg = load_config(repo)
    assert cfg.protected_branches == list(DEFAULT_PROTECTED) + ["trunk"] and cfg.safety_mode == "strict"
    assert (cfg.test_paths, cfg.infra_paths, cfg.ignored_paths) == (["spec/*"], ["ops/*"], ["gen/*"])


# ---------------------------------------------------------------- conflicts, both directions
def test_protected_branches_are_unioned_in_every_direction(dirs):
    home, repo = dirs
    write(home, "protected_branches: [staging, main]")
    write(repo, "protected_branches: [nothing-special, staging]")
    cfg = load_config(repo)
    assert cfg.protected_branches == list(DEFAULT_PROTECTED) + ["staging", "nothing-special"]
    for b in DEFAULT_PROTECTED + ("staging", "nothing-special"):
        assert verdict("git push --force origin " + b, cfg).decision == "deny", b


@pytest.mark.parametrize("user,repo_mode,expected", [
    ("strict", "standard", "strict"), ("standard", "strict", "strict"), ("strict", "strict", "strict"),
    ("standard", "standard", "standard"), (None, "strict", "strict"), ("strict", None, "strict"), (None, None, "standard"),
])
def test_safety_mode_is_the_strictest_of_all_sources(dirs, user, repo_mode, expected):
    home, repo = dirs
    if user:
        write(home, f"safety_mode: {user}")
    if repo_mode:
        write(repo, f"safety_mode: {repo_mode}")
    cfg = load_config(repo)
    assert cfg.safety_mode == expected
    assert verdict("git rebase -i HEAD~3", cfg).decision == ("deny" if expected == "strict" else "ask")


@pytest.mark.parametrize("user,repo_v,expected", [
    (False, True, False), (True, False, False), (False, False, False), (True, True, True), (None, False, False), (False, None, False),
])
@pytest.mark.parametrize("key", ["memory_enabled", "recorder_enabled"])
def test_privacy_switches_false_from_any_source_wins(dirs, key, user, repo_v, expected):
    home, repo = dirs
    if user is not None:
        write(home, f"{key}: {str(user).lower()}")
    if repo_v is not None:
        write(repo, f"{key}: {str(repo_v).lower()}")
    assert getattr(load_config(repo), key) is expected


@pytest.mark.parametrize("user,repo_v,expected", [(None, 7, 7), (None, 90, 30), (90, 7, 7), (90, 365, 90), (7, None, 7), (90, None, 90), (None, None, 30)])
def test_retention_repo_can_only_lower(dirs, user, repo_v, expected):
    home, repo = dirs
    if user is not None:
        write(home, f"recorder_retention_days: {user}")
    if repo_v is not None:
        write(repo, f"recorder_retention_days: {repo_v}")
    assert load_config(repo).recorder_retention_days == expected


# ---------------------------------------------------------------- unconditional rules cannot be configured away
@pytest.mark.parametrize("body", [
    "safety_mode: off", "guard_enabled: false", "protected_branches: []", "protected_branches:", "guard: off\nenabled: false",
    "safety_mode: off\nprotected_branches: []\nguard_enabled: false",
])
@pytest.mark.parametrize("where", ["user", "repo", "both"])
def test_hostile_values_never_disable_the_guard(dirs, body, where):
    home, repo = dirs
    if where in ("user", "both"):
        write(home, body)
    if where in ("repo", "both"):
        write(repo, body)
    cfg = load_config(repo)
    assert cfg.warnings, "every ignored value is reported"
    assert cfg.safety_mode == "standard" and "main" in cfg.protected_branches
    for cmd in ("git reset --hard", "git clean -fd", "git push --force origin main", "git checkout -f", "git stash clear"):
        assert verdict(cmd, cfg, "main").decision == "deny", cmd


def test_warning_names_the_source(dirs):
    home, repo = dirs
    write(home, "guard_enabled: false")
    write(repo, "bogus: 1")
    w = " ".join(load_config(repo).warnings)
    assert "user policy" in w and "repository policy" in w and "guard_enabled" in w and "bogus" in w


# ---------------------------------------------------------------- malformed / hostile files
def test_malformed_repo_file_does_not_disable_user_policy(dirs):
    home, repo = dirs
    write(home, "safety_mode: strict")
    write(repo, "", raw=b"not frontmatter\n\x00\xff")
    cfg = load_config(repo)
    assert cfg.safety_mode == "strict" and cfg.warnings


@pytest.mark.parametrize("raw", [
    b"", b"---\n", b"---\nsafety_mode strict\n---\n", b"\xff\xfe\x00\x00", b"---\n" + b"\x00" * 100 + b"\n---\n",
    b"---\nprotected_branches: [a, b\n---\n", b"---\nk: v\n", b"---\n" + b"- x\n" * 50, b"---\r\nsafety_mode: strict\r\n---\r\n",
])
def test_malformed_files_never_crash(dirs, raw):
    home, repo = dirs
    write(repo, "", raw=raw)
    cfg = load_config(repo)
    assert isinstance(cfg, Config) and "main" in cfg.protected_branches


def test_huge_file_is_ignored_with_a_warning_and_fast(dirs):
    home, repo = dirs
    write(repo, "", raw=b"---\nprotected_branches: [" + b"x," * MAX_POLICY_BYTES + b"]\n---\n")
    t0 = time.monotonic()
    cfg = load_config(repo)
    assert time.monotonic() - t0 < 2.0
    assert cfg.protected_branches == list(DEFAULT_PROTECTED) and any("larger" in w for w in cfg.warnings)


def test_many_list_items_are_capped(dirs):
    home, repo = dirs
    items = ", ".join(f"b{i}" for i in range(5000))
    write(repo, f"protected_branches: [{items}]")
    t0 = time.monotonic()
    cfg = load_config(repo)
    assert time.monotonic() - t0 < 2.0 and len(cfg.protected_branches) <= len(DEFAULT_PROTECTED) + 200


def test_symlinked_policy_file_is_not_followed(dirs, tmp_path):
    home, repo = dirs
    outside = tmp_path / "outside.md"
    outside.write_text("---\nprotected_branches: [leaked]\nsafety_mode: strict\n---\n")
    (repo / ".claude").mkdir()
    os.symlink(outside, repo / REL)
    cfg = load_config(repo)
    assert "leaked" not in cfg.protected_branches and cfg.safety_mode == "standard"
    assert any("symlink" in w or "not a plain" in w for w in cfg.warnings)


def test_symlinked_user_policy_is_not_followed(dirs, tmp_path):
    home, repo = dirs
    outside = tmp_path / "outside.md"
    outside.write_text("---\nsafety_mode: strict\n---\n")
    (home / ".claude").mkdir()
    os.symlink(outside, home / REL)
    assert load_config(repo).safety_mode == "standard"


def test_dangling_symlink_and_directory_and_fifo_are_ignored_without_hanging(dirs, tmp_path):
    home, repo = dirs
    (repo / ".claude").mkdir()
    os.symlink(tmp_path / "does-not-exist", repo / REL)
    assert load_config(repo).warnings
    os.unlink(repo / REL)
    os.mkdir(repo / REL)
    assert load_config(repo).warnings
    os.rmdir(repo / REL)
    os.mkfifo(repo / REL)
    t0 = time.monotonic()
    cfg = load_config(repo)
    assert time.monotonic() - t0 < 2.0 and cfg.warnings
    os.unlink(repo / REL)
    s = socket.socket(socket.AF_UNIX)
    try:
        import tempfile
        short = tempfile.mkdtemp(prefix="gw", dir="/tmp")
        s.bind(os.path.join(short, "s"))
        os.rename(os.path.join(short, "s"), repo / REL)
        assert load_config(repo).warnings
    finally:
        s.close()


def test_unknown_keys_are_warned_and_known_keys_still_apply(dirs):
    home, repo = dirs
    write(repo, "bogus: 1\nsafety_mode: strict\nprotected_branches: [trunk]")
    cfg = load_config(repo)
    assert cfg.safety_mode == "strict" and "trunk" in cfg.protected_branches
    assert any("bogus" in w for w in cfg.warnings)


def test_repo_rooted_at_home_reads_the_single_file_once(dirs):
    home, _ = dirs
    write(home, "protected_branches: [staging]")
    cfg = load_config(home)
    assert cfg.protected_branches.count("staging") == 1 and cfg.warnings == []


def test_no_home_in_environment_still_loads(tmp_path, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    repo = tmp_path / "r"
    repo.mkdir()
    cfg = load_config(repo)
    assert "main" in cfg.protected_branches


def test_cli_override_flags_follow_the_same_tighten_only_rule(repo):
    import json
    import subprocess
    import sys
    from tests.conftest import SCRIPTS
    p = subprocess.run([sys.executable, str(SCRIPTS / "warp.py"), "guard", "check", "git push --force origin main", "--repo", str(repo.path),
                        "--protected", "only-this", "--mode", "standard"], capture_output=True, text=True, timeout=30)
    assert json.loads(p.stdout)["decision"] == "deny"
