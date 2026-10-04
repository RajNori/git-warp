"""Unit tests for the shell tokenizer."""
import pytest

from gitwarp.safety.tokenizer import MAX_DEPTH, ShellParseError, parse_script


def texts(s):
    return [c.texts() for c in parse_script(s)]


@pytest.mark.parametrize("src,expected", [
    ("a b c", [["a", "b", "c"]]),
    ("a 'b c' \"d e\"", [["a", "b c", "d e"]]),
    ("a\\ b", [["a b"]]),
    ("a;b", [["a"], ["b"]]),
    ("a && b || c", [["a"], ["b"], ["c"]]),
    ("a | b |& c", [["a"], ["b"], ["c"]]),
    ("a & b", [["a"], ["b"]]),
    ("a\nb", [["a"], ["b"]]),
    ("a # comment\nb", [["a"], ["b"]]),
    ("a#b", [["a#b"]]),
    ("echo 'it'\"'\"'s'", [["echo", "it's"]]),
    ("echo $'a\\tb'", [["echo", "a\tb"]]),
    ("echo $'\\x67\\151t'", [["echo", "git"]]),
    ("echo $'\\u0067'", [["echo", "g"]]),
    ("echo \"a\\\"b\"", [["echo", 'a"b']]),
    ("echo \"a\\nb\"", [["echo", "a\\nb"]]),
    ("a\\\nb", [["ab"]]),
    ("a > f b", [["a", "b"]]),
    ("a 2>&1 b", [["a", "b"]]),
    ("a >> f", [["a"]]),
    ("a &> f", [["a"]]),
    ("a <<< hi", [["a"]]),
    ("cat <<EOF\nx y\nEOF\nb", [["cat"], ["b"]]),
    ("cat <<-EOF\n\tx\n\tEOF\nb", [["cat"], ["b"]]),
    ("cat <<'EOF'\n$(boom)\nEOF\nb", [["cat"], ["b"]]),
    ("(a; b)", [["a"], ["b"]]),
    ("{ a; b; }", [["{", "a"], ["b"], ["}"]]),
    ("echo $(a b)", [["a", "b"], ["echo", "$(...)"]]),
    ("echo `a b`", [["a", "b"], ["echo", "`...`"]]),
    ("echo \"$(a)\"", [["a"], ["echo", "$(...)"]]),
    ("echo ${X:-$(a)}", [["a"], ["echo", "${X:-$(a)}"]]),
    ("echo $X $1 $@", [["echo", "$X", "$1", "$@"]]),
    ("echo $", [["echo", "$"]]),
    ("echo a$", [["echo", "a$"]]),
    ("a <(b) c", [["b"], ["a", "<(...)", "c"]]),
    ("case x in x) a ;; esac", [["case", "x", "in", "x"], ["a"], ["esac"]]),
    ("", []), ("   ", []), (";;;", []), ("&&", []),
])
def test_tokenization(src, expected):
    assert texts(src) == expected


def test_word_flags():
    w = parse_script("echo $X \"$X\" a$X $(b) *.c {a,b} 'x*' {")[-1].words
    assert w[1].bare and w[1].dyn
    assert w[2].bare
    assert w[3].dyn and not w[3].bare
    assert w[4].dyn and not w[4].bare
    assert w[5].glob and w[6].glob and not w[7].glob
    assert not w[8].glob  # a lone brace word is the group keyword


def test_pipe_prev_links_pipeline():
    cmds = parse_script("echo hi | sh; ls")
    assert cmds[1].pipe_prev is cmds[0]
    assert cmds[2].pipe_prev is None


def test_heredoc_and_herestring_are_captured():
    c = parse_script("sh <<EOF\ngit reset --hard\nEOF")[0]
    assert c.heredocs == ["git reset --hard"]
    c = parse_script("sh <<< 'x y'")[0]
    assert c.herestrings == ["x y"]


def test_unquoted_heredoc_substitution_is_hoisted_but_quoted_is_not():
    assert any(c.texts() == ["boom"] for c in parse_script("cat <<EOF\n$(boom)\nEOF"))
    assert not any(c.texts() == ["boom"] for c in parse_script("cat <<'EOF'\n$(boom)\nEOF"))


@pytest.mark.parametrize("src", ["echo 'unterminated", "echo \"unterminated", "echo $(unterminated", "echo `unterminated",
                                  "echo ${unterminated", "(((", ")))", "echo \\", "cat <<EOF\nnever ends", "$'unterminated",
                                  "a <(", "echo $((1+", "echo @(a|b", "echo \"$(\"", "<<", ">", "2>", "|", "((", "a;;b"])
def test_malformed_input_never_raises(src):
    parse_script(src)


def test_unterminated_quote_keeps_content():
    assert texts("git reset --hard '") == [["git", "reset", "--hard", ""]]
    assert texts("echo $(git reset --hard") == [["git", "reset", "--hard"], ["echo", "$(...)"]]


def test_extglob_parenthesis_is_part_of_word():
    assert texts("ls @(a|b) x") == [["ls", "@(a|b)", "x"]]


def test_nesting_limit_raises_cleanly():
    with pytest.raises(ShellParseError):
        parse_script("$(" * (MAX_DEPTH + 5) + "x" + ")" * (MAX_DEPTH + 5))
    with pytest.raises(ShellParseError):
        parse_script("(" * (MAX_DEPTH + 5))
    parse_script("$(" * 5 + "x" + ")" * 5)
