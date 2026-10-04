import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "plugin" / "scripts"


def run_hook(name, event, cwd, raw=None):
    """Run scripts/hook_<name>.py like Claude does. Returns (returncode, stdout, stderr, elapsed)."""
    payload = raw if raw is not None else json.dumps(event)
    t = time.monotonic()
    p = subprocess.run([sys.executable, str(SCRIPTS / f"hook_{name}.py")], input=payload, capture_output=True, text=True, cwd=str(cwd), timeout=60, env=dict(os.environ))
    return p.returncode, p.stdout, p.stderr, time.monotonic() - t
