"""Risk rules: each rule triggered and not triggered; plus secrets/diff helpers."""
import pytest

from gitwarp.analysis import risk
from gitwarp.analysis.common import dependency_drift, kind_of, mixed_concerns
from gitwarp.analysis.diffscan import parse_diff
from gitwarp.analysis.secrets import classify_line, scan_text


def ids(r):
    return [d["id"] for d in r["driver_details"]]


BASE = {"has_upstream": True, "ahead": 0, "behind": 0}

CASES = [
    # (rule id, expected level, triggering facts, near-miss facts)
    ("conflicted-paths", "HIGH", {"conflicted": 1}, {"conflicted": 0}),
    ("rebase-dirty", "HIGH", {"operation": "rebase", "dirty": 2}, {"operation": "rebase", "dirty": 0}),
    ("secret-material", "HIGH", {"secret_files": [".env"]}, {"secret_files": [], "secret_findings": 0}),
    ("secret-material", "HIGH", {"secret_findings": 1}, {"secret_findings": 0}),
    ("large-sensitive-migration", "HIGH", {"files_changed": 26, "sensitive": 1, "migrations": 1}, {"files_changed": 25, "sensitive": 1, "migrations": 1}),
    ("operation-in-progress", "MEDIUM", {"operation": "merge"}, {"operation": None}),
    ("diverged", "MEDIUM", {"ahead": 1, "behind": 1}, {"ahead": 2, "behind": 0}),
    ("sensitive-paths", "MEDIUM", {"sensitive": 1}, {"sensitive": 0}),
    ("migration", "MEDIUM", {"migrations": 1}, {"migrations": 0}),
    ("lockfile-drift", "MEDIUM", {"lock_drift": 1}, {"lock_drift": 0}),
    ("no-upstream-ahead", "MEDIUM", {"has_upstream": False, "ahead_of_base": 2, "base": "main"}, {"has_upstream": False, "ahead_of_base": 0}),
    ("large-diff", "MEDIUM", {"files_changed": 41}, {"files_changed": 40}),
    ("large-diff", "MEDIUM", {"lines_changed": 1001}, {"lines_changed": 1000}),
    ("detached-dirty", "MEDIUM", {"detached": True, "dirty": 1}, {"detached": True, "dirty": 0}),
]


@pytest.mark.parametrize("rule,level,trig,near", CASES)
def test_rule_triggered_and_not(rule, level, trig, near):
    hit = risk.evaluate({**BASE, **trig})
    assert rule in ids(hit)
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    assert rank[hit["level"]] >= rank[level]
    miss = risk.evaluate({**BASE, **near})
    assert rule not in ids(miss)


def test_low_when_nothing_triggers():
    r = risk.evaluate(dict(BASE))
    assert r["level"] == "LOW" and len(r["drivers"]) == 1 and "No risk rule" in r["drivers"][0]


def test_level_is_max_and_drivers_have_counts():
    r = risk.evaluate({**BASE, "sensitive": 3, "conflicted": 2})
    assert r["level"] == "HIGH" and r["drivers"][0].startswith("HIGH") and "2 conflicted" in r["drivers"][0]
    assert all(isinstance(d["count"], int) for d in r["driver_details"])
    assert not any(isinstance(v, float) for v in r.values())


def test_recommendations():
    f = {**BASE, "conflicted": 1, "mixed": True, "missing_tests": True, "behind": 2}
    rec = [x["id"] for x in risk.recommend(f, risk.evaluate(f))]
    assert rec[0] == "resolve-conflicts-first" and {"split-commits", "add-tests", "rebase-or-merge-upstream"} <= set(rec)
    assert [x["id"] for x in risk.recommend(BASE, risk.evaluate(BASE))] == ["no-action-needed"]
    assert all(x["inspect"].startswith("git ") for x in risk.recommend(f, risk.evaluate(f)))


def test_pr_risk():
    assert risk.evaluate_pr({})["level"] == "LOW"
    assert risk.evaluate_pr({"secret_findings": 1})["level"] == "HIGH"
    assert risk.evaluate_pr({"destructive_migration_stmts": 1})["level"] == "HIGH"
    r = risk.evaluate_pr({"migrations": 1, "behind_base": 2})
    assert r["level"] == "MEDIUM" and {"migration", "behind-base"} <= set(ids(r))


def test_secret_classifier():
    assert classify_line('API_KEY = "sk-live-abcdefghijklmnop1234567890"')
    assert classify_line("-----BEGIN RSA PRIVATE KEY-----")
    assert classify_line("db = postgres://user:hunter2xx@host/db")
    assert classify_line('password = "hunter2hunter2"') == "credential-assignment"
    for ok in ['password = os.environ["PW"]', "token_url = 'https://example.com/token'", 'password: str', 'api_key = "changeme"',
               "Basic configuration options", "secret = settings.SECRET", 'password = ""']:
        assert classify_line(ok) is None, ok
    out = scan_text("a.py", 'x = 1\nTOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123"\n')
    assert out == [{"path": "a.py", "line": 2, "kind": "provider-token-or-url-credentials", "value": "[REDACTED]"}]


def test_parse_diff_paths_lines_and_combined():
    diff = ("diff --git a/dir with space/a.py b/dir with space/a.py\nindex 1..2 100644\n--- a/dir with space/a.py\t\n+++ b/dir with space/a.py\t\n"
            "@@ -1,2 +1,3 @@\n-old\n+new\n+--- not a header\n"
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
            "diff --cc conflicted.txt\n@@@ -1 -1 +1 @@@\n++<<<<<<< x\n")
    recs, trunc = parse_diff(diff)
    assert ("dir with space/a.py", "+", 1, "new") in recs and ("dir with space/a.py", "-", 1, "old") in recs
    assert ("dir with space/a.py", "+", 2, "--- not a header") in recs
    assert ("gone.py", "-", 1, "bye") in recs
    assert not any(p == "conflicted.txt" for p, *_ in recs) and not trunc
    recs, trunc = parse_diff("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1,5 @@\n" + "+l\n" * 5, max_lines_per_file=3)
    assert len(recs) == 3 and trunc == {"x"}


def test_dependency_drift_cases():
    tracked = {"package.json", "package-lock.json", "sub/Cargo.toml", "sub/Cargo.lock", "requirements.txt", "go.mod", "go.sum"}
    d = dependency_drift({"package.json"}, tracked)
    assert d["manifest_without_lockfile"][0]["lockfiles"] == ["package-lock.json"]
    assert not dependency_drift({"package.json", "package-lock.json"}, tracked)["manifest_without_lockfile"]
    d = dependency_drift({"sub/Cargo.lock"}, tracked)
    assert d["lockfile_without_manifest"] == [{"lockfile": "sub/Cargo.lock", "manifest": "sub/Cargo.toml"}]
    assert not dependency_drift({"requirements.txt"}, tracked)["manifest_without_lockfile"]  # no lockfile exists for pip


def test_mixed_concerns_and_kind():
    cl = lambda kind, label="x": {"kind": kind, "label": label, "file_count": 1}  # noqa: E731
    assert not mixed_concerns([cl("source"), cl("test"), cl("docs")])["flag"]
    assert mixed_concerns([cl("source", "a"), cl("ui", "b")])["flag"]
    assert mixed_concerns([cl("source"), cl("infra")])["flag"]
    assert kind_of({"migration", "schema"}) == "migration" and kind_of({"test", "source"}) == "test"
