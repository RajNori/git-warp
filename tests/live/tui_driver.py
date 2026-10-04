"""Drive the REAL interactive Claude Code TUI through a pseudo-terminal and record what a user would see.

Headless (`claude -p`) cannot answer one release question: does a Guardian ASK still surface as a permission dialog when a
SKILL pre-approves the Bash command?  (In headless mode a skill's `allowed-tools` does not pre-approve at all, so the
comparison needs a real session.)  This module spawns `claude` in a pty, pastes a prompt, watches the rendered screen for
permission dialogs, answers each through a caller-supplied policy, and returns the transcript and the dialogs it saw.

Safety: use only in a disposable repository.  The caller decides which dialogs to approve; the default policy declines.
The child environment is scrubbed of CLAUDE*/VSCODE* markers inherited from a parent Claude Code session.
"""
from __future__ import annotations

import os
import pty
import re
import select
import signal
import struct
import fcntl
import termios
import time
from dataclasses import dataclass, field

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]|\r")
DIALOG = re.compile(r"Doyouwantto|Esctocancel|Yes,and|No,and|1\.Yes|Allowthis")


def strip(data: bytes) -> str:
    return ANSI.sub("", data.decode("utf-8", "replace"))


def squash(text: str) -> str:
    """The TUI repaints with cursor moves, so spaces vanish after ANSI stripping: compare without whitespace."""
    return re.sub(r"\s+", "", text)


@dataclass
class Dialog:
    kind: str                 # what the policy called it
    text: str                 # squashed dialog text
    answer: str               # approve | decline | ignore


@dataclass
class Result:
    transcript: str
    dialogs: list = field(default_factory=list)
    finished: bool = False


def drive(cwd, plugin_dirs, prompt, policy, *, allowed=None, total=170, settle=70, extra_args=()) -> Result:
    """policy(squashed_dialog_text) -> (kind, "approve"|"decline"|"ignore").  Ends `settle` seconds after the prompt, or 15 s
    after the last dialog was answered, whichever is first."""
    signal.alarm(total + 60)
    args = ["claude", "--setting-sources", "project,local", "--permission-mode", "default", *extra_args]
    for d in plugin_dirs:
        args += ["--plugin-dir", str(d)]
    if allowed:
        args += ["--allowedTools", allowed]
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        for k in list(os.environ):
            if k.startswith(("CLAUDE", "VSCODE", "TERM_PROGRAM")) and k != "CLAUDE_CONFIG_DIR":
                os.environ.pop(k, None)
        os.environ.update(TERM="xterm-256color", COLUMNS="170", LINES="50", NO_COLOR="1")
        os.execvp("claude", args)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 170, 0, 0))
    buf = b""
    t0 = time.time()
    last_new = t0
    sent_at = None
    trusted = False
    handled = 0                   # buffer offset already examined for dialogs
    result = Result("")
    answered_at = None

    def read(timeout=0.4) -> bool:
        nonlocal buf, last_new
        r, _, _ = select.select([fd], [], [], timeout)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                return False
            if not d:
                return False
            buf += d
            last_new = time.time()
        return True

    alive = True
    while alive and time.time() - t0 < total:
        alive = read()
        window = squash(strip(buf[handled:]))
        if not trusted and re.search(r"Yes,Itrustthisfolder|trustthisfolder", window) and time.time() - last_new > 1.5:
            os.write(fd, b"\x1b[B")
            time.sleep(0.6)
            os.write(fd, b"\r")
            trusted = True
            handled = len(buf)
            time.sleep(2.5)
            continue
        if sent_at is None and time.time() - t0 > 6 and time.time() - last_new > 2.5:
            os.write(fd, b"\x1b[200~" + prompt.encode() + b"\x1b[201~")   # bracketed paste: no dropped keystrokes
            time.sleep(1.0)
            os.write(fd, b"\r")
            sent_at = time.time()
            handled = len(buf)
            continue
        if sent_at is not None and DIALOG.search(window) and time.time() - last_new > 1.2:
            kind, answer = policy(window)
            result.dialogs.append(Dialog(kind, window[-700:], answer))
            handled = len(buf)
            answered_at = time.time()
            if answer == "approve":
                os.write(fd, b"\r")                  # option 1 ("Yes") is preselected
            elif answer == "decline":
                os.write(fd, b"\x1b")                # Escape cancels the dialog
            time.sleep(1.5)
            continue
        if sent_at is not None and (time.time() - sent_at > settle or (answered_at and time.time() - answered_at > 15 and time.time() - last_new > 8)):
            result.finished = True
            break
    try:
        os.write(fd, b"\x03")
        time.sleep(0.4)
        os.write(fd, b"\x03")
    except OSError:
        pass
    time.sleep(0.8)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except Exception:
            try:
                os.kill(pid, sig)
            except Exception:
                pass
        time.sleep(0.3)
    for _ in range(20):
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                break
        except Exception:
            break
        time.sleep(0.2)
    signal.alarm(0)
    result.transcript = strip(buf)
    return result


signal.signal(signal.SIGALRM, lambda *a: os._exit(3))
