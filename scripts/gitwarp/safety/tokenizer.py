"""A small POSIX-ish shell *tokenizer* used only to find command words.

It never executes anything.  It understands enough shell to answer "which simple
commands would this string run, and with which words?":

* single / double quotes, backslash escapes, ``$'..'`` (ANSI-C) quoting
* separators ``; & && || | |& newline``, subshells ``( )``, groups ``{ }``
* command substitution ``$( )`` and backticks (parsed recursively, hoisted)
* redirections (ignored), here-docs (bodies skipped, kept for ``bash <<EOF``),
  here-strings, comments
* process substitution ``<( )`` / ``>( )``

Words that contain an unresolved expansion (``$VAR``, ``$(..)``) are marked
``dyn``; words with unquoted glob / brace characters are marked ``glob``.
Anything pathological (nesting too deep, too many steps) raises
:class:`ShellParseError` so callers can fail safe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAX_DEPTH = 24
MAX_STEPS = 400_000


class ShellParseError(Exception):
    """Input too deeply nested / too large to analyse safely."""


@dataclass
class Word:
    text: str
    dyn: bool = False      # contains an unresolved expansion
    glob: bool = False     # unquoted glob / brace characters
    bare: bool = False     # exactly one plain variable expansion ($X, "${X}")
    quoted: bool = False   # any quoting/escaping was used


@dataclass(eq=False)
class Cmd:
    words: List[Word] = field(default_factory=list)
    heredocs: List[str] = field(default_factory=list)
    herestrings: List[str] = field(default_factory=list)
    pipe_prev: Optional["Cmd"] = None

    def texts(self) -> List[str]:
        return [w.text for w in self.words]


class Budget:
    __slots__ = ("n",)

    def __init__(self) -> None:
        self.n = 0


class _W:
    __slots__ = ("buf", "dyn", "glob", "quoted", "n_exp", "lit", "cmdsub", "brace")

    def __init__(self) -> None:
        self.buf: List[str] = []
        self.dyn = self.glob = self.quoted = self.cmdsub = self.brace = False
        self.n_exp = 0
        self.lit = False

    def add(self, text: str) -> None:
        self.buf.append(text)
        self.lit = True

    def word(self) -> Word:
        text = "".join(self.buf)
        glob = self.glob or (self.brace and text not in ("{", "}") and len(text) > 1)
        bare = self.dyn and self.n_exp == 1 and not self.lit and not self.cmdsub
        return Word(text, dyn=self.dyn, glob=glob, bare=bare, quoted=self.quoted)


_WS = " \t\r"
_BREAK = " \t\r\n;&|<>)"
_NAME_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
_NAME_CHARS = _NAME_START + "0123456789"
_SPECIAL_VARS = "@*#?$!-0123456789"
_ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b",
         "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _hexval(s: str) -> Optional[int]:
    try:
        return int(s, 16)
    except ValueError:
        return None


class Parser:
    def __init__(self, text: str, budget: Optional[Budget] = None) -> None:
        self.s = text
        self.n = len(text)
        self.b = budget or Budget()

    # ------------------------------------------------------------------ util
    def tick(self, k: int = 1) -> None:
        self.b.n += k
        if self.b.n > MAX_STEPS:
            raise ShellParseError("input too large to analyse")

    # --------------------------------------------------------------- quoting
    def _ansi_c(self, w: _W, i: int) -> int:
        """``i`` points just after ``$'``.  Decode until the closing quote."""
        s, n = self.s, self.n
        out: List[str] = []
        while i < n:
            self.tick()
            c = s[i]
            if c == "'":
                i += 1
                break
            if c == "\\" and i + 1 < n:
                d = s[i + 1]
                if d in _ANSI:
                    out.append(_ANSI[d])
                    i += 2
                elif d == "x":
                    j = i + 2
                    while j < n and j < i + 4 and s[j] in "0123456789abcdefABCDEF":
                        j += 1
                    v = _hexval(s[i + 2:j]) if j > i + 2 else None
                    out.append(chr(v) if v is not None and v else "")
                    i = j if v is not None else i + 2
                elif d in "01234567":
                    j = i + 1
                    while j < n and j < i + 4 and s[j] in "01234567":
                        j += 1
                    v = int(s[i + 1:j], 8)
                    out.append(chr(v) if v else "")
                    i = j
                elif d in "uU":
                    size = 4 if d == "u" else 8
                    j = i + 2
                    while j < n and j < i + 2 + size and s[j] in "0123456789abcdefABCDEF":
                        j += 1
                    v = _hexval(s[i + 2:j]) if j > i + 2 else None
                    try:
                        out.append(chr(v) if v else "")
                    except (ValueError, OverflowError):
                        out.append("")
                    i = j
                else:
                    out.append("\\" + d)
                    i += 2
            else:
                out.append(c)
                i += 1
        w.quoted = True
        w.add("".join(out))
        return i

    def _backtick(self, w: _W, i: int, depth: int, out: List[Cmd]) -> int:
        """``i`` points at the opening backtick."""
        s, n = self.s, self.n
        j = i + 1
        while j < n and s[j] != "`":
            self.tick()
            j += 2 if s[j] == "\\" else 1
        body = s[i + 1:min(j, n)]
        body = body.replace("\\`", "`").replace("\\\\", "\\").replace("\\$", "$")
        cmds, _ = Parser(body, self.b).parse_seq(0, None, depth + 1)
        out.extend(cmds)
        w.dyn = True
        w.cmdsub = True
        w.n_exp += 1
        w.buf.append("`...`")
        return min(j + 1, n)

    def _dollar(self, w: _W, i: int, depth: int, out: List[Cmd], in_dq: bool) -> int:
        s, n = self.s, self.n
        nxt = s[i + 1] if i + 1 < n else ""
        if nxt == "(":
            cmds, j = self.parse_seq(i + 2, ")", depth + 1)
            out.extend(cmds)
            w.dyn = w.cmdsub = True
            w.n_exp += 1
            w.buf.append("$(...)")
            return j
        if nxt == "{":
            j = i + 2
            d = 1
            while j < n and d:
                self.tick()
                ch = s[j]
                if ch == "\\":
                    j += 2
                elif ch == "'" and not in_dq:
                    k = s.find("'", j + 1)
                    j = n if k < 0 else k + 1
                elif s.startswith("$(", j):
                    cmds, j = self.parse_seq(j + 2, ")", depth + 1)
                    out.extend(cmds)
                elif s.startswith("${", j):
                    d += 1
                    j += 2
                elif ch == "}":
                    d -= 1
                    j += 1
                else:
                    j += 1
            w.dyn = True
            w.n_exp += 1
            w.buf.append(s[i:min(j, n)])
            return min(j, n)
        if nxt == "'" and not in_dq:
            return self._ansi_c(w, i + 2)
        if nxt == '"' and not in_dq:
            w.quoted = True
            return self._dq(w, i + 2, depth, out, True)
        if nxt and nxt in _NAME_START:
            j = i + 1
            while j < n and s[j] in _NAME_CHARS:
                j += 1
            w.dyn = True
            w.n_exp += 1
            w.buf.append(s[i:j])
            return j
        if nxt and nxt in _SPECIAL_VARS:
            w.dyn = True
            w.n_exp += 1
            w.buf.append(s[i:i + 2])
            return i + 2
        w.add("$")
        return i + 1

    def _dq(self, w: _W, i: int, depth: int, out: List[Cmd], until_quote: bool = True) -> int:
        s, n = self.s, self.n
        w.quoted = True
        while i < n:
            self.tick()
            c = s[i]
            if until_quote and c == '"':
                return i + 1
            if c == "\\" and i + 1 < n:
                d = s[i + 1]
                if d in '$`"\\':
                    w.add(d)
                    i += 2
                elif d == "\n":
                    i += 2
                else:
                    w.add("\\")
                    i += 1
            elif c == "$":
                i = self._dollar(w, i, depth, out, True)
            elif c == "`":
                i = self._backtick(w, i, depth, out)
            else:
                w.add(c)
                i += 1
        return n

    # ----------------------------------------------------------------- words
    def read_word(self, i: int, depth: int, out: List[Cmd]) -> Tuple[Word, int]:
        s, n = self.s, self.n
        w = _W()
        while i < n:
            self.tick()
            c = s[i]
            if c in _BREAK:
                break
            if c == "(":
                if w.buf and w.buf[-1] and w.buf[-1][-1] in "?*+@!":
                    d, j = 0, i
                    while j < n:
                        self.tick()
                        if s[j] == "(":
                            d += 1
                        elif s[j] == ")":
                            d -= 1
                            if d == 0:
                                j += 1
                                break
                        j += 1
                    w.add(s[i:j])
                    w.glob = True
                    i = j
                    continue
                break
            if c == "\\":
                if i + 1 < n:
                    if s[i + 1] == "\n":
                        i += 2
                        continue
                    w.quoted = True
                    w.add(s[i + 1])
                    i += 2
                else:
                    i += 1
            elif c == "'":
                k = s.find("'", i + 1)
                end = n if k < 0 else k
                w.quoted = True
                w.add(s[i + 1:end])
                i = min(end + 1, n)
            elif c == '"':
                i = self._dq(w, i + 1, depth, out)
            elif c == "$":
                i = self._dollar(w, i, depth, out, False)
            elif c == "`":
                i = self._backtick(w, i, depth, out)
            else:
                if c in "*?[":
                    w.glob = True
                elif c == "{":
                    w.brace = True
                w.add(c)
                i += 1
        return w.word(), i

    # -------------------------------------------------------------- sequence
    def _heredoc_bodies(self, pending: list, i: int, depth: int, out: List[Cmd]) -> int:
        """``i`` is the index just after the newline. Reads bodies, returns new index."""
        s, n = self.s, self.n
        for cmd, delim, quoted, strip in pending:
            lines: List[str] = []
            while i < n:
                self.tick()
                e = s.find("\n", i)
                e = n if e < 0 else e
                line = s[i:e]
                i = e + 1
                if (line.lstrip("\t") if strip else line) == delim:
                    break
                lines.append(line)
            body = "\n".join(lines)
            cmd.heredocs.append(body)
            if not quoted and ("$" in body or "`" in body):
                sub = Parser(body, self.b)
                sub._dq(_W(), 0, depth + 1, out, until_quote=False)
        return min(i, n)

    def parse_seq(self, i: int, closer: Optional[str], depth: int) -> Tuple[List[Cmd], int]:
        if depth > MAX_DEPTH:
            raise ShellParseError("nesting too deep")
        s, n = self.s, self.n
        out: List[Cmd] = []
        cur = Cmd()
        pending: list = []
        mode: Optional[str] = None   # None | skip | herestring | heredoc
        heredoc_strip = False

        def end_cmd(pipe: bool = False) -> None:
            nonlocal cur
            last = cur if cur.words else None
            if last is not None:
                out.append(last)
            cur = Cmd(pipe_prev=last if pipe else None)

        while i < n:
            self.tick()
            c = s[i]
            if c in _WS:
                i += 1
                continue
            if c == "\n":
                end_cmd()
                if pending:
                    i = self._heredoc_bodies(pending, i + 1, depth, out)
                    pending = []
                else:
                    i += 1
                mode = None
                continue
            if c == "#":
                j = s.find("\n", i)
                i = n if j < 0 else j
                continue
            if c == ")":
                end_cmd()
                if closer == ")":
                    return out, i + 1
                i += 1
                continue
            if c == "(":
                end_cmd()
                cmds, i = self.parse_seq(i + 1, ")", depth + 1)
                out.extend(cmds)
                continue
            if c == ";":
                end_cmd()
                i += 2 if i + 1 < n and s[i + 1] in ";&" else 1
                mode = None
                continue
            if c == "&":
                nxt = s[i + 1] if i + 1 < n else ""
                if nxt == ">":
                    i += 3 if s.startswith("&>>", i) else 2
                    mode = "skip"
                    continue
                end_cmd()
                i += 2 if nxt == "&" else 1
                mode = None
                continue
            if c == "|":
                nxt = s[i + 1] if i + 1 < n else ""
                if nxt == "|":
                    end_cmd()
                    i += 2
                else:
                    end_cmd(pipe=True)
                    i += 2 if nxt == "&" else 1
                mode = None
                continue
            if c in "<>":
                nxt = s[i + 1] if i + 1 < n else ""
                if nxt == "(":
                    cmds, i = self.parse_seq(i + 2, ")", depth + 1)
                    out.extend(cmds)
                    cur.words.append(Word("<(...)", dyn=True))
                    continue
                if s.startswith("<<<", i):
                    mode, i = "herestring", i + 3
                elif s.startswith("<<-", i):
                    mode, i, heredoc_strip = "heredoc", i + 3, True
                elif s.startswith("<<", i):
                    mode, i, heredoc_strip = "heredoc", i + 2, False
                elif s.startswith(">>", i) or s.startswith(">|", i) or s.startswith(">&", i) \
                        or s.startswith("<&", i) or s.startswith("<>", i):
                    mode, i = "skip", i + 2
                else:
                    mode, i = "skip", i + 1
                continue
            # an ordinary word
            w, i = self.read_word(i, depth, out)
            if not w.text and not w.quoted and not w.dyn:
                continue  # e.g. a bare line continuation
            if i < n and s[i] in "<>" and w.text.isdigit() and not w.dyn and mode is None:
                continue  # fd prefix such as 2>
            if mode == "skip":
                mode = None
            elif mode == "herestring":
                cur.herestrings.append(w.text)
                mode = None
            elif mode == "heredoc":
                pending.append((cur, w.text, w.quoted, heredoc_strip))
                mode = None
            else:
                cur.words.append(w)
        end_cmd()
        return out, n


def parse_script(text: str, budget: Optional[Budget] = None, depth: int = 0) -> List[Cmd]:
    """Parse ``text`` into the flat list of simple commands it would run (nested ones hoisted)."""
    cmds, _ = Parser(text, budget).parse_seq(0, None, depth)
    return cmds
