"""SQL tokenizer and token helpers shared by the statement reader and the Liquibase DDL reader.

Token types: ident, qident (backticked), str, num, bind (`:name` or `?`), define (`<name>`, a JDBI
template placeholder), op, punct, other. Whitespace and comments (`--`, `#`, `/* */`) are dropped;
each token keeps its start/end offset in the source text.
"""
import collections
import re

Tok = collections.namedtuple("Tok", "type value start end")
_TOKEN = re.compile(r"""
    (?P<ws>\s+) | (?P<lc>(?:--|\#)[^\n]*) | (?P<bc>/\*.*?\*/)
  | (?P<str>'(?:[^'\\]|\\.|'')*'|"(?:[^"\\]|\\.|"")*")
  | (?P<qident>`(?:[^`]|``)*`)
  | (?P<define><[A-Za-z_][A-Za-z0-9_]*>)
  | (?P<bind>(?<!:):[A-Za-z_][A-Za-z0-9_.]*|\?)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<op><=>|<=|>=|<>|!=|:=|::|\|\||&&|[=<>+\-*/%!~^&|])
  | (?P<punct>[(),.;])
  | (?P<other>.)
""", re.X | re.S)


def tokenize(text):
    """Split SQL into tokens, dropping whitespace and comments."""
    out = []
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        if kind in ("ws", "lc", "bc"):
            continue
        out.append(Tok(kind, m.group(), m.start(), m.end()))
    return out


def low(tok):
    return tok.value.lower() if tok.type == "ident" else None


def name_of(tok):
    """Identifier text without backticks, lower-cased; None for non-identifiers."""
    if tok.type == "ident":
        return tok.value.lower()
    if tok.type == "qident":
        return tok.value[1:-1].replace("``", "`").lower()
    return None


def normalise(sql):
    """One-line form: tokens joined by single spaces where the source had any gap."""
    toks = tokenize(sql)
    parts = []
    for i, t in enumerate(toks):
        if i and t.start > toks[i - 1].end:
            parts.append(" ")
        parts.append(t.value)
    return "".join(parts).strip()


def depths(toks):
    depth, out = 0, []
    for t in toks:
        if t.value == ")":
            depth -= 1
        out.append(depth)
        if t.value == "(":
            depth += 1
    return out


def split(toks, word_set):
    """Split at depth-0 tokens whose lower value is in word_set; BETWEEN x AND y is kept whole."""
    parts, cur, depth, between = [], [], 0, False
    for t in toks:
        lv = t.value.lower() if t.type in ("ident", "punct") else None
        if t.value == "(":
            depth += 1
        elif t.value == ")":
            depth -= 1
        if depth == 0 and lv == "between":
            between = True
        elif depth == 0 and lv in word_set:
            if lv == "and" and between:
                between = False
            else:
                parts.append(cur)
                cur = []
                continue
        cur.append(t)
    parts.append(cur)
    return [p for p in parts if p]


def strip_parens(toks):
    while len(toks) >= 2 and toks[0].value == "(" and toks[-1].value == ")":
        depth = 0
        for i, t in enumerate(toks):
            depth += (t.value == "(") - (t.value == ")")
            if depth == 0 and i < len(toks) - 1:
                return toks
        toks = toks[1:-1]
    return toks
