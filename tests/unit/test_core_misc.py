from gitwarp.core.config import load_config, parse_frontmatter, Config
from gitwarp.core.redact import redact, redact_obj
from gitwarp.core.paths import classify_path


def test_config_defaults_and_missing(tmp_path):
    cfg = load_config(tmp_path)
    assert "main" in cfg.protected_branches and cfg.safety_mode == "standard" and not cfg.warnings
    assert load_config(None).memory_enabled


def test_config_parse(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/git-warp.local.md").write_text(
        "---\nprotected_branches: [main, trunk]\nsafety_mode: strict\nsensitive_paths:\n  - src/billing/*\nmemory_enabled: false\n"
        "recorder_retention_days: 7\nbogus: 1\n---\nbody\n")
    cfg = load_config(tmp_path)
    assert cfg.protected_branches == ["main", "trunk"] and cfg.safety_mode == "strict"
    assert cfg.sensitive_paths == ["src/billing/*"] and cfg.memory_enabled is False and cfg.recorder_retention_days == 7
    assert any("bogus" in w for w in cfg.warnings)


def test_config_malformed_falls_back(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/git-warp.local.md").write_text("---\nsafety_mode: yolo\nprotected_branches: []\nrecorder_retention_days: abc\n---\n")
    cfg = load_config(tmp_path)
    assert cfg.safety_mode == "standard" and cfg.protected_branches and len(cfg.warnings) == 3
    (tmp_path / ".claude/git-warp.local.md").write_text("no frontmatter")
    assert load_config(tmp_path).warnings


def test_redaction():
    cases = [
        "Authorization: Bearer abcdefghijklmnop1234", "export API_KEY=sk-live-abcdefghijklmnopqrstuv",
        "ghp_" + "a" * 36, "AKIAABCDEFGHIJKLMNOP", "postgres://user:hunter2@host/db",
        "curl --password hunter2 x", "password: 'swordfish'", "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
        "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4fw",
    ]
    for c in cases:
        out = redact(c)
        assert "[REDACTED]" in out, c
        for leak in ("hunter2", "swordfish", "abcdefghijklmnop1234", "AKIAABCDEFGHIJKLMNOP"):
            assert leak not in out
    assert redact("git commit -m 'fix typo'") == "git commit -m 'fix typo'"


def test_redact_obj():
    out = redact_obj({"password": "x", "nested": ["token=abc123", {"ok": "fine"}], "n": 3, "long": "a" * 900})
    assert out["password"] == "[REDACTED]" and "abc123" not in str(out) and out["n"] == 3 and len(out["long"]) < 600


def test_classify():
    c = classify_path
    assert {"test"} <= c("tests/test_x.py") and "source" not in c("tests/test_x.py")
    assert {"migration", "schema"} <= c("db/migrations/001_init.sql")
    assert {"lockfile", "dependency"} <= c("package-lock.json")
    assert {"ci", "infra"} <= c(".github/workflows/ci.yml")
    assert "infra" in c("Dockerfile") and "infra" in c("infra/main.tf")
    assert "secret-file" in c(".env.production")
    assert "sensitive" in c("src/auth/login.py") and "source" in c("src/auth/login.py")
    assert "docs" in c("README.md") and "generated" in c("dist/app.min.js")
    assert "ui" in c("src/components/Button.tsx")
    assert c("weird") and "sensitive" in classify_path("src/x.py", Config(sensitive_paths=["src/*.py"]))


def test_env_template_not_secret_file():
    assert "secret-file" not in classify_path(".env.example")
    assert "secret-file" in classify_path(".env.local")


def test_missing_cwd_is_not_a_repo(tmp_path):
    import pytest
    from gitwarp.core import git
    with pytest.raises(git.NotARepository):
        git.run(["status"], cwd=tmp_path / "nope")


def test_redact_is_linear_on_pathological_input():
    import time
    for s in ("git reset --hard " + "a-" * 50000, "x" * 200000, "token=" + "a" * 100000, "A" * 100000 + "secret"):
        t = time.time()
        out = redact(s)
        assert time.time() - t < 1.0
        assert len(out) < 9000
    assert "chars truncated" in redact("y" * 20000)


def test_redact_still_catches_secrets_after_prefix_bound():
    assert "hunter2" not in redact("MY_DB_PASSWORD=hunter2")
    assert "abc" not in redact("some.long-prefix_api_key: abc")


def test_author_fields_are_not_redacted_but_auth_secrets_are():
    assert redact("Author: Jane Doe <jane@example.com>") == "Author: Jane Doe <jane@example.com>"
    assert redact("AuthorDate: Fri Oct 2 2026") == "AuthorDate: Fri Oct 2 2026"
    assert "abc123" not in redact("auth=abc123")
    assert "xyz789" not in redact("AUTH_TOKEN=xyz789")
