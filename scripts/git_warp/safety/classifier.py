"""Pure Git command classifier. It does not execute Git or grant permission."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
import shlex
from typing import Iterable

from .shell_lexer import LexedCommand, lex_command


class Decision(str, Enum):
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class Classification:
    decision: Decision
    reason_code: str
    message: str


@dataclass(frozen=True)
class SafetyContext:
    """Policy inputs. Branch protection is based on the push destination ref."""

    protected_branches: frozenset[str] = frozenset(
        {"main", "master", "develop", "development", "production", "prod", "release"}
    )


_BLOCK = "Git Warp blocked this destructive Git operation: "
_ASK = "Git Warp requires review before this Git operation: "
_RECOGNIZED_SUBCOMMANDS = frozenset(
    "add am annotate apply archive bisect blame branch bundle cat-file check-attr "
    "check-ignore check-mailmap check-ref-format checkout cherry cherry-pick citool "
    "clean clone commit config count-objects credential describe diff difftool "
    "fast-export fast-import fetch format-patch fsck gc get-tar-commit-id grep gui "
    "hash-object help init instaweb log maintenance merge mergetool mv notes pack-objects "
    "pack-redundant pack-refs patch-id prune pull push range-diff rebase reflog "
    "remote repack replace request-pull reset restore revert rm send-email shortlog "
    "show show-branch sparse-checkout stash status submodule switch symbolic-ref tag "
    "verify-commit verify-pack verify-tag worktree".split()
)


def _result(decision: Decision, code: str, message: str) -> Classification:
    return Classification(decision, code, message)


def _base(token: str) -> str:
    return os.path.basename(token).lower()


def _contains_git_word(tokens: Iterable[str]) -> bool:
    return any(_base(token) in {"git", "git.exe"} for token in tokens)


def _unwrap(tokens: tuple[str, ...], depth: int = 0) -> tuple[tuple[str, ...] | None, bool]:
    """Return Git argv (without executable), or (None, possible/ambiguous Git)."""
    if not tokens or depth > 3:
        return None, False
    index = 0
    wrappers = {"command", "builtin", "exec", "nohup", "time", "nice", "sudo"}
    while index < len(tokens):
        exe = _base(tokens[index])
        if exe in {"git", "git.exe"}:
            return tokens[index + 1:], False
        if exe in wrappers:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                opt = tokens[index]
                index += 1
                if exe == "sudo" and opt in {"-u", "-g", "-h", "-p", "-C", "-D"} and index < len(tokens):
                    index += 1
            continue
        if exe == "env":
            index += 1
            while index < len(tokens):
                value = tokens[index]
                if value == "--":
                    index += 1
                    break
                if value in {"-i", "--ignore-environment"}:
                    index += 1
                elif value in {"-u", "--unset"}:
                    index += 2
                elif value.startswith("--unset=") or (value.startswith("-u") and len(value) > 2):
                    index += 1
                elif "=" in value and not value.startswith("="):
                    index += 1
                elif value == "-S" or value == "--split-string":
                    # GNU env -S uses its own quoting/splitting rules. Re-lex a
                    # single straightforward payload; uncertain forms are ASK.
                    if index + 1 >= len(tokens):
                        return None, True
                    try:
                        nested = tuple(shlex.split(tokens[index + 1], posix=True))
                    except ValueError:
                        return None, True
                    return _unwrap(nested, depth + 1)
                else:
                    break
            continue
        if exe in {"sh", "bash", "zsh", "dash", "ksh"}:
            # Explicit shell -c payloads are recursively inspected. Other shell
            # invocation modes that contain Git are review-required.
            if index + 2 < len(tokens) and tokens[index + 1] in {"-c", "-lc", "-cl"}:
                payload = tokens[index + 2]
                nested_lex = lex_command(payload)
                if nested_lex.error or nested_lex.ambiguous or len(nested_lex.segments) != 1:
                    return None, bool(re.search(r"\bgit(?:\.exe)?\b", payload, re.I))
                found, ambiguous = _unwrap(nested_lex.segments[0], depth + 1)
                if found is not None or ambiguous:
                    return found, ambiguous
            return None, _contains_git_word(tokens[index + 1:])
        # A command we do not understand may be a Git wrapper if Git appears in
        # its arguments. Avoid treating benign prose such as `echo git` as Git.
        if exe in {"echo", "printf", "true", "false", "pwd"}:
            return None, False
        if _contains_git_word(tokens[index + 1:]):
            return None, True
        return None, False
    return None, False


def _git_subcommand(args: tuple[str, ...]) -> tuple[str | None, tuple[str, ...], bool]:
    i = 0
    takes_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
    while i < len(args):
        token = args[i]
        if token in takes_value:
            i += 2
            continue
        if any(token.startswith(opt + "=") for opt in {"--git-dir", "--work-tree", "--namespace", "--config-env"}):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token.lower(), args[i + 1:], False
    return None, (), bool(args)


def _options_and_positionals(args: tuple[str, ...]) -> tuple[set[str], list[str]]:
    options: set[str] = set()
    positional: list[str] = []
    i = 0
    after_double_dash = False
    while i < len(args):
        token = args[i]
        if token == "--":
            after_double_dash = True
            i += 1
            continue
        if not after_double_dash and token.startswith("-"):
            options.add(token)
            i += 1
            continue
        positional.append(token)
        i += 1
    return options, positional


def _protected_destination(refspec: str, context: SafetyContext) -> str | None:
    # '+' forces an update. For a one-sided refspec Git uses the same name on
    # the destination, so that target is provable. HEAD and wildcard refs are
    # intentionally not guessed unless their destination is explicit.
    destination = refspec.lstrip("+").split(":", 1)[-1] if ":" in refspec else refspec.lstrip("+")
    if not destination or destination == "HEAD" or any(c in destination for c in "*?["):
        return None
    if destination.startswith("refs/heads/"):
        destination = destination[len("refs/heads/"):]
    if destination in context.protected_branches:
        return destination
    return None


def classify_tokens(tokens: Iterable[str], context: SafetyContext | None = None) -> Classification | None:
    """Classify a single tokenized segment. ``None`` defers to Claude policy."""
    context = context or SafetyContext()
    segment = tuple(tokens)
    git_args, ambiguous = _unwrap(segment)
    if git_args is None:
        if ambiguous:
            return _result(Decision.ASK, "ambiguous_git_invocation", _ASK + "the command may invoke Git through a wrapper or shell construct.")
        return None

    subcommand, args, malformed = _git_subcommand(git_args)
    if malformed:
        return _result(Decision.ASK, "ambiguous_git_command", _ASK + "the Git command could not be parsed safely.")
    if subcommand is None:
        return None
    if subcommand not in _RECOGNIZED_SUBCOMMANDS:
        # Git aliases can execute shell commands via `!` in repository/user
        # config. Treat unknown subcommands as aliases until proven otherwise.
        return _result(Decision.ASK, "unknown_git_subcommand", _ASK + "the Git subcommand is unrecognized and could be a configured alias.")
    opts, positional = _options_and_positionals(args)

    if subcommand == "reset" and ("--hard" in opts or any(re.fullmatch(r"-[a-zA-Z]*H[a-zA-Z]*", o) for o in opts)):
        return _result(Decision.DENY, "reset_hard", _BLOCK + "`git reset --hard` can discard working-tree changes.")
    if subcommand == "clean":
        force = "--force" in opts or any(o.startswith("-") and not o.startswith("--") and "f" in o[1:] for o in opts)
        if force:
            return _result(Decision.DENY, "clean_force", _BLOCK + "forced `git clean` can permanently remove untracked files.")
    if subcommand == "checkout" and "--" in args and positional:
        return _result(Decision.DENY, "checkout_paths", _BLOCK + "`git checkout` with pathspecs overwrites working-tree files.")
    if subcommand == "restore":
        has_worktree = not ("--staged" in opts or "-S" in opts) or "--worktree" in opts or "-W" in opts
        if positional and has_worktree:
            return _result(Decision.DENY, "restore_paths", _BLOCK + "`git restore` pathspecs overwrite working-tree files.")
    if subcommand == "reflog" and args and args[0].lower() == "expire":
        return _result(Decision.DENY, "reflog_expire", _BLOCK + "expiring reflogs removes recovery history.")
    if subcommand == "gc" and any(o == "--prune" or o.startswith("--prune=") for o in opts):
        return _result(Decision.DENY, "gc_prune", _BLOCK + "pruning Git objects can remove recoverable history.")

    if subcommand == "push":
        force = any(o in {"-f", "--force", "--force-with-lease"} or o.startswith("--force-with-lease=") for o in opts)
        # A leading '+' on a refspec is also an explicit forced update.
        if positional and (":" in positional[0] or positional[0].startswith("+")):
            force = force or positional[0].startswith("+")
        if force:
            # Remote is the first positional, refspecs follow. No repository
            # state is consulted; if destination is not explicit, review it.
            refspecs = positional if positional and (":" in positional[0] or positional[0].startswith("+")) else positional[1:]
            destinations = [_protected_destination(ref, context) for ref in refspecs]
            protected = next((ref for ref in destinations if ref), None)
            if protected:
                return _result(Decision.DENY, "force_push_protected", _BLOCK + f"a force push targets protected branch `{protected}`.")
            return _result(Decision.ASK, "force_push", _ASK + "force-push destination or history safety needs review.")
        if "--delete" in opts or "-d" in opts:
            return _result(Decision.ASK, "remote_branch_delete", _ASK + "remote branch deletion needs review.")
    if subcommand == "branch" and ("-D" in opts or "--delete-force" in opts):
        return _result(Decision.ASK, "forced_branch_delete", _ASK + "forced local branch deletion needs review.")
    if subcommand == "rebase":
        return _result(Decision.ASK, "rebase", _ASK + "rebase rewrites commit history.")
    if subcommand == "commit" and "--amend" in opts:
        return _result(Decision.ASK, "commit_amend", _ASK + "amending rewrites the previous commit.")
    return None


def classify_command(command: str, context: SafetyContext | None = None) -> Classification | None:
    """Classify a shell command string without execution or shell interpolation.

    A clear destructive operation wins over all other segments. Any operation
    requiring review, or unparseable text that may contain Git, yields ASK.
    Unrelated/safe commands return None so Claude's normal permissions apply.
    """
    lexed: LexedCommand = lex_command(command)
    if lexed.error:
        if re.search(r"\bgit(?:\.exe)?\b", command, re.I):
            return _result(Decision.ASK, "ambiguous_git_invocation", _ASK + "the shell command exceeded parser limits or could not be tokenized.")
        return None
    results = [classify_tokens(segment, context) for segment in lexed.segments]
    if lexed.ambiguous and (
        re.search(r"\bgit(?:\.exe)?\b", command, re.I)
        or any(segment and segment[0].startswith("$") for segment in lexed.segments)
    ):
        results.append(_result(Decision.ASK, "ambiguous_git_invocation", _ASK + "shell expansion or grouping prevents reliable classification."))
    for result in results:
        if result and result.decision is Decision.DENY:
            return result
    return next((result for result in results if result), None)
