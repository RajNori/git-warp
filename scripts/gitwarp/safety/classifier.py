"""Deterministic Git command classifier: ``classify_command(command, cfg, branch) -> Verdict``.

Pure function: it tokenizes the command string (never runs it), finds every Git
invocation (through wrappers, subshells, ``bash -c``, ``eval "literal"``,
``xargs``, ``find -exec``, here-docs piped to a shell) and returns the MOST
severe verdict.  See ``docs/guard-limitations.md`` for what it cannot see.

Decisions: ``deny`` (never allowed), ``ask`` (user must confirm) and ``allow``
(silent).  In ``safety_mode: strict`` history-rewriting ``ask`` rules become
``deny``.
"""
from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field
from typing import List, Optional

from gitwarp.core.config import Config
from gitwarp.core.redact import redact

from .tokenizer import Budget, Cmd, ShellParseError, Word, parse_script

MAX_COMMAND_CHARS = 20_000
MAX_SCRIPT_DEPTH = 12
_RANK = {"allow": 0, "ask": 1, "deny": 2}


@dataclass
class Verdict:
    decision: str                      # allow | ask | deny
    rule: str = "allow"
    operation: str = ""
    reason: str = ""
    safer: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "rule": self.rule, "operation": self.operation,
                "reason": self.reason, "safer": list(self.safer), "commands": list(self.commands)}


# --------------------------------------------------------------------------- rules
# rule -> (decision, operation, what was detected, why it matters, safer paths)
_BR = "git branch rescue/pre-{op}  # keep a pointer to the current commit"
_STASH = "git stash push -u  # park uncommitted work first"
RULES = {
    "reset-hard": ("deny", "git reset --hard",
                   "`git reset --hard` throws away all uncommitted changes in tracked files and moves the branch pointer.",
                   "Uncommitted work is not in the reflog, so it usually cannot be recovered.",
                   ["git branch rescue/pre-reset  # keep a pointer to the current commit", _STASH,
                    "git reset --soft <rev> or git reset --mixed <rev>  # move HEAD but keep your changes",
                    "git restore <specific-path>  # discard one file only"]),
    "clean-force": ("deny", "git clean -f",
                    "`git clean` with a force flag deletes untracked files (and with -x ignored files) permanently.",
                    "Untracked files are not in Git history and cannot be restored.",
                    ["git clean -n  # dry run: list what would be removed", "git clean -i  # choose interactively",
                     "git stash push -u  # keep untracked files recoverable"]),
    "checkout-discard-all": ("deny", "git checkout/restore of the whole tree",
                             "This overwrites every modified file in the work tree with the committed version.",
                             "All uncommitted edits to the targeted files are lost permanently.",
                             [_STASH, "git restore <specific-path>  # discard one file only",
                              "git diff  # review what would be lost first"]),
    "checkout-force": ("deny", "git checkout --force",
                       "`git checkout -f` / `--force` discards local modifications while switching.",
                       "Uncommitted changes are overwritten without a way back.",
                       [_STASH + ", then git checkout <branch>", "git checkout <branch>  # refuses if it would lose changes"]),
    "switch-discard": ("deny", "git switch --discard-changes",
                       "`git switch -f` / `--discard-changes` throws away local modifications while switching.",
                       "Uncommitted changes are overwritten without a way back.",
                       [_STASH + ", then git switch <branch>", "git switch <branch>  # refuses if it would lose changes"]),
    "reflog-destroy": ("deny", "git reflog expire/delete",
                       "This removes reflog entries, which are the safety net for recovering lost commits.",
                       "Without the reflog, commits after a bad reset/rebase become hard or impossible to find.",
                       ["git reflog  # inspect it instead", "git branch rescue/<name> <sha>  # preserve a commit"]),
    "gc-prune-now": ("deny", "git gc --prune=now",
                     "Pruning with `now`/`all` permanently deletes unreachable objects immediately.",
                     "Dangling commits (rescue candidates) are removed with no grace period.",
                     ["git gc  # default prune keeps 2 weeks of unreachable objects", "git fsck --lost-found  # inspect first"]),
    "prune": ("deny", "git prune",
              "`git prune` permanently deletes unreachable objects.",
              "Dangling commits cannot be recovered afterwards.",
              ["git prune -n  # dry run", "git gc  # safe default grace period"]),
    "stash-clear": ("deny", "git stash clear",
                    "`git stash clear` deletes every stash entry.",
                    "Stashed work becomes unreachable and is only recoverable via fsck.",
                    ["git stash list  # review first", "git stash drop stash@{N}  # remove a single entry (asks)"]),
    "stash-drop": ("ask", "git stash drop",
                   "`git stash drop` deletes a stash entry.", "The stashed changes become unreachable.",
                   ["git stash show -p stash@{N}  # review it first", "git stash pop  # apply then drop only if clean"]),
    "update-ref-delete": ("deny", "git update-ref -d",
                          "`git update-ref -d` deletes a branch or HEAD ref directly, bypassing Git's safety checks.",
                          "Commits only reachable from that ref become dangling.",
                          ["git branch -d <branch>  # refuses unmerged branches", "git branch rescue/<name> <sha>"]),
    "update-ref-delete-other": ("ask", "git update-ref -d",
                                "`git update-ref -d` deletes a ref directly.", "It bypasses Git's usual checks.",
                                ["git tag -d <tag> or git branch -d <branch>"]),
    "update-ref-protected": ("ask", "git update-ref",
                             "`git update-ref` moves a protected branch pointer directly.",
                             "It rewrites history on '{branch}' without safety checks.",
                             ["git branch rescue/pre-update-ref  # keep the old tip", "git merge/git revert on a feature branch"]),
    "push-force-protected": ("deny", "git push --force (protected branch)",
                             "Force-pushing '{branch}' (a protected branch) can overwrite shared history on the remote.",
                             "Collaborators lose commits and the remote history cannot be restored from this clone.",
                             ["git push  # a normal push", "git revert <sha> and push normally",
                              "git push --force-with-lease  # only on your own feature branch"]),
    "push-force": ("ask", "git push --force",
                   "Force push to '{branch}' rewrites the remote branch.",
                   "Anyone who based work on the old commits will be affected.",
                   ["git push --force-with-lease  # fails if the remote changed", "git push  # without force if possible"]),
    "push-mirror": ("deny", "git push --mirror",
                    "`git push --mirror` makes the remote an exact copy of this repo, deleting remote-only refs.",
                    "It can delete branches and tags on the remote and overwrite all of its history.",
                    ["git push <remote> <branch>  # push one branch", "git push --tags  # push tags only"]),
    "push-delete-protected": ("deny", "git push --delete (protected branch)",
                              "Deleting the remote branch '{branch}' (a protected branch).",
                              "Removing a protected branch can break the team's workflow and CI.",
                              ["Delete branches through the hosting UI after review"]),
    "push-delete": ("ask", "git push --delete",
                    "Deleting remote branch '{branch}'.", "Remote branch deletion affects everyone using it.",
                    ["git branch -d <branch>  # delete the local one only", "git push --dry-run"]),
    "history-tool": ("deny", "git filter-branch / filter-repo",
                     "`{tool}` rewrites the entire repository history.",
                     "Every commit hash changes and the old history is hard to recover.",
                     ["git clone --mirror <repo> backup.git  # take a full backup, then run it yourself outside Claude",
                      "git revert <sha>  # undo a change without rewriting history"]),
    "rm-git": ("deny", "rm -rf .git",
               "Removing '{path}' deletes the repository's entire history and configuration.",
               "All commits, branches and stashes in it are lost unless a remote has them.",
               ["git clone --mirror <repo> backup.git  # backup first", "rm -f .git/index.lock  # for a stale lock only"]),
    "rebase": ("ask", "git rebase",
               "`git rebase` rewrites commit history of the current branch.",
               "Commit hashes change; conflicts can leave the repo mid-rebase.",
               ["git branch rescue/pre-rebase  # keep a pointer to the old tip", "git merge <base>  # integrate without rewriting",
                "git rebase --abort  # leave a rebase in progress"]),
    "commit-amend": ("ask", "git commit --amend",
                     "`git commit --amend` replaces the last commit.",
                     "If it was already pushed, the amended commit diverges from the remote.",
                     ["git commit  # make a new commit", "git commit --fixup=<sha>  # fix up later"]),
    "branch-force-delete": ("ask", "git branch -D",
                            "Force-deleting branch '{branch}' (even if unmerged).",
                            "Unmerged commits become reachable only via the reflog.",
                            ["git branch -d <branch>  # refuses if unmerged", "git branch rescue/<name> <branch>  # keep a pointer"]),
    "branch-force-move": ("ask", "git branch -f / -M",
                          "This force-moves, renames over or copies over an existing branch.",
                          "The previous branch tip is overwritten.",
                          ["git branch -m <old> <new>  # rename without overwrite", "git branch rescue/<name>  # keep the old tip"]),
    "reset-protected": ("ask", "git reset (protected branch)",
                        "Moving HEAD back with `git reset` on protected branch '{branch}'.",
                        "Commits after the target disappear from the branch.",
                        ["git revert <sha>  # undo with a new commit", "git reset --soft <rev>  # keep changes staged",
                         "git switch -c feature/<name>  # work on a feature branch"]),
    "reset-unresolved": ("ask", "git reset $VAR",
                         "`git reset` is given an unresolved variable that could be `--hard`.",
                         "Git Warp cannot see what it expands to.",
                         ["Spell out the flag: git reset --soft|--mixed <rev>"]),
    "clean-unresolved": ("ask", "git clean $VAR",
                         "`git clean` is given an unresolved variable that could be a force flag.",
                         "Git Warp cannot see what it expands to.", ["git clean -n  # dry run with literal flags"]),
    "tag-rewrite": ("ask", "git tag -d / -f",
                    "This deletes or moves an existing tag.", "Tags are often referenced by releases and other clones.",
                    ["git tag <new-name>  # create a new tag", "git show <tag>  # note the sha before changing"]),
    "worktree-force-remove": ("ask", "git worktree remove --force",
                              "Force-removing a worktree discards its uncommitted changes.",
                              "Work in that directory is lost.",
                              ["git -C <worktree> status  # check it first", "git worktree remove <path>  # refuses if dirty"]),
    "submodule-deinit-force": ("ask", "git submodule deinit --force",
                               "Force-deinit of a submodule discards its local modifications.", "Uncommitted submodule work is lost.",
                               ["git -C <submodule> status  # check it first"]),
    "alias-config": ("ask", "git config alias.*",
                     "Defining a Git alias can hide a destructive command behind a harmless name.",
                     "The guard cannot see through aliases when they are later invoked.",
                     ["Run the real command spelled out instead of defining an alias"]),
    "alias-inline": ("ask", "git -c alias.*",
                     "An alias is defined inline via `git -c alias.<name>=...`.",
                     "Aliases can run arbitrary commands the guard cannot classify.",
                     ["Run the real command spelled out"]),
    "remote-modify": ("ask", "git remote set-url/remove",
                      "This changes or removes a remote.", "Pushes/fetches may go somewhere else, or tracking info is lost.",
                      ["git remote -v  # inspect first", "git remote add <new-name> <url>"]),
    "unresolved-subcommand": ("ask", "git $VAR",
                              "The Git subcommand is built from a variable, substitution or glob.",
                              "Git Warp cannot tell whether it is destructive.",
                              ["Spell out the Git subcommand literally"]),
    "unresolved-command": ("ask", "unresolved command running git",
                           "A command word is built from a variable or substitution and the line mentions git.",
                           "Git Warp cannot tell what will run.", ["Spell out the command literally"]),
    "eval-git": ("ask", "eval mentioning git",
                 "`eval` receives a computed string that mentions git.",
                 "Git Warp cannot see the command that will actually run.", ["Run the Git command directly without eval"]),
    "shell-git": ("ask", "shell -c with computed script mentioning git",
                  "A shell is given a computed script that mentions git.",
                  "Git Warp cannot see the command that will actually run.", ["Pass the Git command literally"]),
    "too-complex": ("ask", "command too complex to analyse",
                    "The command is too long or deeply nested to analyse and mentions git.",
                    "Git Warp cannot verify it is safe.", ["Split it into smaller, simpler commands"]),
    "option-unresolved": ("ask", "git <option>$VAR",
                          "A Git option word carries an unresolved variable or substitution (for example `--hard$IFS`).",
                          "Git Warp cannot see which flag it expands to, and the subcommand can destroy work.",
                          ["Spell out the flags literally"]),
    "interpreter-git": ("ask", "interpreter running a git command string",
                        "An interpreter (python/node/ruby/perl...) is given a string containing a destructive Git command.",
                        "Git Warp cannot see what the program will actually run.", ["Run the Git command directly so it can be reviewed"]),
    "checkout-index-force": ("ask", "git checkout-index -f",
                             "`git checkout-index --force` overwrites work-tree files from the index.",
                             "Local modifications to those files are lost.", ["git diff  # review what would be lost first"]),
    "read-tree-reset": ("deny", "git read-tree --reset -u",
                        "`git read-tree --reset -u` overwrites the work tree like `git reset --hard`.",
                        "Uncommitted changes are lost.", [_STASH, "git read-tree -m <tree>  # merge without reset"]),
    "read-tree-reset-index": ("ask", "git read-tree --reset",
                              "`git read-tree --reset` discards the index contents.", "Staged changes are lost.",
                              ["git diff --cached  # review staged changes first"]),
    "rm-tree": ("deny", "git rm -rf of the whole tree",
                "`git rm -f/-r` of the whole tree deletes every tracked file from the work tree.",
                "Uncommitted edits to those files are lost.", [_STASH, "git rm --cached <path>  # untrack without deleting"]),
    "rm-tree-ask": ("ask", "git rm -r of the whole tree",
                    "`git rm -r` of the whole tree removes every tracked file from the work tree and index.",
                    "It is a very large deletion.", ["git rm --cached -r <path>  # untrack without deleting"]),
    "update-ref-stdin": ("ask", "git update-ref --stdin",
                         "`git update-ref --stdin` applies ref updates/deletions read from stdin that the guard cannot see.",
                         "It can delete or move any branch.", ["Use `git update-ref <ref> <sha>` with explicit arguments"]),
    "push-prune": ("ask", "git push --prune",
                   "`git push --prune` deletes remote branches that have no local counterpart.",
                   "Remote-only branches (other people's work) are removed.", ["git push <remote> <branch>  # push one branch"]),
    "env-alias": ("ask", "GIT_CONFIG alias via environment",
                  "An alias is injected through GIT_CONFIG_* environment variables.",
                  "Aliases can run arbitrary commands the guard cannot classify.", ["Run the real command spelled out"]),
}

# ask-level rules that turn into deny in strict mode (history rewriting)
HISTORY_RULES = frozenset({"rebase", "commit-amend", "branch-force-delete", "branch-force-move",
                           "push-force", "push-delete", "reset-protected", "update-ref-protected",
                           "push-prune"})

GIT_SUBS = frozenset("""add am apply archive bisect blame branch bundle checkout cherry cherry-pick clean clone commit config describe diff
fetch filter-branch filter-repo format-patch fsck gc grep init log ls-files ls-remote merge mv notes prune pull push range-diff rebase reflog
remote repack replace reset restore revert rm shortlog show show-branch stash status submodule switch tag update-index update-ref worktree""".split())

RESERVED = frozenset({"{", "}", "!", "if", "then", "else", "elif", "fi", "while", "until", "do", "done", "esac", "coproc"})
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash", "mksh", "csh", "tcsh", "fish"})
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_REV_LIKE = re.compile(r"(~|\^|@\{|^[0-9a-fA-F]{7,40}$|^(origin|upstream)/)")
_ALL_MAGIC_REST = {"", ".", "./", "*", "**", ".*"}

# wrapper -> options that consume the following word
_WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "command": set(), "builtin": set(), "exec": {"-a"}, "nohup": set(), "setsid": set(),
    "time": {"-f", "-o", "--format", "--output"}, "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "-u"}, "stdbuf": {"-i", "-o", "-e"},
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t", "--user", "--group", "--host", "--prompt",
             "--chdir", "--role", "--type", "--close-from", "--other-user"},
    "doas": {"-u", "-C"}, "timeout": {"-s", "-k", "--signal", "--kill-after"}, "unbuffer": set(), "caffeinate": {"-t", "-w"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "-J", "--max-args", "--max-procs", "--delimiter",
              "--arg-file", "--max-lines", "--replace", "--eof", "--max-chars"},
}


def _all_rest(r: str) -> bool:
    """``r`` (a pathspec without magic prefix) selects everything below the current directory/top."""
    if r in _ALL_MAGIC_REST:
        return True
    return posixpath.normpath(r) in _ALL_MAGIC_REST    # './.', '././', './/', 'a/..' -> '.'


def is_all_pathspec(t: str) -> bool:
    """True for pathspecs that select the whole tree: ``.``, ``./.``, ``*``, ``:/``, ``:(top)``, ``..``."""
    t = t.strip()
    if t in ("..", "../") or (t and all(p in ("..", ".", "") for p in t.split("/"))):
        return True
    if t.startswith(":("):
        k = t.find(")")
        if k < 0 or "exclude" in t[:k]:
            return False
        return _all_rest(t[k + 1:])
    if t.startswith(":/"):
        return _all_rest(t[2:])
    if t.startswith(":") and t[1:2] in ("!", "^"):
        return False
    return _all_rest(t)


class Opts:
    """Generic short/long option splitter for one Git subcommand's argv."""

    def __init__(self, words: List[Word], short_arg=frozenset(), long_arg=frozenset()):
        self.shorts: set = set()
        self.short_vals: dict = {}
        self.longs: dict = {}
        self.pos: List[str] = []
        self.dd: List[str] = []
        self.bare: List[str] = []
        self.has_dyn = False
        self.dyn_flag = False      # an option-looking word with an unresolved suffix, e.g. --hard$IFS
        i, seen_dd, n = 0, False, len(words)
        while i < n:
            w = words[i]
            t = w.text
            i += 1
            if w.dyn:
                self.has_dyn = True
                if t.startswith("-") and not w.bare and not seen_dd:
                    self.dyn_flag = True
            if seen_dd:
                self.dd.append(t)
                continue
            if t == "--":
                seen_dd = True
                continue
            if w.bare:
                self.bare.append(t)
                self.pos.append(t)
                continue
            if t.startswith("--") and len(t) > 2:
                name, eq, val = t[2:].partition("=")
                if eq:
                    self.longs[name] = val
                elif name in long_arg and i < n:
                    self.longs[name] = words[i].text
                    i += 1
                else:
                    self.longs[name] = None
            elif t.startswith("-") and len(t) > 1 and not w.dyn:
                for k, ch in enumerate(t[1:]):
                    self.shorts.add(ch)
                    if ch in short_arg:
                        rest = t[2 + k:]
                        if rest:
                            self.short_vals[ch] = rest
                        elif i < n:
                            self.short_vals[ch] = words[i].text
                            i += 1
                        break
            else:
                self.pos.append(t)

    def lopt(self, full: str, minlen: int = 2) -> bool:
        """Long option present, accepting Git's unique-prefix abbreviations."""
        return any(len(k) >= minlen and full.startswith(k) for k in self.longs)

    def lval(self, full: str, minlen: int = 2) -> Optional[str]:
        for k, v in self.longs.items():
            if len(k) >= minlen and full.startswith(k):
                return v
        return None

    @property
    def all_pos(self) -> List[str]:
        return self.pos + self.dd


class _Ctx:
    def __init__(self, cfg: Config, branch: Optional[str]) -> None:
        self.cfg = cfg
        self.cands: List[Optional[str]] = [branch]    # possible current branches; None = unknown
        self.found: List[tuple] = []                  # (rank, order, Verdict)
        self.commands: List[str] = []
        self.budget = Budget()
        self.strict = cfg.safety_mode == "strict"
        self.depth = 0                                # script nesting depth of the invocation being classified

    def protected(self, name: Optional[str]) -> bool:
        if not name:
            return False
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.cfg.protected_branches)

    def add(self, rule: str, **fmt) -> None:
        decision, op, what, why, safer = RULES[rule]
        if decision == "ask" and self.ctx_strict() and rule in HISTORY_RULES:
            decision = "deny"
            why = why + " (strict safety mode: history rewriting is denied.)"
        reason = f"{_sub(what, fmt)} {_sub(why, fmt)}"
        v = Verdict(decision, rule, op, reason, [_sub(s, fmt) for s in safer])
        self.found.append((_RANK[decision], len(self.found), v))

    def ctx_strict(self) -> bool:
        return self.strict


def _sub(text: str, fmt: dict) -> str:
    """Substitute {branch}/{tool}/{path} only (other braces, e.g. stash@{N}, stay literal)."""
    return re.sub(r"\{(branch|tool|path)\}", lambda m: str(fmt.get(m.group(1), "?")), text)


def _joined(words: List[Word], limit: int = 12) -> str:
    return " ".join(w.text for w in words[:limit])


# ------------------------------------------------------------------- git classification
def _split_git(words: List[Word]):
    """Return (cfg_values, has_dir_override, sub, rest, info_only)."""
    cfgs: List[str] = []
    dir_override = False
    i, n = 0, len(words)
    info_only = False
    while i < n:
        t = words[i].text
        if t in ("-C", "-c"):
            if t == "-c" and i + 1 < n:
                cfgs.append(words[i + 1].text)
            else:
                dir_override = True
            i += 2
        elif t.startswith("--"):
            name, eq, val = t[2:].partition("=")
            if name in ("git-dir", "work-tree"):
                dir_override = True
            if name in ("config-env",):
                cfgs.append(val if eq else (words[i + 1].text if i + 1 < n else ""))
            if name in ("help", "version", "html-path", "man-path", "info-path", "exec-path") and not eq:
                info_only = True
            if name in ("git-dir", "work-tree", "namespace", "super-prefix", "config-env", "attr-source") and not eq:
                i += 2
            else:
                i += 1
        elif t in ("-p", "-P"):
            i += 1
        elif t.startswith("-") and len(t) > 1:
            i += 1
        else:
            break
    if i >= n:
        return cfgs, dir_override, None, [], info_only
    return cfgs, dir_override, words[i], words[i + 1:], info_only


def _git_invocation(words: List[Word], ctx: _Ctx, sub_override: Optional[str] = None) -> None:
    """``words`` are everything after the ``git`` word."""
    cfgs, dir_override, sub, rest, info_only = _split_git(words)
    if sub_override is not None:
        sub = Word(sub_override)
        rest = list(words)
        cfgs, dir_override = [], False
    shown = "git " + _joined(words)
    ctx.commands.append(redact(shown[:400]))   # cut BEFORE redacting (cheap, bounded)
    for c in cfgs:
        if c.lower().startswith("alias."):
            ctx.add("alias-inline")
    if sub is None or info_only:
        return
    if sub.dyn or sub.glob:
        ctx.add("unresolved-subcommand")
        return
    name = sub.text
    cands = [None] if dir_override else list(ctx.cands)
    fn = _HANDLERS.get(name)
    if name in ("filter-branch", "filter-repo"):
        ctx.add("history-tool", tool="git " + name)
        return
    if name == "gc":
        _gc_config(cfgs, ctx)
    before = len(ctx.found)
    if fn is not None:
        fn(rest, ctx, cands)
    if (len(ctx.found) == before and name in _DESTRUCTIVE_SUBS
            and any(w.dyn and not w.bare and w.text.startswith("-") for w in rest)):
        ctx.add("option-unresolved")   # e.g. `git reset --hard$IFS`: a flag we cannot read on a destructive-capable subcommand
    if name in ("checkout", "switch") and not dir_override:
        _track_branch(name, rest, ctx)


_DESTRUCTIVE_SUBS = frozenset({"reset", "clean", "push", "checkout", "restore", "branch", "gc", "reflog", "stash", "update-ref",
                               "tag", "worktree", "switch", "prune", "rm", "read-tree", "checkout-index", "submodule", "rebase"})
_EXPIRE_NOW = re.compile(r"^gc\.(prune|reflog)expire(unreachable)?\s*=\s*(now|all|0|0\.\w+)$", re.I)


def _gc_config(cfgs: List[str], ctx: _Ctx) -> None:
    """``git -c gc.pruneExpire=now gc`` is ``gc --prune=now``; gc.reflogExpire=now wipes the reflog."""
    for c in cfgs:
        m = _EXPIRE_NOW.match(c.strip())
        if m:
            ctx.add("gc-prune-now" if m.group(1).lower() == "prune" else "reflog-destroy")


def _cand_label(cands) -> str:
    names = [c for c in cands if c]
    return "/".join(dict.fromkeys(names)) or "the current branch"


def _track_branch(sub: str, rest: List[Word], ctx: _Ctx) -> None:
    o = Opts(rest, short_arg={"b", "B", "c", "C"}, long_arg={"orphan", "conflict"})
    for k in ("b", "B", "c", "C"):
        if k in o.short_vals:
            ctx.cands = [o.short_vals[k]]
            return
    if o.dd or not o.pos or o.has_dyn:
        return
    if sub == "switch" or o.pos[0] not in (".",):
        ctx.cands = [o.pos[0]] + [c for c in ctx.cands]


def _h_reset(rest, ctx, cands):
    o = Opts(rest)
    if o.lopt("hard", 2):
        ctx.add("reset-hard")
        return
    if o.lopt("soft", 2) or o.lopt("keep", 2) or o.lopt("merge", 2) or o.lopt("patch", 2) or "p" in o.shorts:
        return
    if o.bare:
        ctx.add("reset-unresolved")
        return
    pos = o.pos
    if pos and any(_REV_LIKE.search(p) for p in pos[:1]) and not o.dd:
        prot = [c for c in cands if ctx.protected(c)]
        if prot:
            ctx.add("reset-protected", branch=prot[0])


def _h_clean(rest, ctx, cands):
    o = Opts(rest, short_arg={"e"}, long_arg={"exclude"})
    dry = "n" in o.shorts or o.lopt("dry-run", 2)
    interactive = "i" in o.shorts or o.lopt("interactive", 3)
    if dry or interactive:
        return
    if "f" in o.shorts or o.lopt("force", 3):
        ctx.add("clean-force")
    elif o.bare:
        ctx.add("clean-unresolved")


def _force_create(o: Opts, key: str, ctx: _Ctx, cands) -> None:
    """``checkout -B`` / ``switch -C`` reset an existing branch to HEAD (like ``branch -f``).  Plain `-B newname` is
    common and harmless (and cannot be told apart from a reset without asking Git), so this asks only when the named
    branch is protected or cannot be read."""
    if key not in o.shorts and not o.lopt("force-create", 3):
        return
    name = o.short_vals.get(key) or o.lval("force-create", 3) or ""
    if not name or o.has_dyn or "$" in name or "`" in name or ctx.protected(name):
        ctx.add("branch-force-move")


def _h_checkout(rest, ctx, cands):
    o = Opts(rest, short_arg={"b", "B"}, long_arg={"orphan", "conflict", "pathspec-from-file"})
    _force_create(o, "B", ctx, cands)
    if "f" in o.shorts or o.lopt("force", 3):
        ctx.add("checkout-force")
        return
    if "p" in o.shorts or o.lopt("patch", 3):
        return
    if any(is_all_pathspec(p) for p in o.all_pos) and not ({"b", "B"} & set(o.short_vals)):
        ctx.add("checkout-discard-all")


def _h_switch(rest, ctx, cands):
    o = Opts(rest, short_arg={"c", "C"}, long_arg={"conflict"})
    _force_create(o, "C", ctx, cands)
    if "f" in o.shorts or o.lopt("force", 3) or o.lopt("discard-changes", 2):
        ctx.add("switch-discard")


def _h_restore(rest, ctx, cands):
    o = Opts(rest, short_arg={"s"}, long_arg={"source", "pathspec-from-file", "conflict"})
    staged = "S" in o.shorts or o.lopt("staged", 3)
    worktree = "W" in o.shorts or o.lopt("worktree", 3)
    if "p" in o.shorts or o.lopt("patch", 3) or (staged and not worktree):
        return
    if any(is_all_pathspec(p) for p in o.all_pos):
        ctx.add("checkout-discard-all")


def _h_reflog(rest, ctx, cands):
    o = Opts(rest)
    if o.pos and o.pos[0] in ("expire", "delete"):
        ctx.add("reflog-destroy")


def _h_gc(rest, ctx, cands):
    o = Opts(rest)
    for k, v in o.longs.items():
        if len(k) >= 3 and "prune".startswith(k) and v in ("now", "all"):
            ctx.add("gc-prune-now")
            return


def _h_prune(rest, ctx, cands):
    o = Opts(rest, short_arg=set(), long_arg={"expire"})
    if not ("n" in o.shorts or o.lopt("dry-run", 3)):
        ctx.add("prune")


def _h_stash(rest, ctx, cands):
    o = Opts(rest, short_arg={"m"}, long_arg={"message"})
    if o.pos and o.pos[0] == "clear":
        ctx.add("stash-clear")
    elif o.pos and o.pos[0] == "drop":
        ctx.add("stash-drop")


def _h_update_ref(rest, ctx, cands):
    o = Opts(rest, short_arg={"m"}, long_arg={"message"})
    ref = o.pos[0] if o.pos else ""
    if o.lopt("stdin", 3):
        ctx.add("update-ref-stdin")      # stdin may hold `delete refs/heads/main`; cannot be inspected
        return
    if "d" in o.shorts or o.lopt("delete", 3):
        if not ref or o.has_dyn:
            ctx.add("update-ref-delete")
        elif ref == "HEAD" or ref.startswith("refs/heads/"):
            ctx.add("update-ref-delete")
        else:
            ctx.add("update-ref-delete-other")
        return
    if ref == "HEAD" or (ref.startswith("refs/heads/") and ctx.protected(ref[len("refs/heads/"):])):
        ctx.add("update-ref-protected", branch=ref.replace("refs/heads/", ""))
    elif o.has_dyn and (ref == "" or "$" in ref or "`" in ref):
        ctx.add("update-ref-protected", branch="a branch")


def _norm_ref(ref: str) -> str:
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def _h_push(rest, ctx, cands):
    o = Opts(rest, short_arg={"o"}, long_arg={"push-option", "receive-pack", "exec", "repo"})
    force = "f" in o.shorts or any(k.startswith("force") or (len(k) >= 3 and "force".startswith(k) and not "follow-tags".startswith(k))
                                   for k in o.longs)
    mirror = o.lopt("mirror", 3)
    delete = "d" in o.shorts or o.lopt("delete", 3)
    all_ = o.lopt("all", 3) or "branches" in o.longs
    refspecs = o.all_pos[1:]
    if "n" in o.shorts or o.lopt("dry-run", 3):
        return  # nothing is sent
    if o.lopt("prune", 3):
        ctx.add("push-prune")
    if mirror:
        ctx.add("push-mirror")
        return
    force_dsts: List[Optional[str]] = []
    delete_dsts: List[Optional[str]] = []
    unknown = False
    for r in refspecs:
        plus = r.startswith("+")
        r = r.lstrip("+")
        src, colon, dst = r.partition(":")
        if not colon:
            dst = src
        if (delete and not colon) or (colon and src == ""):
            dst = _norm_ref(dst)
            delete_dsts.append(dst)
            if plus or force:
                force_dsts.append(dst)
            continue
        if dst in ("HEAD", "@", ""):
            targets = list(cands)
        elif dst.startswith("refs/tags/") or dst.startswith("refs/") and not dst.startswith("refs/heads/"):
            targets = [dst]
        else:
            targets = [_norm_ref(dst)]
        if "$" in dst or "*" in dst or "`" in dst:
            targets.append(None)
        if plus or force:
            force_dsts.extend(targets)
    if not refspecs:
        if all_:
            if force:
                ctx.add("push-force-protected", branch="all branches")
            return
        if force:
            force_dsts.extend(cands)
    for d in delete_dsts:
        if ctx.protected(d):
            ctx.add("push-delete-protected", branch=d)
            return
    if delete_dsts:
        ctx.add("push-delete", branch=", ".join(d or "?" for d in delete_dsts))
    for d in force_dsts:
        if ctx.protected(d):
            ctx.add("push-force-protected", branch=d)
            return
    if force_dsts:
        unknown = any(d is None for d in force_dsts)
        ctx.add("push-force", branch=_cand_label(force_dsts) if not unknown else "an unknown branch")


def _h_branch(rest, ctx, cands):
    o = Opts(rest, short_arg={"u"}, long_arg={"set-upstream-to", "sort", "format", "contains", "no-contains",
                                               "merged", "no-merged", "points-at", "color", "column"})
    delete = "d" in o.shorts or "D" in o.shorts or o.lopt("delete", 3)
    force = "f" in o.shorts or "D" in o.shorts or "M" in o.shorts or "C" in o.shorts or o.lopt("force", 3)
    move = "m" in o.shorts or "M" in o.shorts or o.lopt("move", 3)
    copy = "c" in o.shorts or "C" in o.shorts or o.lopt("copy", 3)
    if delete and force:
        ctx.add("branch-force-delete", branch=", ".join(o.pos) or "?")
    elif force and (move or copy or o.pos):
        ctx.add("branch-force-move")


def _h_commit(rest, ctx, cands):
    o = Opts(rest, short_arg={"m", "F", "C", "c", "t"},
             long_arg={"message", "file", "reuse-message", "reedit-message", "author", "date", "template", "cleanup",
                       "fixup", "squash", "trailer"})
    if o.lopt("amend", 2):
        ctx.add("commit-amend")


def _h_rebase(rest, ctx, cands):
    o = Opts(rest, short_arg={"s", "X", "x"}, long_arg={"onto", "exec", "strategy", "strategy-option"})
    for full in ("abort", "continue", "skip", "quit", "show-current-patch", "edit-todo"):
        if o.lopt(full, 3):
            return
    ctx.add("rebase")
    ex = o.short_vals.get("x") if "x" in o.short_vals else o.lval("exec", 2)
    if ex:
        _run_string(ex, ctx)


def _run_string(text: str, ctx: _Ctx) -> None:
    """Classify a literal command string handed to git (``rebase --exec``, ``submodule foreach``, ``bisect run``)."""
    try:
        _script(text, ctx, ctx.depth + 1)
    except (ShellParseError, RecursionError):
        if "git" in text.lower():
            ctx.add("too-complex")


def _h_tag(rest, ctx, cands):
    o = Opts(rest, short_arg={"m", "F", "u"}, long_arg={"message", "file", "local-user", "format", "sort", "contains",
                                                         "no-contains", "points-at", "merged", "no-merged", "cleanup"})
    if "d" in o.shorts or "f" in o.shorts or o.lopt("delete", 3) or o.lopt("force", 3):
        ctx.add("tag-rewrite")


def _h_worktree(rest, ctx, cands):
    o = Opts(rest, short_arg={"b", "B"}, long_arg={"reason"})
    if o.pos and o.pos[0] == "remove" and ("f" in o.shorts or o.lopt("force", 3)):
        ctx.add("worktree-force-remove")


def _h_submodule(rest, ctx, cands):
    o = Opts(rest)
    if o.pos and o.pos[0] == "foreach":
        cmd_words = [w for w in rest if w.text not in ("foreach", "--recursive", "-q", "--quiet", "--")]
        if cmd_words:
            if any(w.dyn for w in cmd_words):
                if _mentions_git(cmd_words):
                    ctx.add("shell-git")
            else:
                _run_string(" ".join(w.text for w in cmd_words), ctx)
        return
    if o.pos and o.pos[0] == "deinit" and ("f" in o.shorts or o.lopt("force", 3)):
        ctx.add("submodule-deinit-force")


def _h_bisect(rest, ctx, cands):
    for i, w in enumerate(rest):
        if w.text == "run" and not w.dyn:
            sub_words = rest[i + 1:]
            if sub_words:
                _words(sub_words, None, ctx, ctx.depth + 1)
            return
        if not w.text.startswith("-"):
            return


def _h_checkout_index(rest, ctx, cands):
    o = Opts(rest)
    if "f" in o.shorts or o.lopt("force", 3):
        if "a" in o.shorts or o.lopt("all", 3) or any(is_all_pathspec(p) for p in o.all_pos):
            ctx.add("checkout-discard-all")      # overwrites every file, like `restore .`
        else:
            ctx.add("checkout-index-force")


def _h_read_tree(rest, ctx, cands):
    o = Opts(rest)
    if o.lopt("reset", 3):
        ctx.add("read-tree-reset" if "u" in o.shorts else "read-tree-reset-index")


def _h_git_rm(rest, ctx, cands):
    o = Opts(rest)
    if o.lopt("cached", 3) or o.lopt("dry-run", 3) or "n" in o.shorts:
        return
    if any(is_all_pathspec(p) for p in o.all_pos):
        force = "f" in o.shorts or o.lopt("force", 3)
        recursive = "r" in o.shorts or o.lopt("recursive", 3)
        ctx.add("rm-tree-ask" if (recursive and not force) else "rm-tree")


def _h_config(rest, ctx, cands):
    o = Opts(rest, short_arg={"f"}, long_arg={"file", "blob", "type", "default"})
    read = ("l" in o.shorts or any(k.startswith(("get", "list", "unset", "remove-section")) for k in o.longs)
            or (o.pos and o.pos[0] in ("get", "list", "unset")))
    if read:
        return
    if any(p.lower().startswith("alias.") for p in o.pos[:2]):
        ctx.add("alias-config")


def _h_remote(rest, ctx, cands):
    o = Opts(rest)
    if o.pos and o.pos[0] in ("set-url", "remove", "rm"):
        ctx.add("remote-modify")


_HANDLERS = {
    "reset": _h_reset, "clean": _h_clean, "checkout": _h_checkout, "switch": _h_switch, "restore": _h_restore,
    "reflog": _h_reflog, "gc": _h_gc, "prune": _h_prune, "stash": _h_stash, "update-ref": _h_update_ref,
    "push": _h_push, "branch": _h_branch, "commit": _h_commit, "rebase": _h_rebase, "tag": _h_tag,
    "worktree": _h_worktree, "submodule": _h_submodule, "config": _h_config, "remote": _h_remote,
    "bisect": _h_bisect, "checkout-index": _h_checkout_index, "read-tree": _h_read_tree, "rm": _h_git_rm,
}


# ------------------------------------------------------------------- shell-level analysis
def _skip_wrapper(name: str, args: List[Word], ctx: _Ctx, depth: int):
    """Strip option words of a wrapper. Returns the remaining words (or None if fully handled)."""
    argopts = _WRAPPERS[name]
    i, n = 0, len(args)
    while i < n:
        t = args[i].text
        if t == "--":
            i += 1
            break
        if _ASSIGN.match(t) and name == "env":
            i += 1
            continue
        if t.startswith("-") and len(t) > 1 and not args[i].dyn:
            if name == "command" and ("v" in t or "V" in t):
                return None
            if name == "env" and t in ("-S", "--split-string") and i + 1 < n:
                _script(args[i + 1].text, ctx, depth + 1)
                return None
            if t in argopts:
                i += 2
            else:
                i += 1
        else:
            break
    if name == "timeout" and i < n:
        i += 1
    rest = args[i:]
    if name == "env":
        while rest and _ASSIGN.match(rest[0].text):
            rest = rest[1:]
    return rest


_ECHO_OPT = re.compile(r"^-[neE]+$")


def _stdin_scripts(cmd: Optional[Cmd]) -> List[str]:
    """Literal text that is piped / here-doc'd into ``cmd`` (for ``echo ... | bash``)."""
    out: List[str] = []
    if cmd is None:
        return out
    out.extend(cmd.heredocs)
    out.extend(cmd.herestrings)
    prev = cmd.pipe_prev
    if prev is not None and prev.words and not prev.words[0].dyn:
        pname = posixpath.basename(prev.words[0].text)
        if pname == "echo":
            args = list(prev.words[1:])
            while args and _ECHO_OPT.match(args[0].text):      # only leading -n/-e/-E; `--hard` etc. are data
                args.pop(0)
            out.append(" ".join(w.text for w in args).replace("\\n", "\n"))
        elif pname == "printf":
            args = [w.text for w in prev.words[1:]]
            if args:
                if "%" in args[0]:          # format string: the operands are the printed data
                    data = args[1:]
                    out.append(" ".join(data))
                    out.append("\n".join(data))
                    out.append(args[0].replace("\\n", "\n"))
                else:
                    out.append(" ".join(args).replace("\\n", "\n"))
        elif pname == "cat":
            out.extend(prev.heredocs)
            out.extend(prev.herestrings)
    return out


def _script(text: str, ctx: _Ctx, depth: int) -> None:
    if depth > MAX_SCRIPT_DEPTH:
        raise ShellParseError("script nesting too deep")
    cmds = parse_script(text, ctx.budget, depth)
    _cmds(cmds, ctx, depth)


def _mentions_git(words: List[Word]) -> bool:
    return any("git" in w.text.lower() for w in words)


_IFS = re.compile(r"\$\{IFS[^}]*\}|\$IFS\b")
# text/search/print tools whose arguments merely MENTION git (never run it), and remote/container launchers whose
# behaviour is documented as a blind spot
_TEXT_TOOLS = frozenset("""echo printf grep egrep fgrep rg ag ack cat less more man which type whereis head tail wc sed awk gawk nawk
tr cut sort uniq diff cmp tee ls file stat open code vim vi nano emacs touch mkdir cp mv ln gh hub glab ssh scp sftp mosh
docker podman kubectl nerdctl brew apt apt-get pip pip3 npm npx yarn pnpm cargo go whatis apropos info say osascript
test [ [[ true false :""".split())
_INTERP = re.compile(r"^(python[0-9.]*|pypy[0-9]*|node|nodejs|deno|bun|ruby|perl|php|lua|osascript)$")
_GIT_PHRASE = re.compile(r"\bgit(?:-[a-z-]+)?\b[^'\"`)\n;]*")
_GIT_ARGV = re.compile(r"""['"]\s*,\s*['"]""")


def _split_ifs(words: List[Word]) -> List[Word]:
    """``git${IFS}reset${IFS}--hard`` is three words to the shell: split on $IFS so the literal parts can be read."""
    if not any(w.dyn and "IFS" in w.text for w in words):
        return words
    out: List[Word] = []
    for w in words:
        if w.dyn and "IFS" in w.text and _IFS.search(w.text):
            for piece in _IFS.split(w.text):
                if piece:
                    out.append(Word(piece, dyn="$" in piece or "`" in piece, quoted=w.quoted))
        else:
            out.append(w)
    return out


def _is_git_word(w: Word) -> bool:
    if w.dyn:
        return False
    b = posixpath.basename(w.text)
    return b == "git" or (b.startswith("git-") and b[4:] in GIT_SUBS)


def _interpreter(args: List[Word], ctx: _Ctx) -> None:
    """``python3 -c "os.system('git reset --hard')"``: a literal string that contains a destructive git phrase -> ask."""
    for w in args:
        if w.dyn:
            continue
        text = _GIT_ARGV.sub(" ", w.text)
        for m in _GIT_PHRASE.finditer(text):
            probe = _Ctx(ctx.cfg, None)
            probe.depth = ctx.depth
            try:
                _script(m.group(0), probe, ctx.depth + 1)
            except (ShellParseError, RecursionError):
                continue
            if probe.found:
                ctx.add("interpreter-git")
                return


def _words(words: List[Word], cmd: Optional[Cmd], ctx: _Ctx, depth: int) -> None:
    ctx.depth = depth
    words = _split_ifs(list(words))
    while words:
        t = words[0].text
        if not words[0].dyn and (t in RESERVED):
            words.pop(0)
        elif _ASSIGN.match(t) and not words[0].dyn:
            if t.upper().startswith("GIT_CONFIG_KEY_") and t.partition("=")[2].lower().startswith("alias."):
                ctx.add("env-alias")
            words.pop(0)
        else:
            break
    if words and words[0].text == "function" and not words[0].dyn:
        words = words[2:]
        while words and words[0].text in RESERVED:
            words.pop(0)
    if not words:
        return
    head = words[0]
    if head.dyn:
        if any(posixpath.basename(w.text) == "git" for w in words[1:]):
            ctx.add("unresolved-command")
            _words(words[1:], cmd, ctx, depth)
        elif "git" in head.text.lower() or any(w.text in _HANDLERS or w.text in ("filter-branch", "filter-repo")
                                               for w in words[1:4] if not w.dyn and not w.text.startswith("-")):
            # `$(echo git) reset --hard`, `$GIT reset --hard`, `"$(which git)" ...`: the head could be git.
            ctx.add("unresolved-command")
            _git_invocation(words[1:], ctx)      # literal arguments may already prove it destructive (deny)
        return
    name = posixpath.basename(head.text)
    args = words[1:]
    if name == "git":
        _git_invocation(args, ctx)
    elif name.startswith("git-") and name[4:] in GIT_SUBS:
        _git_invocation(args, ctx, sub_override=name[4:])
    elif name in _WRAPPERS:
        rest = _skip_wrapper(name, args, ctx, depth)
        if rest:
            _words(rest, cmd, ctx, depth)
    elif name in SHELLS or name == "su":
        _shell(name, args, cmd, ctx, depth)
    elif name == "eval":
        if any(w.dyn for w in args):
            if _mentions_git(args):
                ctx.add("eval-git")
        elif args:
            _script(" ".join(w.text for w in args), ctx, depth + 1)
    elif name == "find":
        _find(args, cmd, ctx, depth)
    elif name in ("cd", "pushd", "popd"):
        ctx.cands = [None]
    elif name == "rm":
        _rm(args, ctx)
    elif _INTERP.match(name):
        _interpreter(args, ctx)
    elif name not in _TEXT_TOOLS:
        # Unknown launcher (arch, xcrun, flock, watch, parallel, script, busybox, chroot, unshare, ...): if a literal
        # `git` word appears later, classify from there.  Text/search tools are excluded so `grep git` stays allowed.
        for i, w in enumerate(args):
            if _is_git_word(w):
                _words(args[i:], cmd, ctx, depth)
                break


def _shell(name: str, args: List[Word], cmd: Optional[Cmd], ctx: _Ctx, depth: int) -> None:
    c_flag = False
    i, n = 0, len(args)
    while i < n:
        t = args[i].text
        if t == "--":
            i += 1
            break
        if t.startswith(("-", "+")) and len(t) > 1:
            if not t.startswith("--") and "c" in t[1:] and t[0] == "-":
                c_flag = True
            if t in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                i += 1
            i += 1
            continue
        break
    if c_flag:
        if i < n:
            s = args[i]
            if s.dyn:
                if "git" in s.text.lower():
                    ctx.add("shell-git")
            else:
                _script(s.text, ctx, depth + 1)
        return
    if name == "su":
        return
    if i >= n:  # script comes from stdin
        for text in _stdin_scripts(cmd):
            _script(text, ctx, depth + 1)


def _find(args: List[Word], cmd, ctx: _Ctx, depth: int) -> None:
    i, n = 0, len(args)
    while i < n:
        if args[i].text in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            while j < n and args[j].text not in (";", "+"):
                j += 1
            sub = args[i + 1:j]
            if sub:
                _words(sub, cmd, ctx, depth)
            i = j
        i += 1


def _rm(args: List[Word], ctx: _Ctx) -> None:
    recursive = False
    targets: List[str] = []
    seen_dd = False
    for w in args:
        t = w.text
        if not seen_dd and t == "--":
            seen_dd = True
        elif not seen_dd and t.startswith("--"):
            if t == "--recursive":
                recursive = True
        elif not seen_dd and t.startswith("-") and len(t) > 1:
            if "r" in t[1:] or "R" in t[1:]:
                recursive = True
        else:
            targets.append(t)
    if not recursive:
        return
    for t in targets:
        p = t.rstrip("/") or t
        for suffix in ("/*", "/."):
            while p.endswith(suffix):
                p = p[: -len(suffix)].rstrip("/")
        if p == ".git" or p.endswith("/.git") or p.endswith(".git"):
            ctx.add("rm-git", path=t)
            return


def _cmds(cmds: List[Cmd], ctx: _Ctx, depth: int) -> None:
    for cmd in cmds:
        _words(cmd.words, cmd, ctx, depth)


# --------------------------------------------------------------------------- public API
def classify_command(command: str, cfg: Optional[Config] = None, branch: Optional[str] = None) -> Verdict:
    """Classify a shell command string. Pure; never executes anything."""
    cfg = cfg or Config()
    if not isinstance(command, str):
        return Verdict("allow")
    ctx = _Ctx(cfg, branch)
    text = command.replace("\x00", " ")
    mentions_git = "git" in text.lower()
    try:
        if len(text) > MAX_COMMAND_CHARS:
            if mentions_git:
                ctx.add("too-complex")
        else:
            _script(text, ctx, 0)
    except (ShellParseError, RecursionError):
        if mentions_git:
            ctx.add("too-complex")
    if not ctx.found:
        return Verdict("allow", "allow", "", "", [], ctx.commands[:20])
    ctx.found.sort(key=lambda f: (-f[0], f[1]))
    best = ctx.found[0][2]
    best.commands = ctx.commands[:20]
    return best
