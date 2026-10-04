"""Deterministic Git command classifier: ``classify_command(command, cfg, branch) -> Verdict``.

This is the ONE Guardian decision API (the PreToolUse hook and ``warp.py guard check`` both call it).  It is a pure
function: it tokenizes the command string (never runs it, never executes an expansion), finds every Git invocation
(through wrappers, subshells, ``bash -c``, ``eval "literal"``, ``xargs``, ``find -exec``, here-docs piped to a shell)
and returns the MOST severe verdict.  See ``docs/guard-limitations.md`` for what it cannot see.

Canonical decisions
-------------------
``deny``   a clearly recognised prohibited destructive operation.
``ask``    a risky-but-legitimate operation, or an ambiguous / dynamic construct the guard cannot read.
``defer``  the command is confidently outside Guardian's scope, or confidently harmless by explicit rule.  It does NOT
           mean "safe", "approved" or "allowed": it only means Guardian has no objection and ordinary Claude Code
           permissions decide.  (The hook prints ``{}`` for it.)
In ``safety_mode: strict`` history-rewriting ``ask`` rules become ``deny``.

Fail-safe uncertainty rule (dynamic expressions)
------------------------------------------------
A dynamic word -- command substitution, backtick, parameter expansion, a variable used as executable or subcommand, a
comma-brace expansion, a process substitution or a script generated and run in the same command -- must never produce a
confident ``defer`` when it sits in an option-capable, subcommand or executable position of a destructive-capable Git
subcommand (reset, clean, push, checkout, restore, branch, switch, stash, tag, rebase, worktree, update-ref, ...).
Such constructs are ``ask`` (or ``deny`` when the literal part already proves destruction).  Dynamic words stay
``defer`` in read-only Git commands (log, diff, show, status, rev-parse, merge-base, ls-files, ...) and in non-Git
commands, and a substitution whose every command is a read-only Git command (or ``date``/``pwd``/``whoami``...) is
treated as a harmless producer.  Variable boundary inside a destructive-capable subcommand: ANY unresolved
variable word -- quoted or not, before or after an operand -- is ``ask``, because its value may start with ``-`` (option),
``+`` (forced refspec) or ``:`` (deletion refspec); ``BRANCH=:dev; git push origin "$BRANCH"`` deletes a remote branch.
Exemptions: values consumed by message/file style options (``-m "$MSG"``, ``-F``, ``-t``, ``--message``, ...), anything after a
literal ``--``, a word with a literal non-option prefix and no colon (``feature-$X``, ``refs/heads/$B``, ``HEAD~$N``),
variables with a known static value in the same string, and read-only subcommands.  Honest trade-off: legitimate scripts such
as ``git checkout "$BRANCH"`` or ``git push origin "$B"`` now ask every time; the guard cannot see the value and prefers the
prompt to a silent deletion.  A variable assigned in the same command
string is the exception: exactly one static value is substituted (never evaluated), a value assigned only from read-only
Git output cannot be an option, an option-like / computed / multiply-assigned value is ``ask`` (or ``deny`` if the literal
proves destruction).  Dry-run modes that Git guarantees win over any other flag (``clean -n``, ``push -n``) settle the
command; ``reset --soft $X`` does not (real Git: a later ``--hard`` wins).  File-writing options on read-looking
commands (``--output`` on diff/log/show/..., ``format-patch -o``, ``archive -o``, ``bundle create``, ``fast-export
--export-marks``, ``grep -O``) and ``-c`` keys that execute programs are ``ask`` (rules ``output-file``, ``config-exec``).
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
_RANK = {"defer": 0, "ask": 1, "deny": 2}


@dataclass
class Verdict:
    decision: str                      # allow | ask | deny
    rule: str = "defer"
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
    "dynamic-argument": ("ask", "git <destructive> with a computed argument",
                         "A destructive-capable Git command receives an argument produced by a command substitution or by a "
                         "variable that was assigned an option-like or dynamic value.",
                         "Git Warp does not run expansions, so it cannot tell whether the argument is a force/hard flag or a protected ref.",
                         ["Spell out the Git flags and refs literally"]),
    "generated-script": ("ask", "script generated then run in the same command",
                         "A file is written and then executed (or sourced) in the same command line.",
                         "Git Warp cannot verify what the generated script will do.",
                         ["Write the script first, review it, then run it as a separate step"]),
    "stdin-script": ("ask", "shell reading a computed script",
                     "A shell is given a script through a process substitution, /dev/stdin or similar and the command mentions git.",
                     "Git Warp cannot see the script text that will actually run.", ["Run the Git command directly so it can be reviewed"]),
    "unknown-subcommand": ("ask", "git <unrecognised subcommand>",
                           "`git {tool}` is not a known Git subcommand, so it may be a configured alias (for example `alias.x = reset --hard`).",
                           "Git aliases cannot shadow builtins, but the guard does not read git config and cannot see what an alias expands to.",
                           ["Spell out the real Git command", "git config --get-regexp '^alias\\.'  # inspect the aliases first"]),
    "fetch-force-protected": ("deny", "git fetch/pull --force into a protected local branch",
                              "A forced fetch/pull refspec overwrites the local branch '{branch}' (protected) with remote history.",
                              "Local commits on that branch are discarded without a way back except the reflog.",
                              ["git fetch origin  # update remote-tracking refs only", "git merge --ff-only origin/<branch>"]),
    "fetch-force-local": ("ask", "git fetch/pull --force into a local branch",
                          "A forced fetch/pull refspec overwrites or rewinds the local branch '{branch}'.",
                          "Local commits on that branch can be lost.",
                          ["git fetch origin  # update remote-tracking refs only", "git branch rescue/pre-fetch  # keep the old tip"]),
    "output-file": ("ask", "git command writing a file",
                    "`git {tool}` is given an option that creates or truncates a file ({opt}) or runs a helper program.",
                    "Read-only looking Git commands can overwrite an arbitrary path with these options.",
                    ["Print to the terminal and redirect explicitly, or review the target path first"]),
    "exec-option": ("ask", "git option or environment that runs a helper program",
                    "`{tool}` makes Git execute a program ({opt}): an external diff/textconv driver, an --upload-pack/--receive-pack/--exec "
                    "helper, a launcher command, or an environment variable such as GIT_SSH_COMMAND / GIT_EXTERNAL_DIFF.",
                    "The program is not visible to the guard and runs with your privileges; `git diff:*` style approvals do not cover it.",
                    ["Run the plain command without the helper option", "Review the helper program first"]),
    "config-exec": ("ask", "git -c <config that runs a program>",
                    "An inline `-c {tool}` sets a Git config key that makes Git execute a program (pager, editor, fsmonitor, hooks, ...).",
                    "The program is not visible to the guard and runs with your privileges.", ["Run the Git command without the -c override"]),
    "env-alias": ("ask", "GIT_CONFIG alias via environment",
                  "An alias is injected through GIT_CONFIG_* environment variables.",
                  "Aliases can run arbitrary commands the guard cannot classify.", ["Run the real command spelled out"]),
}

# ask-level rules that turn into deny in strict mode (history rewriting)
HISTORY_RULES = frozenset({"rebase", "commit-amend", "branch-force-delete", "branch-force-move",
                           "push-force", "push-delete", "reset-protected", "update-ref-protected",
                           "push-prune", "fetch-force-local"})

# Static list of Git builtins / plumbing / porcelain (from `git --list-cmds=main,others,nohelpers`, Git 2.53) plus common
# externals.  Git aliases cannot shadow builtins, so a subcommand NOT in this set may be a user/repo-config alias
# (`alias.x = reset --hard`) that the guard cannot see -> ask.  Static on purpose: the guard never shells out to git config.
GIT_BUILTINS = frozenset("""add am annotate apply archimport archive backfill bisect blame branch bugreport bundle cat-file check-attr
check-ignore check-mailmap check-ref-format checkout checkout-index cherry cherry-pick clean clone column commit commit-graph commit-tree
config count-objects credential credential-cache credential-gcloud credential-netrc credential-osxkeychain credential-store
cvsexportcommit cvsimport cvsserver daemon describe diagnose diff diff-files diff-index diff-pairs diff-tree difftool write-tree
fast-export fast-import fetch fetch-pack filter-branch fmt-merge-msg for-each-ref for-each-repo format-patch fsck fsck-objects worktree
gc get-tar-commit-id grep hash-object help hook http-backend http-fetch http-push imap-send index-pack init init-db instaweb
interpret-trailers jump last-modified log ls-files ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base merge-file
merge-index merge-octopus merge-one-file merge-ours merge-recursive merge-recursive-ours merge-recursive-theirs merge-resolve
merge-subtree merge-tree mergetool mktag mktree multi-pack-index mv name-rev notes p4 pack-objects pack-redundant pack-refs patch-id
pickaxe prune prune-packed pull push quiltimport range-diff read-tree rebase receive-pack reflog refs remote remote-ext remote-fd
remote-ftp remote-ftps remote-http remote-https repack replace replay repo request-pull rerere reset restore rev-list rev-parse revert
rm send-email send-pack whatchanged shell shortlog show show-branch show-index show-ref sparse-checkout stage stash status stripspace
submodule version subtree switch symbolic-ref tag unpack-file unpack-objects update-index update-ref update-server-info
upload-archive verify-tag upload-pack var verify-commit verify-pack
lfs gui gitk citool scalar svn flow annex filter-repo""".split())

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

    def __init__(self, words: List[Word], short_arg=frozenset(), long_arg=frozenset(), ctx: "Optional[_Ctx]" = None):
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
                if ctx is not None and not seen_dd and t != "--":
                    ctx.dyn_seen.append((w, bool(self.pos)))      # dynamic word in an option-capable position (values of known options are consumed below)
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
        self.mentions_git = False                     # the whole command string mentions git
        self.vars: dict = {}                          # name -> [static value | _UNKNOWN | _TAINT, ...] assigned in this string
        self.written: set = set()                     # files written by redirection / tee earlier in this string
        self.exec_env: set = set()                    # exec-bearing environment assignments seen in this string
        self.settled = False                          # a handler proved the mode harmless regardless of dynamic words (dry-run)
        self.dyn_seen: list = []                # dynamic option-position words seen by the current handler

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

    # ---- variables assigned in this command string (tracked literally, never evaluated)
    def record_var(self, name: str, w: Word, append: bool = False) -> None:
        if not w.dyn:
            st = w.text
        elif w.subs and not all(_benign_cmds(c) for c in w.subs):
            st = _TAINT
        else:
            src = _var_name(w)
            if src:
                st = self.var_state(src)
            else:
                st = _BENIGN if (w.subs and w.varexp == 0) else _UNKNOWN   # only read-only Git / date-like producers
            if st is None:
                st = _UNKNOWN
        if append and name in self.vars:
            prev = self.vars[name]
            self.vars[name] = prev + [st]
        else:
            self.vars.setdefault(name, []).append(st)

    def var_state(self, name: str):
        """str (exactly one static assignment) | _TAINT | _UNKNOWN | None (never assigned here)."""
        vals = self.vars.get(name)
        if not vals:
            return None
        if len(vals) == 1:
            return vals[0]
        if any(v is _TAINT or (isinstance(v, str) and _val_risky(v)) for v in vals):
            return _TAINT
        return _BENIGN if all(v is _BENIGN for v in vals) else _UNKNOWN


def _sub(text: str, fmt: dict) -> str:
    """Substitute {branch}/{tool}/{path} only (other braces, e.g. stash@{N}, stay literal)."""
    return re.sub(r"\{(branch|tool|path|opt)\}", lambda m: str(fmt.get(m.group(1), "?")), text)


def _joined(words: List[Word], limit: int = 12) -> str:
    return " ".join(w.text for w in words[:limit])



# ------------------------------------------------------------------- dynamic-word analysis (never executes anything)
_BENIGN = object()       # variable assigned only from read-only Git / harmless producers: cannot be an option
_UNKNOWN = object()      # variable assigned something we cannot read, but not alarming (e.g. $(git rev-parse HEAD))
_TAINT = object()        # variable assigned a computed value (non-benign substitution)
READONLY_GIT = frozenset("""log show diff status rev-parse rev-list merge-base describe ls-files ls-tree cat-file for-each-ref
show-ref name-rev shortlog blame grep ls-remote diff-tree diff-index diff-files whatchanged var count-objects""".split())
BENIGN_PRODUCERS = frozenset({"date", "pwd", "whoami", "hostname", "uname", "id", "nproc", "arch"})
PIPE_FILTERS = frozenset({"head", "tail", "wc", "sort", "uniq", "cut"})
_VARNAME = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_PLAIN_VALUE = re.compile(r"^[^\s*?\[\]{}$`'\"\\;&|<>()~]*$")
STDIN_PATHS = ("/dev/stdin", "-", "/proc/self/fd/0")


def _benign_cmds(cmds: List[Cmd]) -> bool:
    """Every command inside a substitution is a read-only Git command or a harmless text/status producer."""
    for c in cmds:
        if not c.words:
            continue
        if c.redirs:
            return False
        h = c.words[0]
        if h.dyn:
            return False
        name = posixpath.basename(h.text)
        if name == "git":
            _c, _d, sub, rest, _i = _split_git(c.words[1:])
            if sub is None or sub.dyn or sub.text not in READONLY_GIT and not (
                    sub.text == "branch" and any(r.text in ("--show-current", "--list", "-l") for r in rest)):
                return False
            if any(r.text.startswith("--output") or r.text == "-O" for r in rest):
                return False
        elif name in PIPE_FILTERS:
            if c.pipe_prev is None:
                return False
        elif name not in BENIGN_PRODUCERS:
            return False
    return True


def _norm_path(t: str) -> str:
    t = t.strip()
    while t.startswith("./"):
        t = t[2:]
    return posixpath.normpath(t) if t else t


def _val_risky(v: str) -> bool:
    return "git" in v.lower() or any(p[:1] in ("-", "+") for p in v.split())


def _var_name(w: Word) -> Optional[str]:
    if not w.bare:
        return None
    m = _VARNAME.match(w.text)
    return m.group(1) if m else None


def _risky_word(w: Word, ctx: "_Ctx") -> bool:
    """A dynamic word whose expansion could be an option / protected ref / force marker."""
    if not w.dyn:
        return False
    if w.subs and not all(_benign_cmds(c) for c in w.subs):
        return True
    n = _var_name(w)
    if n is not None:
        st = ctx.var_state(n)
        return st is _TAINT or (isinstance(st, str) and _val_risky(st))
    if w.glob and "{" in w.text and ("," in w.text or ".." in w.text) and w.text[:1] in ("-", "+", "{"):
        return True
    return False


def _uncertain_position(w: Word, seen_pos: bool, ctx: "_Ctx") -> bool:
    """Boundary for dynamic words in a destructive-capable Git subcommand (documented in the module docstring).

    ANY unresolved variable word -- quoted or not, before or after an operand -- is uncertain, because its value may start
    with ``-`` (option), ``+`` (forced refspec) or ``:`` (deletion refspec): ``git push origin "$BRANCH"`` with
    ``BRANCH=:dev`` deletes the remote branch.  Exempt (the caller never passes these here): values consumed by value
    options (``-m "$MSG"``, ``-F``, ``-t`` ...), anything after a literal ``--``.  Exempt here: a word with a literal
    prefix that cannot start an option / refspec and holds no ``:`` (``feature-$X``, ``refs/heads/$B``, ``HEAD~$N``), a
    variable with a known static value, and a variable assigned only from read-only Git output."""
    if _risky_word(w, ctx):
        return True
    if w.varexp == 0:
        return False
    n = _var_name(w)
    if n is not None:
        st = ctx.var_state(n)
        if st is _BENIGN or isinstance(st, str):
            return False
    return w.text[:1] in ("$", "-", "+", ":", "`") or ":" in w.text


def _subst_known(words: List[Word], ctx: "_Ctx"):
    """Literal substitution of plain ``$VAR`` operands whose single static assignment is visible in this same command
    string.  Returns (words, flagged); ``flagged`` means a variable carried an option-like / computed value."""
    out: List[Word] = []
    flagged = False
    for w in words:
        n = _var_name(w)
        if n is None:
            out.append(w)
            continue
        st = ctx.var_state(n)
        if st is _TAINT:
            flagged = True
            out.append(w)
        elif st is _BENIGN:
            out.append(Word("REV~0"))            # read-only Git output: cannot be an option; rev-like placeholder keeps branch checks
        elif isinstance(st, str):
            if _PLAIN_VALUE.match(st):
                parts = st.split()
                if not parts:
                    if w.quoted:
                        out.append(Word(""))
                    continue
                if any(p[:1] in ("-", "+") for p in parts):
                    flagged = True
                out.extend(Word(p) for p in parts)
            else:
                flagged = flagged or _val_risky(st)
                out.append(w)
        else:
            out.append(w)
    return out, flagged


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
        elif _exec_config(c.partition("=")[0], c.partition("=")[2]):
            ctx.add("config-exec", tool=c.partition("=")[0].strip()[:60])
    if sub is None or info_only:
        return
    if ctx.exec_env:
        ctx.add("exec-option", tool="git " + (sub.text[:40] if not sub.dyn else "?"), opt="environment: " + ", ".join(sorted(ctx.exec_env)))
    if sub.dyn or sub.glob:
        ctx.add("unresolved-subcommand")
        known = ctx.var_state(_var_name(sub)) if _var_name(sub) else None
        if isinstance(known, str) and _PLAIN_VALUE.match(known) and known.strip():
            _git_invocation([Word(known)] + list(rest), ctx)     # the literal value may prove destruction (deny)
        return
    rest, flagged = _subst_known(rest, ctx)
    name = sub.text
    opt = _output_file_option(name, rest)
    if opt:
        ctx.add("output-file", tool=name, opt=opt)
    xo = _exec_option(name, rest, ctx) if name != "rebase" else None
    if xo:
        ctx.add("exec-option", tool="git " + name, opt=xo)
    if sub_override is None and name not in GIT_BUILTINS:
        ctx.add("unknown-subcommand", tool=name[:60])
        return
    cands = [None] if dir_override else list(ctx.cands)
    fn = _HANDLERS.get(name)
    if name in ("filter-branch", "filter-repo"):
        ctx.add("history-tool", tool="git " + name)
        return
    if name == "gc":
        _gc_config(cfgs, ctx)
    before = len(ctx.found)
    ctx.dyn_seen = []
    ctx.settled = False
    if fn is not None:
        fn(rest, ctx, cands)
    elif name in _DESTRUCTIVE_SUBS:
        Opts(rest, ctx=ctx)
    dyn_seen, ctx.dyn_seen = ctx.dyn_seen, []
    if (len(ctx.found) == before and name in _DESTRUCTIVE_SUBS
            and any(w.dyn and not w.bare and w.text.startswith("-") for w in rest)):
        ctx.add("option-unresolved")   # e.g. `git reset --hard$IFS`: a flag we cannot read on a destructive-capable subcommand
    elif name in _DESTRUCTIVE_SUBS and not _read_only_form(name, rest):
        foreach = name == "submodule" and any(not w.dyn and w.text == "foreach" for w in rest)
        if flagged or (not ctx.settled and not foreach and any(_uncertain_position(w, seen_pos, ctx) for w, seen_pos in dyn_seen)):
            ctx.add("dynamic-argument")
    if name in ("checkout", "switch") and not dir_override:
        _track_branch(name, rest, ctx)


_DESTRUCTIVE_SUBS = frozenset({"reset", "clean", "push", "checkout", "restore", "branch", "gc", "reflog", "stash", "update-ref",
                               "tag", "worktree", "switch", "prune", "rm", "read-tree", "checkout-index", "submodule", "rebase",
                               "fetch", "pull"})
_EXPIRE_NOW = re.compile(r"^gc\.(prune|reflog)expire(unreachable)?\s*=\s*(now|all|0|0\.\w+)$", re.I)


def _gc_config(cfgs: List[str], ctx: _Ctx) -> None:
    """``git -c gc.pruneExpire=now gc`` is ``gc --prune=now``; gc.reflogExpire=now wipes the reflog."""
    for c in cfgs:
        m = _EXPIRE_NOW.match(c.strip())
        if m:
            ctx.add("gc-prune-now" if m.group(1).lower() == "prune" else "reflog-destroy")


_EXEC_CONFIG = re.compile(r"^(core\.(pager|editor|sshcommand|fsmonitor|hookspath|askpass|gitproxy|alternaterefscommand)|pager\..+|"
                          r"diff\.(external|[^.]+\.(textconv|command))|credential(\..+)?\.helper|sequence\.editor|gpg(\..+)?\.program|"
                          r"gpg\.ssh\.defaultkeycommand|merge\..+\.(driver|name)|filter\..+\.(clean|smudge|process)|uploadpack\.packobjectshook|"
                          r"remote\..+\.(uploadpack|receivepack|vcs|proxy)|sendemail\..+|difftool\..+\.cmd|mergetool\..+\.cmd|"
                          r"browser\..+\.cmd|man\..+\.cmd|instaweb\.browser|web\.browser|http\.sslcommand)$", re.I)
_SAFE_PAGERS = frozenset({"", "cat", "less", "more", "less -FRX", "less -R", "false", "true"})
ALWAYS_EXEC_SUBS = frozenset("""difftool mergetool instaweb daemon http-backend http-fetch http-push credential credential-cache
credential-gcloud credential-netrc credential-osxkeychain credential-store send-email imap-send gui gitk citool remote-ext
receive-pack upload-pack cvsserver""".split())
EXEC_ENV = frozenset("""GIT_EXTERNAL_DIFF GIT_SSH GIT_SSH_COMMAND GIT_PAGER GIT_EDITOR GIT_SEQUENCE_EDITOR GIT_ASKPASS SSH_ASKPASS
GIT_PROXY_COMMAND GIT_TEMPLATE_DIR GIT_EXEC_PATH""".split())
_SAFE_ENV_VALUES = frozenset({"", "cat", "less", "more", "true", "false", ":", "less -FRX", "less -R"})


def _exec_config(key: str, value: str) -> bool:
    """``key`` (``core.pager``, ``remote.o.uploadpack``, ...) makes Git run a program, and ``value`` is not a benign pager."""
    k = key.strip()
    if not _EXEC_CONFIG.match(k):
        return False
    if k.lower().startswith(("core.pager", "pager.")) and value.strip().strip("'\"") in _SAFE_PAGERS:
        return False
    return True


def _exec_option(name: str, rest: List[Word], ctx: "_Ctx") -> Optional[str]:
    """Execution-bearing option / subcommand: returns a short description or None."""
    if name in ALWAYS_EXEC_SUBS or name.startswith("credential-"):
        return f"git {name} launches helper programs"
    if name == "bundle":
        return None
    words = []
    for w in rest:
        if w.text == "--":
            break
        if not w.dyn or w.text.startswith("-"):
            words.append(w.text)
    for i, t in enumerate(words):
        if _prefix_opt(t, "ext-diff") or _prefix_opt(t, "textconv"):
            return t.split("=", 1)[0]
        if name in ("fetch", "pull", "push", "clone", "ls-remote", "archive", "submodule", "remote", "send-pack", "fetch-pack"):
            for full in ("upload-pack", "receive-pack", "exec", "remote"):
                if _prefix_opt(t, full, 3 if full != "remote" else 6) and not (full == "remote" and name != "archive"):
                    return t.split("=", 1)[0]
        if name == "clone":
            if t == "-u" or (t.startswith("-u") and not t.startswith("--")) or _prefix_opt(t, "template", 4):
                return t.split("=", 1)[0] if t.startswith("--") else "-u"
            if t in ("-c", "--config") or _prefix_opt(t, "config", 6):
                val = t.split("=", 1)[1] if "=" in t else (words[i + 1] if i + 1 < len(words) else "")
                if _exec_config(val.partition("=")[0], val.partition("=")[2]):
                    return "--config " + val.partition("=")[0]
    return None


def _env_exec(name: str, value: str, ctx: "_Ctx") -> None:
    """Environment assignment (prefix, standalone, ``export``, ``env X=..``) that makes a later Git run a program."""
    n = name.rstrip("+")
    if n in EXEC_ENV and value.strip().strip("'\"") not in _SAFE_ENV_VALUES:
        ctx.exec_env.add(n)
    elif n.upper().startswith("GIT_CONFIG_KEY_") and _EXEC_CONFIG.match(value.strip()):
        ctx.exec_env.add(n)
    elif n in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG") and _norm_path(value) in ctx.written:
        ctx.exec_env.add(n)


_OUTPUT_DIFFLIKE = frozenset("""diff log show whatchanged reflog diff-tree diff-index diff-files range-diff shortlog blame rev-list
format-patch archive stash""".split())


def _prefix_opt(t: str, full: str, minlen: int = 3) -> bool:
    """``t`` is ``--name[=value]`` where ``name`` is an unambiguous-looking abbreviation of ``full`` (Git accepts prefixes)."""
    if not t.startswith("--") or len(t) <= 2:
        return False
    n = t[2:].split("=", 1)[0]
    return len(n) >= minlen and full.startswith(n)


def _output_file_option(name: str, rest: List[Word]) -> Optional[str]:
    """First option on ``rest`` that makes ``git <name>`` write a file or run a helper (``--output=F`` on diff/log/show...,
    ``format-patch -o``, ``archive -o``, ``bundle create``, ``fast-export --export-marks``, ``grep -O``; ``fsck --lost-found`` only writes inside .git and is the guard's own suggested safe path, so it is not flagged).
    ``--output-indicator-*`` / ``--output-directory`` (outside format-patch) are different options and are not matched."""
    words = []
    for w in rest:
        if w.text == "--":
            break
        if not w.dyn or w.text.startswith("-"):
            words.append(w.text)
    if name == "bundle":
        return "create" if next((t for t in words if not t.startswith("-")), "") == "create" else None
    for t in words:
        if name in _OUTPUT_DIFFLIKE and name != "stash" and _prefix_opt(t, "output"):
            return t.split("=", 1)[0]
        if name == "format-patch" and (t == "-o" or (t.startswith("-o") and not t.startswith("--")) or _prefix_opt(t, "output-directory")):
            return t.split("=", 1)[0]
        if name == "archive" and (t == "-o" or (t.startswith("-o") and not t.startswith("--"))):
            return "-o"
        if name == "fast-export" and _prefix_opt(t, "export-marks"):
            return "--export-marks"
        if name == "grep" and (t.startswith("-O") or _prefix_opt(t, "open-files-in-pager")):
            return "--open-files-in-pager"
    return None


def _read_only_form(name: str, rest: List[Word]) -> bool:
    """Listing forms of otherwise destructive-capable subcommands (``git branch --list $X``, ``git stash list``)."""
    texts = [w.text for w in rest if not w.dyn]
    if name == "branch":
        return any(t in ("--list", "-l", "--show-current", "-a", "-r", "-v", "-vv", "--contains", "--merged", "--no-merged")
                   for t in texts) and not any(t in ("-d", "-D", "-f", "-m", "-M", "-c", "-C", "--delete", "--force", "--move", "--copy")
                                               for t in texts)
    if name in ("stash", "reflog", "worktree", "tag", "remote"):
        return bool(texts) and texts[0] in ("list", "show", "ls") or (name == "tag" and any(t in ("-l", "--list") for t in texts))
    return False


def _cand_label(cands) -> str:
    names = [c for c in cands if c]
    return "/".join(dict.fromkeys(names)) or "the current branch"


def _track_branch(sub: str, rest: List[Word], ctx: _Ctx) -> None:
    o = Opts(rest, ctx=ctx, short_arg={"b", "B", "c", "C"}, long_arg={"orphan", "conflict"})
    for k in ("b", "B", "c", "C"):
        if k in o.short_vals:
            ctx.cands = [o.short_vals[k]]
            return
    if o.dd or not o.pos or o.has_dyn:
        return
    if sub == "switch" or o.pos[0] not in (".",):
        ctx.cands = [o.pos[0]] + [c for c in ctx.cands]


def _h_reset(rest, ctx, cands):
    o = Opts(rest, ctx=ctx)
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
    o = Opts(rest, ctx=ctx, short_arg={"e"}, long_arg={"exclude"})
    dry = "n" in o.shorts or o.lopt("dry-run", 2)
    interactive = "i" in o.shorts or o.lopt("interactive", 3)
    if dry or interactive:
        ctx.settled = True               # -n / -i win over any force flag, whatever its position or origin
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
    o = Opts(rest, ctx=ctx, short_arg={"b", "B"}, long_arg={"orphan", "conflict", "pathspec-from-file"})
    _force_create(o, "B", ctx, cands)
    if "f" in o.shorts or o.lopt("force", 3):
        ctx.add("checkout-force")
        return
    if "p" in o.shorts or o.lopt("patch", 3):
        return
    if any(is_all_pathspec(p) for p in o.all_pos) and not ({"b", "B"} & set(o.short_vals)):
        ctx.add("checkout-discard-all")


def _h_switch(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"c", "C"}, long_arg={"conflict"})
    _force_create(o, "C", ctx, cands)
    if "f" in o.shorts or o.lopt("force", 3) or o.lopt("discard-changes", 2):
        ctx.add("switch-discard")


def _h_restore(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"s"}, long_arg={"source", "pathspec-from-file", "conflict"})
    staged = "S" in o.shorts or o.lopt("staged", 3)
    worktree = "W" in o.shorts or o.lopt("worktree", 3)
    if "p" in o.shorts or o.lopt("patch", 3) or (staged and not worktree):
        return
    if any(is_all_pathspec(p) for p in o.all_pos):
        ctx.add("checkout-discard-all")


def _h_reflog(rest, ctx, cands):
    o = Opts(rest, ctx=ctx)
    if o.pos and o.pos[0] in ("expire", "delete"):
        ctx.add("reflog-destroy")


def _h_gc(rest, ctx, cands):
    o = Opts(rest, ctx=ctx)
    for k, v in o.longs.items():
        if len(k) >= 3 and "prune".startswith(k) and v in ("now", "all"):
            ctx.add("gc-prune-now")
            return


def _h_prune(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg=set(), long_arg={"expire"})
    if not ("n" in o.shorts or o.lopt("dry-run", 3)):
        ctx.add("prune")


def _h_stash(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"m"}, long_arg={"message"})
    if o.pos and o.pos[0] == "clear":
        ctx.add("stash-clear")
    elif o.pos and o.pos[0] == "drop":
        ctx.add("stash-drop")


def _h_update_ref(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"m"}, long_arg={"message"})
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
    o = Opts(rest, ctx=ctx, short_arg={"o"}, long_arg={"push-option", "receive-pack", "exec", "repo"})
    force = "f" in o.shorts or any(k.startswith("force") or (len(k) >= 3 and "force".startswith(k) and not "follow-tags".startswith(k))
                                   for k in o.longs)
    mirror = o.lopt("mirror", 3)
    delete = "d" in o.shorts or o.lopt("delete", 3)
    all_ = o.lopt("all", 3) or "branches" in o.longs
    refspecs = o.all_pos[1:]
    if "n" in o.shorts or o.lopt("dry-run", 3):
        ctx.settled = True
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


def _fetch_refspecs(o: Opts, skip_repo: bool = True) -> List[str]:
    pos = o.all_pos
    return pos[1:] if skip_repo else pos


def _h_fetch(rest, ctx, cands, pull: bool = False):
    """Forced refspecs (`+src:dst`, --force/-f, --update-head-ok) whose destination is a LOCAL branch overwrite or rewind it.
    Plain `git fetch [remote [branch]]` only updates remote-tracking refs and is not Guardian's business."""
    o = Opts(rest, ctx=ctx, short_arg={"j", "o", "S"} if not pull else {"s", "X", "j", "S"},
             long_arg={"depth", "jobs", "filter", "upload-pack", "server-option", "shallow-since", "shallow-exclude", "deepen",
                       "negotiation-tip", "refmap", "recurse-submodules", "strategy", "strategy-option", "negotiate-only",
                       "submodule-prefix", "gpg-sign", "cleanup", "log"})
    flag_force = "f" in o.shorts or o.lopt("force", 3) or o.lopt("update-head-ok", 3) or (not pull and "u" in o.shorts)
    refspecs = _fetch_refspecs(o)
    if pull and flag_force and not any(":" in r for r in refspecs):
        ctx.add("fetch-force-local", branch="the current branch")   # `pull --force` may rewind the checked-out branch
        return
    for r in refspecs:
        plus = r.startswith("+")
        spec = r.lstrip("+")
        src, colon, dst = spec.partition(":")
        if not colon or not dst:
            continue
        if not (plus or flag_force):
            continue
        d = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
        if dst.startswith(("refs/remotes/", "refs/tags/", "refs/notes/")) or (dst.startswith("refs/") and not dst.startswith("refs/heads/")):
            continue                                     # remote-tracking / tags / notes: not a local branch
        if "$" in dst or "`" in dst:
            ctx.add("fetch-force-local", branch="an unknown branch")
            continue
        if "*" in d or "?" in d or "[" in d:
            if any(fnmatch.fnmatchcase(p, d) for p in ctx.cfg.protected_branches) or d in ("*",):
                ctx.add("fetch-force-protected", branch=d)
            else:
                ctx.add("fetch-force-local", branch=d)
            continue
        if ctx.protected(d):
            ctx.add("fetch-force-protected", branch=d)
        else:
            ctx.add("fetch-force-local", branch=d)


def _h_pull(rest, ctx, cands):
    _h_fetch(rest, ctx, cands, pull=True)


def _h_branch(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"u"}, long_arg={"set-upstream-to", "sort", "format", "contains", "no-contains",
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
    o = Opts(rest, ctx=ctx, short_arg={"m", "F", "C", "c", "t"},
             long_arg={"message", "file", "reuse-message", "reedit-message", "author", "date", "template", "cleanup",
                       "fixup", "squash", "trailer"})
    if o.lopt("amend", 2):
        ctx.add("commit-amend")


def _h_rebase(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"s", "X", "x"}, long_arg={"onto", "exec", "strategy", "strategy-option"})
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
    o = Opts(rest, ctx=ctx, short_arg={"m", "F", "u"}, long_arg={"message", "file", "local-user", "format", "sort", "contains",
                                                         "no-contains", "points-at", "merged", "no-merged", "cleanup"})
    if "d" in o.shorts or "f" in o.shorts or o.lopt("delete", 3) or o.lopt("force", 3):
        ctx.add("tag-rewrite")


def _h_worktree(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"b", "B"}, long_arg={"reason"})
    if o.pos and o.pos[0] == "remove" and ("f" in o.shorts or o.lopt("force", 3)):
        ctx.add("worktree-force-remove")


def _h_submodule(rest, ctx, cands):
    o = Opts(rest, ctx=ctx)
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
    o = Opts(rest, ctx=ctx)
    if "f" in o.shorts or o.lopt("force", 3):
        if "a" in o.shorts or o.lopt("all", 3) or any(is_all_pathspec(p) for p in o.all_pos):
            ctx.add("checkout-discard-all")      # overwrites every file, like `restore .`
        else:
            ctx.add("checkout-index-force")


def _h_read_tree(rest, ctx, cands):
    o = Opts(rest, ctx=ctx)
    if o.lopt("reset", 3):
        ctx.add("read-tree-reset" if "u" in o.shorts else "read-tree-reset-index")


def _h_git_rm(rest, ctx, cands):
    o = Opts(rest, ctx=ctx)
    if o.lopt("cached", 3) or o.lopt("dry-run", 3) or "n" in o.shorts:
        return
    if any(is_all_pathspec(p) for p in o.all_pos):
        force = "f" in o.shorts or o.lopt("force", 3)
        recursive = "r" in o.shorts or o.lopt("recursive", 3)
        ctx.add("rm-tree-ask" if (recursive and not force) else "rm-tree")


def _h_config(rest, ctx, cands):
    o = Opts(rest, ctx=ctx, short_arg={"f"}, long_arg={"file", "blob", "type", "default"})
    read = ("l" in o.shorts or any(k.startswith(("get", "list", "unset", "remove-section")) for k in o.longs)
            or (o.pos and o.pos[0] in ("get", "list", "unset")))
    if read:
        return
    if any(p.lower().startswith("alias.") for p in o.pos[:2]):
        ctx.add("alias-config")
    elif len(o.pos) >= 2 and _exec_config(o.pos[0], o.pos[1]):
        ctx.add("config-exec", tool=o.pos[0][:60])


def _h_remote(rest, ctx, cands):
    o = Opts(rest, ctx=ctx)
    if o.pos and o.pos[0] in ("set-url", "remove", "rm"):
        ctx.add("remote-modify")


_HANDLERS = {
    "reset": _h_reset, "clean": _h_clean, "checkout": _h_checkout, "switch": _h_switch, "restore": _h_restore,
    "reflog": _h_reflog, "gc": _h_gc, "prune": _h_prune, "stash": _h_stash, "update-ref": _h_update_ref,
    "push": _h_push, "branch": _h_branch, "commit": _h_commit, "rebase": _h_rebase, "tag": _h_tag,
    "worktree": _h_worktree, "submodule": _h_submodule, "config": _h_config, "remote": _h_remote,
    "fetch": _h_fetch, "pull": _h_pull, "bisect": _h_bisect, "checkout-index": _h_checkout_index, "read-tree": _h_read_tree, "rm": _h_git_rm,
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
            _env_exec(t.partition("=")[0], t.partition("=")[2], ctx)
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
            _env_exec(rest[0].text.partition("=")[0], rest[0].text.partition("=")[2], ctx)
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
            _record_assign(words.pop(0), ctx)
        elif _ASSIGN.match(t) and _var_assign_dyn(words[0]):
            _record_assign(words.pop(0), ctx)
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
        st = ctx.var_state(_var_name(head)) if _var_name(head) else None
        if isinstance(st, str) and st.strip():
            # `cmd="git reset --hard"; $cmd`: the literal value is visible in this same string; read it (never run it)
            try:
                vcmds = parse_script(st, ctx.budget, depth + 1)
            except ShellParseError:
                vcmds = []
            if vcmds and vcmds[0].words and not any(w.dyn for w in vcmds[0].words):
                if _mentions_git(vcmds[0].words):
                    ctx.add("unresolved-command")          # a variable used as the executable is never a confident safe
                _words(list(vcmds[0].words) + words[1:], cmd, ctx, depth)
                return
        elif st is _TAINT and ctx.mentions_git:
            ctx.add("unresolved-command")
        if head.subs and not all(_benign_cmds(c) for c in head.subs) and any(
                "git" in w.text.lower() for c in head.subs for cc in c for w in cc.words[:1] + cc.words[1:]):
            ctx.add("unresolved-command")
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
    if ctx.written and (_norm_path(head.text) in ctx.written or (
            (name in SHELLS or name in ("source", ".", "exec", "eval") or _INTERP.match(name))
            and any(_norm_path(w.text) in ctx.written for w in args if not w.text.startswith("-")))):
        ctx.add("generated-script")                 # written earlier in this command line and now executed
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
    elif name in ("source", "."):
        _source(args, cmd, ctx, depth)
    elif name in ("export", "declare", "typeset", "local", "readonly"):
        for w in args:
            if _ASSIGN.match(w.text):
                _record_assign(w, ctx)
    elif name in ("read", "mapfile", "readarray", "getopts", "for", "select"):
        for w in (args[:1] if name in ("for", "select") else args):
            if not w.text.startswith("-") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", w.text):
                ctx.vars.setdefault(w.text, []).append(_UNKNOWN)
    elif name == "tee":
        for w in args:
            if not w.text.startswith("-"):
                ctx.written.add(_norm_path(w.text))
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


def _record_assign(w: Word, ctx: _Ctx) -> None:
    name, _, value = w.text.partition("=")
    append = name.endswith("+")
    name = name.rstrip("+")
    # the tokenizer keeps the whole ``NAME=value`` as one word; re-derive the value word's dynamism
    vw = Word(value, dyn=w.dyn, bare=False, quoted=w.quoted, subs=w.subs, varexp=w.varexp)
    if w.dyn and _VARNAME.match(value):
        vw.bare = True
    ctx.record_var(name, vw, append)
    _env_exec(name, value if not w.dyn else "$dynamic", ctx)


def _var_assign_dyn(w: Word) -> bool:
    """``NAME=$(...)`` / ``NAME=$X`` -- an assignment whose value is dynamic (the word carries the whole text)."""
    return w.dyn and bool(_ASSIGN.match(w.text))


def _source(args: List[Word], cmd: Optional[Cmd], ctx: _Ctx, depth: int) -> None:
    """``source FILE`` / ``. FILE``: a literal project file is out of scope (defer); stdin / process substitution /
    a file generated in the same command are not."""
    op = next((w for w in args if not (w.text.startswith("-") and len(w.text) > 1 and not w.dyn)), None)
    if op is None:
        return
    if not op.dyn and _norm_path(op.text) in ctx.written:
        return                                        # already reported as generated-script
    if (not op.dyn and (op.text in STDIN_PATHS or op.text.startswith(("/dev/fd/", "/proc/self/fd/")))) or op.text == "<(...)":
        texts = _stdin_scripts(cmd)
        for text in texts:
            _script(text, ctx, depth + 1)
        if not texts and ctx.mentions_git:
            ctx.add("stdin-script")
    elif op.dyn and ctx.mentions_git and (_risky_word(op, ctx) or op.subs):
        ctx.add("stdin-script")


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
    operand = args[i] if i < n else None
    from_stdin = operand is None or (not operand.dyn and operand.text in STDIN_PATHS)
    if cmd is not None and operand is None and any(_norm_path(f) in ctx.written for f in cmd.in_redirs):
        ctx.add("generated-script")                 # `bash < gen.sh` after writing gen.sh
    if from_stdin:  # script comes from stdin
        texts = _stdin_scripts(cmd)
        for text in texts:
            _script(text, ctx, depth + 1)
        if operand is not None and not texts and ctx.mentions_git:
            ctx.add("stdin-script")
    elif operand is not None and operand.dyn and ctx.mentions_git and (
            operand.text == "<(...)" or ctx.var_state(_var_name(operand) or "") is _TAINT or operand.subs):
        ctx.add("stdin-script")                     # `bash <(echo '...git...')`: script text is computed


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
        for target in cmd.redirs:
            if target and not target.startswith("&") and target not in ("/dev/null", "1", "2"):
                ctx.written.add(_norm_path(target))


# --------------------------------------------------------------------------- public API
def classify_command(command: str, cfg: Optional[Config] = None, branch: Optional[str] = None) -> Verdict:
    """Classify a shell command string. Pure; never executes anything."""
    cfg = cfg or Config()
    if not isinstance(command, str):
        return Verdict("defer")
    ctx = _Ctx(cfg, branch)
    text = command.replace("\x00", " ")
    mentions_git = ctx.mentions_git = "git" in text.lower()
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
        return Verdict("defer", "defer", "", "", [], ctx.commands[:20])
    ctx.found.sort(key=lambda f: (-f[0], f[1]))
    best = ctx.found[0][2]
    best.commands = ctx.commands[:20]
    return best
