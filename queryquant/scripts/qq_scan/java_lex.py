"""Java source reader for the scanner: tokens, type and method outlines, string constant folding.

Not a Java parser. It tokenizes (comments dropped, string literals and text blocks decoded), matches
brackets, and walks type bodies to find member declarations:
    types   [{name, kind, start, end, annotations, methods, consts}]
    methods [{name, start, name_idx, params, body, annotations, return_tokens}]
Annotations are (name, (args_start, args_end) or None, line, token_index). `fold()` evaluates a
string expression made of literals, text blocks, `+`, same-file constants (`NAME`, `Type.NAME`) and
method-local `String` variables; anything else returns (None, reason).
"""
import collections
import logging
import re

log = logging.getLogger("qq_scan.java_lex")

JTok = collections.namedtuple("JTok", "type value line start")
_TOKEN = re.compile(r'''
    (?P<ws>\s+) | (?P<lc>//[^\n]*) | (?P<bc>/\*.*?\*/)
  | (?P<text>"""[ \t\f]*\r?\n(?:[^"\\]|\\.|"(?!""))*""")
  | (?P<str>"(?:[^"\\\n]|\\.)*")
  | (?P<char>'(?:[^'\\\n]|\\.)+')
  | (?P<num>\d[\w.]*)
  | (?P<ident>[A-Za-z_$][\w$]*)
  | (?P<op>::|->|\+=|==|!=|<=|>=|&&|\|\||[-+*/%=<>!&|^~?:])
  | (?P<punct>[(){}\[\];,.@])
  | (?P<other>.)
''', re.X | re.S)
_ESC = {"n": "\n", "t": "\t", "r": "\r", "s": " ", '"': '"', "'": "'", "\\": "\\", "b": "", "f": ""}
MODIFIERS = {"public", "private", "protected", "static", "final", "abstract", "default", "synchronized",
             "native", "transient", "volatile", "strictfp", "sealed", "non"}
TYPE_WORDS = {"class", "interface", "enum", "record"}


def _unescape(body):
    return re.sub(r"\\(\r?\n|.)", lambda m: "" if m.group(1) in ("\n", "\r\n") else _ESC.get(m.group(1), m.group(1)),
                  body, flags=re.S)


def tokenize(text):
    """Java tokens with 1-based line numbers; str/text token values are the decoded string."""
    out, line, pos = [], 1, 0
    for m in _TOKEN.finditer(text):
        kind, raw = m.lastgroup, m.group()
        line += text.count("\n", pos, m.start())
        pos = m.start()
        if kind in ("ws", "lc", "bc"):
            continue
        if kind == "text":
            body = raw[3:-3].split("\n", 1)[1] if "\n" in raw[3:-3] else ""
            out.append(JTok("str", _unescape(body), line, m.start()))
        elif kind == "str":
            out.append(JTok("str", _unescape(raw[1:-1]), line, m.start()))
        else:
            out.append(JTok(kind, raw, line, m.start()))
    return out


def match_brackets(toks):
    """Map each bracket token index to its partner's index."""
    pairs, stack = {}, []
    opener = {")": "(", "]": "[", "}": "{"}
    for i, t in enumerate(toks):
        if t.type != "punct":
            continue
        if t.value in "([{":
            stack.append(i)
        elif t.value in ")]}":
            while stack and toks[stack[-1]].value != opener[t.value]:
                stack.pop()
            if stack:
                j = stack.pop()
                pairs[i], pairs[j] = j, i
    return pairs


class Outline:
    """Types, methods and constants of one Java file."""

    def __init__(self, text):
        self.toks = tokenize(text)
        self.match = match_brackets(self.toks)
        self.types = []
        self._walk(0, len(self.toks), None)

    def _annotation(self, i):
        """Parse `@Name[.x](args)` at i. Returns (annotation, next_index)."""
        t = self.toks
        j, name = i + 1, None
        if j < len(t) and t[j].type == "ident":
            name, j = t[j].value, j + 1
            while j + 1 < len(t) and t[j].value == "." and t[j + 1].type == "ident":
                name, j = t[j + 1].value, j + 2
        args = None
        if j < len(t) and t[j].value == "(" and j in self.match:
            args = (j + 1, self.match[j])
            j = self.match[j] + 1
        return (name, args, t[i].line, i), j

    def _walk(self, start, end, owner):
        """Scan members between start and end (exclusive), adding types/methods/consts."""
        t, i = self.toks, start
        while i < end:
            anns, member_start, j, saw_eq = [], i, i, False
            after_anns = i
            while j < end:
                tok = t[j]
                if tok.value == "@" and j + 1 < end and t[j + 1].value != "interface":
                    ann, j = self._annotation(j)
                    anns.append(ann)
                    after_anns = j
                    continue
                if tok.type == "ident" and tok.value in TYPE_WORDS and (j == 0 or t[j - 1].value != ".") \
                        and j + 1 < end and t[j + 1].type == "ident":
                    k = j
                    while k < end and t[k].value != "{":
                        k += 1
                    if k >= end or k not in self.match:
                        return
                    typ = {"name": t[j + 1].value, "kind": tok.value, "start": k, "end": self.match[k],
                           "annotations": anns, "methods": [], "consts": {}, "owner": owner}
                    self.types.append(typ)
                    log.debug("type %s %s at line %d", tok.value, typ["name"], tok.line)
                    self._walk(k + 1, self.match[k], typ)
                    i = self.match[k] + 1
                    break
                if tok.value == "(" and not saw_eq and owner is not None and j > member_start \
                        and t[j - 1].type == "ident" and j in self.match:
                    close = self.match[j]
                    k = close + 1
                    while k < end and t[k].value not in ("{", ";", ",", "="):
                        if t[k].value == "(" and k in self.match:
                            k = self.match[k]
                        k += 1
                    if k < end and t[k].value in ("{", ";"):
                        body = (k, self.match[k]) if t[k].value == "{" and k in self.match else None
                        owner["methods"].append({
                            "name": t[j - 1].value, "start": member_start, "name_idx": j - 1,
                            "params": (j + 1, close), "body": body, "annotations": anns,
                            "return_tokens": [x for x in t[after_anns:j - 1] if x.value not in MODIFIERS]})
                        i = (body[1] if body else k) + 1
                        break
                    j = close + 1
                    continue
                if tok.value == "=":
                    saw_eq = True
                    k = j + 1
                    while k < end and t[k].value != ";":
                        k = self.match.get(k, k) + 1 if t[k].value in "([{" and t[k].type == "punct" else k + 1
                    if owner is not None and j >= 2 and t[j - 1].type == "ident" and t[j - 2].value == "String":
                        owner["consts"][t[j - 1].value] = (j + 1, k)
                    i = k + 1
                    break
                if tok.value in (";", "}"):
                    i = j + 1
                    break
                if tok.value == "{" and j in self.match:
                    i = self.match[j] + 1
                    break
                j += 1
            else:
                i = end

    def type_at(self, idx):
        """Innermost type whose body contains token idx."""
        best = None
        for typ in self.types:
            if typ["start"] <= idx <= typ["end"] and (best is None or typ["start"] > best["start"]):
                best = typ
        return best

    def method_at(self, idx):
        typ = self.type_at(idx)
        while typ is not None:
            for m in typ["methods"]:
                if m["body"] and m["body"][0] <= idx <= m["body"][1]:
                    return typ, m
            typ = typ["owner"]
        return self.type_at(idx), None

    def fold(self, start, end, typ, method=None, before=None, _seen=None):
        """Evaluate tokens[start:end] as a string expression. Returns (value, None) or (None, reason)."""
        t, seen = self.toks, set(_seen or ())
        toks = list(range(start, end))
        while toks and t[toks[0]].value == "(" and self.match.get(toks[0]) == toks[-1]:
            toks = toks[1:-1]
        if not toks:
            return None, "empty SQL expression"
        parts, cur, depth = [], [], 0
        for k in toks:
            v = t[k].value
            depth += (v in "([") - (v in ")]") if t[k].type == "punct" else 0
            if depth == 0 and v == "+" and t[k].type == "op":
                parts.append(cur)
                cur = []
            else:
                cur.append(k)
        parts.append(cur)
        out = []
        for part in parts:
            vals = [t[k] for k in part]
            if len(vals) == 1 and vals[0].type == "str":
                out.append(vals[0].value)
                continue
            if len(vals) >= 1 and vals[0].value == "(":
                v, why = self.fold(part[0], part[-1] + 1, typ, method, before, seen)
            elif len(vals) == 1 and vals[0].type == "ident":
                v, why = self._lookup(vals[0].value, typ, method, before or part[0], seen)
            elif len(vals) == 3 and vals[1].value == "." and vals[0].type == vals[2].type == "ident":
                owner = next((x for x in self.types if x["name"] == vals[0].value), None)
                if owner is None:
                    return None, "constant from another file: %s.%s" % (vals[0].value, vals[2].value)
                v, why = self._lookup(vals[2].value, owner, None, None, seen)
            else:
                return None, "SQL built by code: %s" % " ".join(x.value for x in vals[:6])
            if v is None:
                return None, why
            out.append(v)
        return "".join(out), None

    def _lookup(self, name, typ, method, before, seen):
        if name in seen:
            return None, "constant refers to itself: %s" % name
        seen = seen | {name}
        if method and method["body"] and before:
            t = self.toks
            lo, hi = method["body"][0], before
            for k in range(hi - 1, lo, -1):
                if t[k].value == name and t[k].type == "ident" and k + 1 < len(t):
                    nxt, prev = t[k + 1].value, t[k - 1].value
                    if nxt == "+=" or (nxt == "=" and prev != "String" and prev not in ("var",)):
                        return None, "SQL variable %s is reassigned before use" % name
                    if nxt == "=" and prev in ("String", "var"):
                        end = k + 2
                        while end < len(t) and t[end].value != ";":
                            end = self.match.get(end, end) + 1 if t[end].value in "([" and t[end].type == "punct" else end + 1
                        return self.fold(k + 2, end, typ, method, k, seen)
        if method and any(self.toks[k].value == name for k in range(*method["params"])):
            return None, "SQL is a method parameter (%s); its callers pass the text" % name
        scope = typ
        while scope is not None:
            if name in scope["consts"]:
                s, e = scope["consts"][name]
                return self.fold(s, e, scope, None, None, seen)
            scope = scope["owner"]
        return None, "SQL comes from a variable or field this scanner cannot resolve: %s" % name
