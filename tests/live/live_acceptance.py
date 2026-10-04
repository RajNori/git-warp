#!/usr/bin/env python3
"""Live Claude Code acceptance for Git Warp (release gate; opt-in, costs API money, NOT collected by pytest).

Runs REAL headless Claude Code sessions (`claude -p ... --output-format stream-json --include-hook-events`) against a
clean `git archive` export of the commit under test, inside disposable repositories under a temp dir.  It never touches
the Git Warp checkout, never installs into the user's Claude configuration (the plugin is loaded for the session only via
`--plugin-dir`; user/project settings are excluded with `--setting-sources project,local`).

    python3 tests/live/live_acceptance.py [--out planning/LIVE_ACCEPTANCE.md] [--only guard,skills] [--budget 1.0]

Scenarios: SessionStart, skill discovery, skill invocation, PreToolUse DENY / ASK / DEFER, ASK against a blanket CLI
pre-approval, ASK against a SKILL-level pre-approved Bash rule (with an unguarded control), dynamic-expression ASK,
PostToolUse capture (+ secret redaction), Stop, and all ten workflow skills end to end.

Exit code 0 only if every scenario that ran is PASS.  A scenario the model never attempted is INCONCLUSIVE (retried).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tui_driver   # noqa: E402  (real interactive TUI through a pty)
# Fake token, assembled at runtime so no literal credential ships in the plugin.
SYNTHETIC_SECRET = "".join(["gh", "p_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"])
SKILL_CMDS = {  # skill -> (prompt arguments exactly as the skill's argument-hint documents them, warp.py subcommand it must run)
    "git-xray": ("", "xray"), "git-pr": ("main", "pr"), "git-rescue": ("I deleted the branch gone/precious; find the lost commit", "rescue"),
    "git-archaeology": ("src/util.py", "archaeology"), "git-bisect-ai": ("--good HEAD~5 --bad HEAD", "bisect"),
    "git-commits": ("", "commits"), "git-blast-radius": ("src/util.py", "blast"), "git-conflict": ("", "conflict"),
    "git-temporal-review": ("", "temporal"), "git-memory": ("hotspots", "memory"),
}


def sh(cwd, *args, check=True):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.invalid",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode:
        raise RuntimeError(f"git {args}: {p.stderr}")
    return p.stdout.strip()


def build_repo(path: Path, conflict: bool = False) -> Path:
    path.mkdir(parents=True)
    sh(path, "init", "-q", "-b", "main")
    (path / "src").mkdir()
    (path / "src/util.py").write_text("def add(a, b):\n    return a + b\n")
    (path / "src/app.py").write_text("from src import util\n\ndef run():\n    return util.add(1, 2)\n")
    (path / "src/__init__.py").write_text("")
    (path / "README.md").write_text("# demo\n")
    sh(path, "add", "-A"); sh(path, "commit", "-qm", "feat: initial app")
    for i in range(6):
        (path / f"src/m{i}.py").write_text(f"from src import util\nV = {i}\n")
        (path / "src/util.py").write_text(f"def add(a, b):\n    return a + b + {i}\n")
        sh(path, "add", "-A"); sh(path, "commit", "-qm", f"feat: module {i}")
    sh(path, "branch", "scratch")
    sh(path, "checkout", "-q", "-b", "gone/precious")
    (path / "src/precious.py").write_text("PRECIOUS = 1\n")
    sh(path, "add", "-A"); sh(path, "commit", "-qm", "feat: precious work on a branch that will be deleted")
    sh(path, "checkout", "-q", "main"); sh(path, "branch", "-D", "gone/precious")
    sh(path, "checkout", "-q", "-b", "feature/x")
    (path / "src/feature.py").write_text("from src import util\nF = util.add(1, 1)\n")
    sh(path, "add", "-A"); sh(path, "commit", "-qm", "feat: feature x")
    sh(path, "checkout", "-q", "main")
    if conflict:
        sh(path, "checkout", "-q", "-b", "side")
        (path / "src/util.py").write_text("def add(a, b):\n    return 'side'\n")
        sh(path, "commit", "-qam", "side edit")
        sh(path, "checkout", "-q", "main")
        (path / "src/util.py").write_text("def add(a, b):\n    return 'main'\n")
        sh(path, "commit", "-qam", "main edit")
        sh(path, "merge", "side", check=False)
    else:
        (path / "src/app.py").write_text("from src import util\n\ndef run():\n    return util.add(2, 3)  # uncommitted edit\n")
        (path / "notes.txt").write_text("untracked\n")
    return path


@dataclass
class Run:
    events: list
    returncode: int
    seconds: float
    cost: float = 0.0

    def hook_responses(self, name=None):
        out = [e for e in self.events if e.get("type") == "system" and e.get("subtype") == "hook_response"]
        return [e for e in out if name is None or e.get("hook_event") == name or e.get("hook_name", "").startswith(name)]

    def tool_uses(self, name=None):
        res = []
        for e in self.events:
            if e.get("type") == "assistant":
                for c in e["message"].get("content", []):
                    if c.get("type") == "tool_use" and (name is None or c["name"] == name):
                        res.append(c)
        return res

    def tool_result(self, tool_use_id):
        for e in self.events:
            if e.get("type") == "user" and isinstance(e["message"].get("content"), list):
                for c in e["message"]["content"]:
                    if c.get("type") == "tool_result" and c.get("tool_use_id") == tool_use_id:
                        return c
        return None

    def init(self):
        return next((e for e in self.events if e.get("type") == "system" and e.get("subtype") == "init"), {})

    def result(self):
        return next((e for e in self.events if e.get("type") == "result"), {})

    def denials(self):
        return self.result().get("permission_denials") or []

    def bash_with(self, needle):
        return [t for t in self.tool_uses("Bash") if needle in json.dumps(t["input"])]


def claude(prompt, repo, plugin_dirs, *, allowed=None, budget=1.0, timeout=420) -> Run:
    cmd = ["claude", "-p", prompt, "--setting-sources", "project,local", "--output-format", "stream-json", "--verbose",
           "--include-hook-events", "--no-session-persistence", "--max-budget-usd", str(budget)]
    for d in plugin_dirs:
        cmd += ["--plugin-dir", str(d)]
    if allowed:
        cmd += ["--allowedTools", allowed]
    t = time.time()
    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout)
    events = []
    for line in p.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    run = Run(events, p.returncode, time.time() - t)
    run.cost = float(run.result().get("total_cost_usd") or 0)
    return run


@dataclass
class Scenario:
    id: str
    title: str
    status: str = "PENDING"        # PASS FAIL INCONCLUSIVE
    evidence: list = field(default_factory=list)
    cost: float = 0.0


class Acceptance:
    def __init__(self, budget):
        self.budget = budget
        self.tmp = Path(tempfile.mkdtemp(prefix="gw-live-"))
        self.plugin = self.tmp / "plugin"
        self.testplug = self.tmp / "testplug"
        self.results: list[Scenario] = []
        self.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        self._export()

    def _export(self):
        self.plugin.mkdir()
        arc = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(self.plugin)], input=arc, check=True)
        (self.testplug / ".claude-plugin").mkdir(parents=True)
        (self.testplug / "skills/preapprove-test").mkdir(parents=True)
        (self.testplug / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "gw-live-test", "version": "0.0.1", "description": "test-only"}))
        (self.testplug / "skills/preapprove-test/SKILL.md").write_text(
            "---\nname: preapprove-test\ndescription: Test-only skill. Use it when the user asks you to use the preapprove-test skill.\n"
            "allowed-tools: Bash(git branch:*)\n---\n\nTest skill. Once loaded you may run the `git branch` command the user asked for.\n")

    def repo(self, name, conflict=False) -> Path:
        self._n = getattr(self, "_n", 0) + 1          # unique per call: scenario retries must not collide
        return build_repo(self.tmp / f"{name}-{self._n}", conflict=conflict)

    def scenario(self, sid, title, fn, retries=2):
        s = Scenario(sid, title)
        for attempt in range(retries + 1):
            try:
                status, evidence, cost = fn()
            except Exception as exc:  # noqa: BLE001
                import traceback
                status, evidence, cost = "FAIL", [f"harness exception: {exc!r}", "traceback: " + traceback.format_exc()[-600:].replace("\n", " | ")], 0.0
            s.cost += cost
            s.status, s.evidence = status, evidence
            if status != "INCONCLUSIVE":
                break
            s.evidence.append(f"(attempt {attempt + 1}: model did not attempt the action; retrying)")
        self.results.append(s)
        print(f"[{s.status}] {sid} {title}")
        return s

    # ------------------------------------------------------------------ scenarios

    def sc_session_and_discovery(self):
        repo = self.repo("discovery")
        r = claude("Reply with exactly: OK", repo, [self.plugin], budget=self.budget)
        init, ev = r.init(), []
        skills = [s for s in init.get("skills", []) if s.startswith("git-warp:")]
        agents = [a for a in init.get("agents", []) if a.startswith("git-warp:")]
        ss = r.hook_responses("SessionStart")
        stop = r.hook_responses("Stop")
        ok = len(skills) == 10 and len(agents) == 3 and bool(ss) and ss[0].get("exit_code") == 0 and bool(stop)
        ev += [f"skills ({len(skills)}): {sorted(skills)}", f"agents ({len(agents)}): {sorted(agents)}",
               f"SessionStart hook responses: {len(ss)} exit={[h.get('exit_code') for h in ss]}",
               f"Stop hook responses: {len(stop)} exit={[h.get('exit_code') for h in stop]}",
               f"plugin loaded from {init.get('plugins', [{}])[0].get('path', '?')} (version {init.get('plugins', [{}])[0].get('version')})"]
        return ("PASS" if ok else "FAIL"), ev, r.cost

    def _bash_case(self, command, allowed, expect, branch_must_remain=None, file_check=None, extra_plugins=(), plugin=True):
        repo = self.repo("g-" + str(abs(hash((command, allowed, expect)))))
        dirs = ([self.plugin] if plugin else []) + list(extra_plugins)
        r = claude(f"Run exactly this bash command with the Bash tool and then report what happened, nothing else: {command}", repo, dirs,
                   allowed=allowed, budget=self.budget)
        uses = r.bash_with(command.split()[0] + " " + command.split()[1]) or r.tool_uses("Bash")
        if not uses:
            return "INCONCLUSIVE", ["model never called Bash"], r.cost
        use = uses[0]
        res = r.tool_result(use["id"]) or {}
        pre = [h for h in r.hook_responses("PreToolUse")]
        decisions = []
        for h in pre:
            try:
                out = json.loads(h.get("output") or h.get("stdout") or "{}")
                decisions.append(out.get("hookSpecificOutput", {}).get("permissionDecision", "defer"))
            except json.JSONDecodeError:
                decisions.append("unparsed")
        branches = sh(repo, "branch", "--list").split()
        ev = [f"command: {use['input'].get('command')!r}", f"PreToolUse decisions: {decisions}",
              f"tool_result is_error={res.get('is_error')}: {str(res.get('content'))[:160]!r}",
              f"permission_denials: {len(r.denials())}", f"branches after: {branches}"]
        executed = not res.get("is_error")
        ok = True
        if expect == "deny":
            ok = "deny" in decisions and not executed
        elif expect == "ask":
            ok = "ask" in decisions and not executed and len(r.denials()) >= 1
        elif expect == "defer":
            ok = executed and all(d in ("defer",) for d in decisions)
        if branch_must_remain:
            ok = ok and branch_must_remain in branches
        if file_check:
            fc = file_check(repo)
            ev.append(f"file check: {fc}")
            ok = ok and fc[0]
        return ("PASS" if ok else "FAIL"), ev, r.cost

    def sc_deny(self):
        return self._bash_case("git reset --hard", "Bash", "deny",
                               file_check=lambda repo: ((repo / "src/app.py").read_text().count("uncommitted edit") == 1, "uncommitted edit preserved"))

    def sc_ask_cli_preapproved(self):
        return self._bash_case("git branch -D scratch", "Bash", "ask", branch_must_remain="scratch")

    def sc_defer(self):
        return self._bash_case("git status --short", "Bash(git status:*)", "defer")

    def sc_dynamic(self):
        return self._bash_case("git reset $(echo --hard)", "Bash", "ask",
                               file_check=lambda repo: ((repo / "src/app.py").read_text().count("uncommitted edit") == 1, "uncommitted edit preserved"))

    @staticmethod
    def _tui_policy(window):
        """Classify a permission dialog by ITS OWN title (the squashed window can contain the echoed prompt)."""
        tail = window[-900:]
        if 'Useskill"' in tail:
            return "skill-approval", "approve"          # a user approves the skill once, as in a normal session
        if "HookPreToolUse" in tail or "GitWarp:" in tail:
            return "guardian-ask", "decline"
        if "Bashcommand" in tail:
            return "claude-own-bash-permission", "decline"
        return "other", "decline"

    def sc_ask_skill_preapproved(self):
        """THE open question, answered in the REAL interactive TUI (headless cannot: there a skill's allowed-tools does not
        pre-approve).  Guarded run: Git Warp + a skill pre-approving Bash(git branch:*).  Control: the same skill alone."""
        prompt = ("First invoke the skill named preapprove-test using the Skill tool. After it is loaded, run exactly this bash command "
                  "with the Bash tool and report what happened, nothing else: git branch -D scratch")
        g = self.repo("skillask-guarded")
        rg = tui_driver.drive(g, [self.plugin, self.testplug], prompt, self._tui_policy)
        c = self.repo("skillask-control")
        rc = tui_driver.drive(c, [self.testplug], prompt, self._tui_policy)
        kinds_g = [d.kind for d in rg.dialogs]
        kinds_c = [d.kind for d in rc.dialogs]
        g_remains = "scratch" in sh(g, "branch", "--list")
        c_deleted = "scratch" not in sh(c, "branch", "--list")
        ev = [f"CONTROL (skill pre-approves Bash(git branch:*), no Git Warp): dialogs={kinds_c}; branch deleted without a Bash prompt={c_deleted}",
              f"GUARDED (same skill + Git Warp): dialogs={kinds_g}; branch survives={g_remains}",
              "interpretation: the control proves the skill-level pre-approval is effective in an interactive session; the guarded run shows "
              "whether Guardian's ASK still surfaces as a permission dialog despite it"]
        if "skill-approval" not in kinds_g or "skill-approval" not in kinds_c:
            return "INCONCLUSIVE", ev + ["the skill was not loaded in one of the runs"], 0.0
        ok = c_deleted and "claude-own-bash-permission" not in kinds_c and "guardian-ask" in kinds_g and g_remains
        return ("PASS" if ok else "FAIL"), ev, 0.2

    def sc_ask_dialog_tui(self):
        """The Guardian ASK is an actual permission dialog (reason text visible) in the interactive TUI."""
        repo = self.repo("askdialog")
        r = tui_driver.drive(repo, [self.plugin], "Run exactly this bash command with the Bash tool: git branch -D scratch", self._tui_policy)
        gd = [d for d in r.dialogs if d.kind == "guardian-ask"]
        remains = "scratch" in sh(repo, "branch", "--list")
        ev = [f"dialogs={[d.kind for d in r.dialogs]}", f"branch survives={remains}"]
        if gd:
            ev.append("dialog text: " + gd[0].text[-420:])
        return ("PASS" if gd and remains and "Force-deleting" in gd[0].text and "requiresconfirmation" in gd[0].text else "FAIL"), ev, 0.1

    def sc_workflow_tui(self, skill):
        """Interactive: after the user approves the skill, its scoped allowed-tools must cover the warp.py call: NO Bash dialog."""
        args, cmdname = SKILL_CMDS[skill]
        conflict = skill == "git-conflict"
        repo = self.repo("tui-" + skill, conflict=conflict)
        if skill == "git-pr":
            sh(repo, "checkout", "-q", "feature/x")
        before = (sh(repo, "rev-parse", "HEAD"), sh(repo, "status", "--porcelain"), sh(repo, "for-each-ref"))
        prompt = (f"Invoke the skill git-warp:{skill} with the Skill tool{(' using arguments: ' + args) if args else ''}, follow it, "
                  "and finish with a 3-line summary. Do not modify the repository.")
        r = tui_driver.drive(repo, [self.plugin], prompt, self._tui_policy, settle=110)
        kinds = [d.kind for d in r.dialogs]
        flat = tui_driver.squash(r.transcript)
        ran = "warp.py" in flat and cmdname in flat
        after = (sh(repo, "rev-parse", "HEAD"), sh(repo, "status", "--porcelain"), sh(repo, "for-each-ref"))
        ev = [f"dialogs={kinds}", f"warp.py {cmdname} appears in the session: {ran}", f"repo unchanged: {before == after}"]
        if "skill-approval" not in kinds or not ran:
            return "INCONCLUSIVE", ev, 0.0
        bash_prompts = [d for d in r.dialogs if d.kind == "claude-own-bash-permission"]
        warp_prompts = [d for d in bash_prompts if "warp.py" in d.text]
        other_prompts = [d for d in bash_prompts if "warp.py" not in d.text]
        ev.append(f"prompts for the warp.py call (allowed-tools pattern failed to match): {len(warp_prompts)}")
        for d in other_prompts:
            ev.append("note: a prompt for a different command (not covered by this skill's allowed-tools): " + d.text[-260:])
        for d in warp_prompts:
            ev.append("FAIL detail: " + d.text[-260:])
        return ("PASS" if not warp_prompts and before == after else "FAIL"), ev, 0.2

    def sc_skill_and_recorder(self):
        repo = self.repo("recorder")
        cmd = f"echo token={SYNTHETIC_SECRET}"
        r = claude(f"Run exactly this bash command with the Bash tool, then stop: {cmd}", repo, [self.plugin], allowed="Bash(echo:*)", budget=self.budget)
        uses = r.bash_with("echo token=")
        if not uses:
            return "INCONCLUSIVE", ["model never ran the command"], r.cost
        post = r.hook_responses("PostToolUse")
        rec = repo / ".git" / "git-warp" / "flight-recorder.jsonl"
        text = rec.read_text() if rec.exists() else ""
        mode = oct(rec.parent.stat().st_mode & 0o777) if rec.exists() else "-"
        ev = [f"PostToolUse hook responses: {len(post)} exit={[h.get('exit_code') for h in post]}",
              f"flight recorder exists: {rec.exists()} lines={len(text.splitlines())} state dir mode={mode}",
              f"synthetic secret persisted: {SYNTHETIC_SECRET in text}"]
        ok = bool(post) and rec.exists() and SYNTHETIC_SECRET not in text and mode == "0o700"
        return ("PASS" if ok else "FAIL"), ev, r.cost

    def sc_workflow(self, skill):
        args, cmdname = SKILL_CMDS[skill]
        conflict = skill == "git-conflict"
        repo = self.repo("wf-" + skill, conflict=conflict)
        if skill == "git-pr":
            sh(repo, "checkout", "-q", "feature/x")
        before = (sh(repo, "rev-parse", "HEAD"), sh(repo, "status", "--porcelain"), sh(repo, "for-each-ref"))
        prompt = (f"Invoke the skill git-warp:{skill} with the Skill tool{(' using arguments: ' + args) if args else ''}, follow it, "
                  "and finish with a 3-line summary of the evidence you found. Do not modify the repository.")
        r = claude(prompt, repo, [self.plugin], allowed="Skill,Bash(python3:*)", budget=self.budget)   # headless: skills' allowed-tools do not pre-approve here, so the warp.py call is allowed explicitly; the TUI scenarios check the skill-level permission
        skill_used = [t for t in r.tool_uses("Skill") if skill in json.dumps(t["input"])]
        runs = [t for t in r.tool_uses("Bash") if "warp.py" in json.dumps(t["input"])]
        if not skill_used or not runs:
            return "INCONCLUSIVE", [f"skill invoked={bool(skill_used)} warp.py run={bool(runs)}"], r.cost
        res = [r.tool_result(t["id"]) or {} for t in runs]
        after = (sh(repo, "rev-parse", "HEAD"), sh(repo, "status", "--porcelain"), sh(repo, "for-each-ref"))
        errs = [x for x in res if x.get("is_error")]
        final = (r.result().get("result") or "")[:240].replace("\n", " ")
        ev = [f"skill invoked: {len(skill_used)}x", f"warp.py invocations: {len(runs)} (errors: {len(errs)}); first: {runs[0]['input'].get('command', '')[:120]!r}",
              f"permission_denials: {len(r.denials())}", f"repo unchanged: {before == after}", f"final answer: {final!r}"]
        ok = not errs and not r.denials() and before == after and cmdname in json.dumps(runs[0]["input"]) and len(final) > 20
        return ("PASS" if ok else "FAIL"), ev, r.cost

    # ------------------------------------------------------------------ driver

    def run(self, only):
        groups = {
            "discovery": [("LIVE-001", "SessionStart + Stop delivered; 10 skills and 3 agents discovered", self.sc_session_and_discovery)],
            "guard": [
                ("LIVE-002", "PreToolUse DENY: git reset --hard is blocked even with Bash pre-approved (work preserved)", self.sc_deny),
                ("LIVE-003", "PreToolUse ASK: git branch -D is not auto-run despite blanket Bash pre-approval", self.sc_ask_cli_preapproved),
                ("LIVE-004", "PreToolUse DEFER: git status runs under ordinary permissions", self.sc_defer),
                ("LIVE-005", "Dynamic expression: git reset $(echo --hard) is not run (ASK)", self.sc_dynamic),
                ("LIVE-006", "INTERACTIVE: ASK surfaces as a dialog when a SKILL pre-approves Bash(git branch:*) (with unguarded control)", self.sc_ask_skill_preapproved),
            ],
            "tui": [("LIVE-008", "TUI: Guardian ASK is a real permission dialog (reason shown); command not run", self.sc_ask_dialog_tui)],
            "recorder": [("LIVE-007", "PostToolUse capture: redacted flight recorder, private state dir", self.sc_skill_and_recorder)],
            "skills": [(f"LIVE-1{i:02d}", f"workflow skill {sk} (headless, content): invoked, warp.py ran, repo unchanged", (lambda sk=sk: self.sc_workflow(sk)))
                       for i, sk in enumerate(SKILL_CMDS, start=1)],
            "skills-tui": [(f"LIVE-2{i:02d}", f"workflow skill {sk} (interactive): scoped allowed-tools cover the warp.py call (no Bash dialog)", (lambda sk=sk: self.sc_workflow_tui(sk)))
                           for i, sk in enumerate(SKILL_CMDS, start=1)],
        }
        for name, items in groups.items():
            for sid, title, fn in items:
                if only and name not in only and sid not in only:
                    continue
                self.scenario(sid, title, fn)

    def report(self, out: Path):
        total = sum(s.cost for s in self.results)
        counts = {k: sum(1 for s in self.results if s.status == k) for k in ("PASS", "FAIL", "INCONCLUSIVE")}
        v = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
        lines = ["# Live Claude Code acceptance", "", f"- Commit under test: `{self.sha}` (clean `git archive` export loaded with `--plugin-dir`; user/project settings excluded)",
                 f"- Claude Code: {v}", f"- Date (UTC): {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
                 f"- Result: PASS {counts['PASS']}, FAIL {counts['FAIL']}, INCONCLUSIVE {counts['INCONCLUSIVE']}; API cost ≈ ${total:.2f}",
                 "- Method: real headless sessions (`claude -p --output-format stream-json --verbose --include-hook-events`) in disposable repositories; "
                 "hook events, tool calls, tool results and permission denials are read from the event stream; repository state is inspected afterwards.",
                 "- Limits: headless mode cannot show an interactive prompt, so ASK is verified as \"the tool did not auto-run and Claude Code recorded a permission denial\"; "
                 "the plugin is loaded per session (`--plugin-dir`), not installed (installation is verified separately in an isolated config dir).", ""]
        for s in self.results:
            lines += [f"## {s.id} — {s.status} — {s.title}", ""] + [f"- {e}" for e in s.evidence] + [""]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines))
        return counts

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "planning" / "LIVE_ACCEPTANCE.md"))
    ap.add_argument("--only", default="", help="comma list of groups (discovery,guard,tui,recorder,skills,skills-tui) and/or scenario ids")
    ap.add_argument("--budget", type=float, default=1.0)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    acc = Acceptance(a.budget)
    try:
        acc.run({x for x in a.only.split(",") if x})
        counts = acc.report(Path(a.out))
    finally:
        if not a.keep:
            acc.cleanup()
    print(json.dumps(counts))
    return 0 if counts["FAIL"] == 0 and counts["INCONCLUSIVE"] == 0 and counts["PASS"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
