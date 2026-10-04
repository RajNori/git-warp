"""Redaction coverage for the synthetic secret types, central output redaction, and non-corruption of ordinary text."""
from __future__ import annotations

import io
import json
import time

import pytest

from gitwarp.core import output
from gitwarp.core.redact import REDACTED, redact, redact_long, scrub
from tests.security.test_convergence_contracts import SECRETS, SECRET_TEXT


@pytest.mark.parametrize("text", SECRET_TEXT)
def test_each_secret_text_is_redacted(text):
    out = redact(text)
    assert not [k for k, v in SECRETS.items() if v in out], out
    assert REDACTED in out


@pytest.mark.parametrize("text", SECRET_TEXT)
@pytest.mark.parametrize("wrap", ["chore: rotate {}", "fix: {} (see ticket)", "{}\n\nbody", "a; b && {} | tee x"])
def test_secrets_are_redacted_in_context(text, wrap):
    out = redact(wrap.format(text))
    assert not [k for k, v in SECRETS.items() if v in out], out


def test_ssh_identity_variants():
    for t in ("ssh -i /keys/prod_k deploy@h", "ssh -o StrictHostKeyChecking=no -i '/k/a b' h", "scp -i=/k/x f h:", "IdentityFile /k/prod_x",
              "ssh --identity-file /k/z h"):
        assert "/k/" not in redact(t) and "/keys/" not in redact(t), t


@pytest.mark.parametrize("text", [
    "0123456789abcdef0123456789abcdef01234567", "feat(core): add storage layer", "fix: handle src/gitwarp/core/storage.py",
    "Merge branch 'feature/x' into main", "docs: update ssh setup notes", "chore: bump version to 1.2.3",
    "perf: ls -i is faster", "see https://example.com/path/to/page?x=1",
])
def test_ordinary_text_is_not_corrupted(text):
    assert redact(text) == text


def test_redaction_is_bounded_in_time():
    nasty = ("ssh " + "-i " * 4000, "mysql " + "-p " * 4000, "a=" * 8000, "Authorization: " + "Bearer " * 2000, "-----BEGIN PRIVATE KEY-----" * 400)
    t0 = time.perf_counter()
    for s in nasty:
        redact(s)
        redact_long(s * 3)
    assert time.perf_counter() - t0 < 5


def test_scrub_redacts_every_string_but_keeps_structure():
    obj = {"a": [SECRETS["github"], 3, None, True], "b": {"c": f"x {SECRETS['openai']}"}, "p": __import__("pathlib").Path("/tmp/x")}
    out = scrub(obj)
    assert SECRETS["github"] not in json.dumps(out) and SECRETS["openai"] not in json.dumps(out)
    assert out["a"][1:] == [3, None, True] and out["p"] == "/tmp/x"


def test_redact_long_does_not_truncate_or_leak():
    big = ("line without secrets\n" * 2000) + SECRETS["github"] + "\n" + ("tail\n" * 2000)
    out = redact_long(big)
    assert SECRETS["github"] not in out and out.endswith("tail\n") and len(out) > 30_000


def test_emit_redacts_centrally():
    buf = io.StringIO()
    output.emit({"commits": [{"subject": f"rotate {SECRETS['aws-key']}"}], "x": {"y": SECRET_TEXT}}, stream=buf)
    assert not [k for k, v in SECRETS.items() if v in buf.getvalue()]
    assert json.loads(buf.getvalue())["commits"][0]["subject"].startswith("rotate ")


def test_hook_output_helpers_redact():
    d = output.pretool_decision("ask", f"command mentions {SECRETS['github']}", context=f"ctx {SECRETS['npm']}")
    d2 = output.additional_context("SessionStart", f"log: {SECRETS['hf']}")
    d3 = {"systemMessage": f"report {SECRETS['anthropic']}"}
    buf = io.StringIO()
    import sys
    old, sys.stdout = sys.stdout, buf
    try:
        output.write_hook(d3)
    finally:
        sys.stdout = old
    blob = json.dumps(d) + json.dumps(d2) + buf.getvalue()
    assert not [k for k, v in SECRETS.items() if v in blob]
    assert d["hookSpecificOutput"]["permissionDecision"] == "ask"
