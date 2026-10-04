"""Hostile environment and hostile repository config, exercised directly through core/git.py helpers.

(The end-to-end CLI versions live in test_convergence_contracts.py.)  Every marker script records an execution; each
test asserts the marker was never run.
"""
import pytest

from gitwarp.core import git


def _marker(tmp_path, name, passthrough=False):
    log = tmp_path / f"{name}.log"
    s = tmp_path / f"{name}.sh"
    s.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{log}"\n' + ('cat "$1"\n' if passthrough else "") + "exit 0\n")
    s.chmod(0o755)
    return s, log


def _touch_everything(repo):
    """Drive the read paths Git Warp uses, through the central helpers."""
    p = repo.path
    git.working_tree_status(p, untracked="all")
    git.changed_files(p)
    git.numstat(p, ["HEAD"])
    git.diff(["HEAD"], p)
    git.log_commits("HEAD", paths=["f0.txt"], with_files=True, cwd=p)
    git.log_commits("HEAD", extra=["-S0"], cwd=p)
    git.commit_metadata("HEAD", p, with_files=True)
    git.show_commit("HEAD", p, patch=True)
    git.blame("f0.txt", "HEAD", cwd=p)
    git.tracked_files(p)
    git.show_file("HEAD", "f0.txt", p)
    git.ls_files_stage(p)
    git.branches(p)
    git.stashes(p)
    git.worktrees(p)
    git.reflog("HEAD", 10, p)
    git.run(["ls-files", "-m", "-o", "--exclude-standard"], cwd=p)
    git.run(["diff", "-U0", "HEAD"], cwd=p)
    git.run(["grep", "-I", "-l", "-F", "-e", "f", "HEAD"], cwd=p)


# --------------------------------------------------------------------------- repository config

def test_repo_filters_do_not_run_during_status_diff_ls_files(repo, tmp_path):
    """filter.<n>.clean/smudge/process run inside `git status`/`git diff` to compare content: they must not."""
    s, log = _marker(tmp_path, "filter", passthrough=True)
    repo.write(".gitattributes", "*.txt filter=evil\n")
    repo.commit("attrs")
    for key in ("clean", "smudge", "process"):
        repo.git("config", f"filter.evil.{key}", str(s))
    repo.git("config", "filter.evil.required", "true")
    repo.write("f0.txt", "changed\n")
    _touch_everything(repo)
    assert not log.exists(), log.read_text() if log.exists() else ""
    # and the analysis still sees the change
    assert any(e.path == "f0.txt" for e in git.working_tree_status(repo.path))


def test_repo_textconv_and_diff_command_do_not_run_in_blame_log_show_diff(repo, tmp_path):
    s, log = _marker(tmp_path, "textconv", passthrough=True)
    repo.write(".gitattributes", "*.txt diff=bk\n")
    repo.commit("attrs")
    repo.git("config", "diff.bk.textconv", str(s))
    repo.git("config", "diff.bk.command", str(s))
    repo.git("config", "diff.bk.cachetextconv", "true")
    repo.write("f0.txt", "changed\n")
    _touch_everything(repo)
    assert not log.exists()


def test_diff_external_config_and_env_do_not_run(repo, tmp_path, monkeypatch):
    s, log = _marker(tmp_path, "external")
    repo.git("config", "diff.external", str(s))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(s))
    repo.write("f0.txt", "changed\n")
    _touch_everything(repo)
    assert not log.exists()


def test_fsmonitor_and_hooks_do_not_run(repo, tmp_path):
    s, log = _marker(tmp_path, "fsmonitor")
    repo.git("config", "core.fsmonitor", str(s))
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hlog = tmp_path / "hooks.log"
    for h in ("post-index-change", "reference-transaction", "post-checkout", "post-commit", "post-merge", "pre-commit"):
        (hooks / h).write_text(f'#!/bin/sh\necho {h} >> "{hlog}"\n')
        (hooks / h).chmod(0o755)
    repo.git("config", "core.hooksPath", str(hooks))
    hooks_dir = repo.path / ".git" / "hooks"
    (hooks_dir / "post-index-change").write_text(f'#!/bin/sh\necho local >> "{hlog}"\n')
    (hooks_dir / "post-index-change").chmod(0o755)
    repo.write("dirty.txt", "d\n")
    _touch_everything(repo)
    assert not log.exists() and not hlog.exists()


def test_signature_verification_and_pager_config_do_not_run(repo, tmp_path):
    gpg, glog = _marker(tmp_path, "gpg")
    pager, plog = _marker(tmp_path, "pager", passthrough=True)
    repo.git("config", "log.showSignature", "true")
    repo.git("config", "gpg.program", str(gpg))
    repo.git("config", "core.pager", str(pager))
    repo.git("config", "pager.log", str(pager))
    repo.git("config", "pager.diff", "true")
    _touch_everything(repo)
    assert not glog.exists() and not plog.exists()


def test_credential_ssh_and_askpass_config_are_neutralized(repo, tmp_path):
    s, log = _marker(tmp_path, "net")
    for key in ("core.sshCommand", "core.askPass", "credential.helper", "core.editor", "sequence.editor"):
        repo.git("config", key, str(s))
    _touch_everything(repo)
    assert not log.exists()


def test_include_path_still_cannot_reach_the_suppressed_keys(repo, tmp_path):
    """An include file is just more config: the command-level overrides still win over what it defines."""
    s, log = _marker(tmp_path, "inc")
    inc = tmp_path / "extra.cfg"
    inc.write_text(f"[core]\n\tfsmonitor = {s}\n[diff]\n\texternal = {s}\n")
    repo.git("config", "include.path", str(inc))
    repo.write("f0.txt", "changed\n")
    _touch_everything(repo)
    assert not log.exists()


# --------------------------------------------------------------------------- environment

def test_env_config_injection_is_ignored(repo, tmp_path, monkeypatch):
    s, log = _marker(tmp_path, "envcfg")
    cfg = tmp_path / "global.cfg"
    cfg.write_text(f"[core]\n\tfsmonitor = {s}\n[diff]\n\texternal = {s}\n")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(s))
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "diff.external")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", str(s))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.fsmonitor={s}'")
    monkeypatch.setenv("GIT_CONFIG", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(cfg))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(s))
    monkeypatch.setenv("GIT_PAGER", str(s))
    monkeypatch.setenv("PAGER", str(s))
    monkeypatch.setenv("GIT_SSH_COMMAND", str(s))
    monkeypatch.setenv("GIT_ASKPASS", str(s))
    monkeypatch.setenv("GIT_EDITOR", str(s))
    repo.write("f0.txt", "changed\n")
    _touch_everything(repo)
    assert not log.exists()


def test_env_cannot_redirect_the_repository(repo, make_repo, tmp_path, monkeypatch):
    decoy = make_repo("decoy", branch="decoybranch").seed(2)
    expected_head = repo.sha()                     # computed before the hostile environment is installed
    for k, v in {"GIT_DIR": decoy.path / ".git", "GIT_WORK_TREE": decoy.path, "GIT_INDEX_FILE": tmp_path / "hostile-index",
                 "GIT_OBJECT_DIRECTORY": tmp_path / "no-objects", "GIT_ALTERNATE_OBJECT_DIRECTORIES": tmp_path / "alt",
                 "GIT_COMMON_DIR": decoy.path / ".git", "GIT_NAMESPACE": "hostile", "GIT_CEILING_DIRECTORIES": str(repo.path.parent)}.items():
        monkeypatch.setenv(k, str(v))
    assert git.repo_root(repo.path).samefile(repo.path)
    assert git.current_branch(repo.path) == "main"
    assert git.head_sha(repo.path) == expected_head
    assert git.git_dir(repo.path).samefile(repo.path / ".git")
    assert git.common_dir(repo.path).samefile(repo.path / ".git")
    repo.write("f0.txt", "changed\n")
    _touch_everything(repo)
    assert not (tmp_path / "hostile-index").exists()


def test_trace_variables_write_nothing(repo, tmp_path, monkeypatch, capfd):
    trace = tmp_path / "trace"
    for k in ("GIT_TRACE", "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF", "GIT_TRACE_PACKET", "GIT_TRACE_SETUP",
              "GIT_TRACE_PERFORMANCE", "GIT_TRACE_CURL"):
        monkeypatch.setenv(k, str(trace) if k != "GIT_TRACE" else "1")
    r = git.run(["status", "--porcelain"], cwd=repo.path)
    _touch_everything(repo)
    assert not trace.exists()
    assert "trace" not in r.stderr.lower()
    assert "trace" not in capfd.readouterr().err.lower()


def test_hostile_env_does_not_change_output_locale_or_prompt(repo, monkeypatch):
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    r = git.run(["rev-parse", "--verify", "nope-nope"], cwd=repo.path)
    assert not r.ok and "fatal" in r.stderr.lower() and "schwerwiegend" not in r.stderr.lower()


# --------------------------------------------------------------------------- documented boundary (trusted user config)

def test_user_global_filter_is_trusted_but_repo_filter_is_not(repo, tmp_path, monkeypatch):
    """Documented boundary: filters from the USER's global config keep working (e.g. git-lfs); filters from the
    repository's own config are emptied."""
    home = tmp_path / "home"
    home.mkdir()
    gs, glog = _marker(tmp_path, "gfilter", passthrough=True)
    ls, llog = _marker(tmp_path, "lfilter", passthrough=True)
    (home / ".gitconfig").write_text(f"[filter \"gl\"]\n\tclean = {gs}\n")
    repo.write(".gitattributes", "g.txt filter=gl\nl.txt filter=lo\n")
    repo.commit("attrs", {"g.txt": "g\n", "l.txt": "l\n"})
    repo.git("config", "filter.lo.clean", str(ls))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))      # a non-null redirection is dropped...
    repo.write("g.txt", "g2\n")
    repo.write("l.txt", "l2\n")
    git.working_tree_status(repo.path)
    git.run(["diff", "--stat"], cwd=repo.path)
    assert not llog.exists()                      # repository-scoped filter: never
    assert glog.exists()                          # ... so the real ~/.gitconfig applies and its (trusted) filter runs
