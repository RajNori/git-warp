"""A deliberately small shell lexer for safety triage, not a Bash parser.

It honors common single/double quoting and backslash escapes, and recognizes
command-list, pipeline, and grouping operators. Expansion, heredocs, process
substitution, and shell grammar are not evaluated. Callers must treat an
ambiguous Git invocation as requiring human review.
"""

from __future__ import annotations

from dataclasses import dataclass
import shlex


MAX_COMMAND_LENGTH = 16_384
MAX_TOKENS = 512
OPERATORS = {";", "&&", "||", "|", "&", "(", ")", "\n"}


@dataclass(frozen=True)
class LexedCommand:
    segments: tuple[tuple[str, ...], ...]
    ambiguous: bool = False
    error: str | None = None


def lex_command(command: str) -> LexedCommand:
    """Split common shell command lists while preserving quoted operators.

    Unsupported or over-budget input is reported as ambiguous; it is never
    interpreted as safe. This does not expand variables or execute shell text.
    """
    if len(command) > MAX_COMMAND_LENGTH:
        return LexedCommand((), True, "command exceeds parser length limit")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace_split = True
    # Make newline an operator instead of silently merging separate commands.
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    ambiguous = False
    count = 0
    try:
        for token in lexer:
            count += 1
            if count > MAX_TOKENS:
                return LexedCommand(tuple(segments), True, "command exceeds parser token limit")
            # shlex emits runs (for example `&&` or `;`) as punctuation tokens.
            if token and all(ch in ";&|()\n" for ch in token):
                i = 0
                while i < len(token):
                    if token.startswith("&&", i) or token.startswith("||", i):
                        op = token[i:i + 2]
                        i += 2
                    else:
                        op = token[i]
                        i += 1
                    if op in {";", "&&", "||", "|", "&", "\n"}:
                        if current:
                            segments.append(tuple(current))
                            current = []
                    else:
                        # Parentheses change shell execution context. The inner
                        # commands are still segmented, but require review if Git
                        # appears in a context we cannot model.
                        ambiguous = True
                continue
            current.append(token)
            # Command substitutions and parameter expansions can synthesize an
            # executable name or shell fragment without our seeing its value.
            if "$(" in token or "${" in token or token.startswith("$"):
                ambiguous = True
            if "`" in token:
                ambiguous = True
        if current:
            segments.append(tuple(current))
    except ValueError as exc:
        return LexedCommand(tuple(segments), True, f"shell tokenization failed: {exc}")
    return LexedCommand(tuple(segments), ambiguous)
