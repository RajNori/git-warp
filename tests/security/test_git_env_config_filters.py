"""GW-SEC-01: repository-filter enumeration must fail CLOSED (truncated / errored / unparseable => refuse, never run)."""
import subprocess as sp

import pytest

from gitwarp.core import git


def _marker(tmp_path, name):
    log = tmp_path / f"{name}.log"
    s = tmp_path / f"{name}.sh"
    s.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{log}"\ncat "$1"\nexit 0\n')
    s.chmod(0o755)
    return s, log


def _many_filters(repo, n, evil_script):
    """``n`` harmless filter definitions in .git/config, then the evil one LAST (a truncated read would miss it)."""
    cfg = repo.path / ".git" / "config"
    with open(cfg, "a") as f:
        for i in range(n):
            f.write(f'[filter "f{i:06d}"]\n\tclean = true\n')
        f.write(f'[filter "zz-evil"]\n\tclean = {evil_script}\n\trequired = true\n')
    repo.write(".gitattributes", "*.txt filter=zz-evil\n")
    repo.write("f0.txt", "changed\n")


def _never_runs(repo, log, reason):
    for args in (["status", "--porcelain"], ["diff", "--stat"], ["ls-files", "-m"], ["diff", "-U0", "HEAD"]):
        with pytest.raises(git.GitRefused) as ei:
            git.run(args, cwd=repo.path)
        assert ei.value.reason == reason
        assert not log.exists(), (args, log.read_text())


def test_more_filters_than_a_command_line_can_neutralise_is_refused(repo, tmp_path):
    s, log = _marker(tmp_path, "evil-many")
    _many_filters(repo, 30_000, s)
    _never_runs(repo, log, "too_many_filters")
    # commands that cannot run filters are unaffected
    assert git.run(["rev-parse", "HEAD"], cwd=repo.path).ok and git.log_commits("HEAD", cwd=repo.path)


def test_truncated_enumeration_is_refused_not_partially_applied(repo, tmp_path, monkeypatch):
    s, log = _marker(tmp_path, "evil-trunc")
    monkeypatch.setattr(git, "_FILTER_ENUM_CAP", 4096)
    _many_filters(repo, 2_000, s)
    _never_runs(repo, log, "filter_enumeration_incomplete")


def test_output_beyond_the_real_cap_is_refused(repo, tmp_path):
    s, log = _marker(tmp_path, "evil-huge")
    _many_filters(repo, git._FILTER_ENUM_CAP // 30 + 20_000, s)         # more than the cap in `git config` output
    with pytest.raises(git.GitRefused) as ei:
        git.run(["diff", "--stat"], cwd=repo.path)
    assert ei.value.reason == "filter_enumeration_incomplete"
    assert not log.exists()


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_enumeration_timeout_or_error_fails_closed(repo, tmp_path, monkeypatch, failure):
    s, log = _marker(tmp_path, "evil-" + failure)
    repo.git("config", "filter.zz-evil.clean", str(s))
    repo.write(".gitattributes", "*.txt filter=zz-evil\n")
    repo.write("f0.txt", "changed\n")
    real = git._exec

    def boom(argv, *a, **k):
        if "config" in argv and "--get-regexp" in argv:
            raise sp.TimeoutExpired(argv, 10) if failure == "timeout" else PermissionError("denied")
        return real(argv, *a, **k)

    monkeypatch.setattr(git, "_exec", boom)
    with pytest.raises(git.GitRefused) as ei:
        git.run(["status", "--porcelain"], cwd=repo.path)
    assert ei.value.reason == "filter_enumeration_incomplete"
    assert not log.exists()


def test_unexpected_config_exit_code_fails_closed(repo, tmp_path, monkeypatch):
    s, log = _marker(tmp_path, "evil-rc")
    repo.git("config", "filter.zz-evil.clean", str(s))
    repo.write(".gitattributes", "*.txt filter=zz-evil\n")
    repo.write("f0.txt", "changed\n")
    real = git._exec
    monkeypatch.setattr(git, "_exec", lambda argv, *a, **k: (2, "", "boom", False) if "--get-regexp" in argv else real(argv, *a, **k))
    with pytest.raises(git.GitRefused):
        git.run(["status", "--porcelain"], cwd=repo.path)
    assert not log.exists()


def test_filter_names_that_cannot_be_expressed_as_overrides_are_refused(repo, tmp_path):
    s, log = _marker(tmp_path, "evil-eq")
    with open(repo.path / ".git" / "config", "a") as f:
        f.write(f'[filter "a=b"]\n\tclean = {s}\n\trequired = true\n')
    repo.write(".gitattributes", "*.txt filter=a=b\n")
    repo.write("f0.txt", "changed\n")
    with pytest.raises(git.GitRefused):
        git.run(["status", "--porcelain"], cwd=repo.path)
    assert not log.exists()


def test_filter_names_differing_only_by_case_are_all_neutralised(repo, tmp_path):
    s, log = _marker(tmp_path, "evil-case")
    for name in ("Evil", "evil", "EVIL"):
        repo.git("config", f"filter.{name}.clean", str(s))
        repo.git("config", f"filter.{name}.required", "true")
    repo.write(".gitattributes", "a.txt filter=Evil\nb.txt filter=evil\nc.txt filter=EVIL\n")
    repo.commit("attrs", {"a.txt": "a\n", "b.txt": "b\n", "c.txt": "c\n"})
    log.unlink(missing_ok=True)                       # the test's own commit may have run them (before the neutralising runner)
    for n in "abc":
        repo.write(f"{n}.txt", "changed\n")
    assert {e.path for e in git.working_tree_status(repo.path)} >= {"a.txt", "b.txt", "c.txt"}
    git.run(["diff", "--stat"], cwd=repo.path)
    assert not log.exists()


def test_normal_repositories_are_unaffected(repo):
    repo.git("config", "filter.one.clean", "true")
    repo.write("f0.txt", "changed\n")
    assert any(e.path == "f0.txt" for e in git.working_tree_status(repo.path))
    assert git.run(["diff", "--stat"], cwd=repo.path).ok
