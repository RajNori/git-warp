"""Helpers shared by the history tests (in-process CLI runner + repo snapshot)."""
import io
import json
from contextlib import redirect_stdout

from gitwarp.history import cli


def run_cli(*argv):
    """Run ``history.cli.main`` in-process; returns ``(exit_code, parsed_json)``."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main([str(a) for a in argv])
    return code, json.loads(buf.getvalue())


def snapshot(r):
    """Everything a read-only command must not change."""
    return {
        "refs": r.git("for-each-ref", "--format=%(refname) %(objectname)"),
        "status": r.git("status", "--porcelain=v1", "--untracked-files=all", check=False),
        "head": r.git("rev-parse", "HEAD", check=False),
        "head_ref": r.git("symbolic-ref", "-q", "HEAD", check=False),
    }
