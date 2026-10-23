"""Find SQL statements in Java sources and the places DAO methods are called from.

Statements:
  sqlobject  @SqlQuery / @SqlUpdate / @SqlBatch on a method (value is a literal, text block,
             `"a" + "b"`, or a same-file String constant). The line is the annotation's line.
  fluent     handle.createQuery(sql) / createUpdate(sql); `sql` may be a local String variable.
  jdbc       connection.prepareStatement(sql).
DAO types are interfaces with SQL Object annotations and classes that hold fluent statements.
Call sites are `var.method(` / `var::method` / `attach(Type.class).method(` where var's declared type
(field, parameter, local, `withExtension(Type.class, var -> ...)`) is a DAO type. Loop ranges are
`for` bodies (not headers), `while` (header and body), `do` bodies, and the argument of
`.forEach(...)`, plus `.map/.flatMap/.peek/.filter(...)` on a chain that contains a stream.
"""
import logging
import os

from .java_lex import Outline

log = logging.getLogger("qq_scan.java_sql")

SQL_ANNOTATIONS = {"SqlQuery": "select", "SqlUpdate": None, "SqlBatch": None}
FLUENT = {"createQuery": "fluent", "createUpdate": "fluent", "prepareStatement": "jdbc"}
LAMBDA_CALLS = {"map", "forEach", "flatMap", "peek", "filter"}
STREAMY = {"stream", "parallelStream", "Stream", "IntStream", "LongStream", "Flux"}
EXTENSION_CALLS = {"withExtension", "useExtension", "inTransaction", "useTransaction"}
LISTY = {"List", "Collection", "Set", "Iterable", "ResultIterable", "ArrayList", "LinkedList",
         "HashSet", "SortedSet", "LinkedHashSet"}
STREAM_TYPES = {"Stream", "Iterator", "ResultIterator"}
SCALARS = {"int", "long", "short", "boolean", "Integer", "Long", "Short", "Boolean", "byte"}


def classify_return(tokens, annotation):
    """Map return type tokens to List|Optional|Stream|single|void|int|other."""
    vals = [x.value for x in tokens]
    if vals and vals[0] == "<":
        depth = 0
        for k, v in enumerate(vals):
            depth += (v == "<") - (v == ">")
            if depth == 0:
                vals = vals[k + 1:]
                break
    if not vals:
        return "other"
    head, is_array = vals[0], "[" in vals
    if head == "void":
        return "void"
    if annotation in ("SqlUpdate", "SqlBatch"):
        return "int" if head in SCALARS else "other"
    if head in LISTY or (is_array and head not in ("byte",)):
        return "List"
    if head == "Optional" or head.startswith("Optional"):
        return "Optional"
    if head in STREAM_TYPES:
        return "Stream"
    if head == "Map":
        return "other"
    return "single"


def _ann_names(anns):
    out = []
    for name, _args, _line, _idx in anns:
        if name and "@" + name not in out:
            out.append("@" + name)
    return out


def _param_annotations(o, method):
    """Annotations inside the parameter list, and the names bound by @BindList / @Define."""
    s, e = method["params"]
    names, lists, defines = [], [], []
    k = s
    while k < e:
        if o.toks[k].value == "@" and k + 1 < e:
            ann, nk = o._annotation(k)
            if "@" + ann[0] not in names:
                names.append("@" + ann[0])
            arg = next((o.toks[x].value for x in range(*ann[1]) if o.toks[x].type == "str"), None) if ann[1] else None
            if arg is None and ann[0] in ("BindList", "Define"):
                arg = next((o.toks[x].value for x in range(nk, e) if o.toks[x].type == "ident"
                            and (x + 1 == e or o.toks[x + 1].value in (",", ")"))), None)
            if ann[0] == "BindList" and arg:
                lists.append(arg)
            elif ann[0] == "Define" and arg:
                defines.append(arg)
            k = nk
        else:
            k += 1
    return names, lists, defines


class JavaFile:
    """One source file: outline plus the statements and calls found in it."""

    def __init__(self, path, rel):
        self.path, self.rel = path, rel
        with open(path, encoding="utf-8", errors="replace") as fh:
            self.outline = Outline(fh.read())
        self.statements = []
        self.loops = []


def sqlobject_statements(jf, service):
    o, out = jf.outline, []
    for typ in o.types:
        type_anns = _ann_names(typ["annotations"])
        for m in typ["methods"]:
            sql_ann = next((a for a in m["annotations"] if a[0] in SQL_ANNOTATIONS), None)
            if sql_ann is None:
                continue
            name, args, line, _ = sql_ann
            sql, why = None, None
            if args is None or args[0] == args[1]:
                why = "SQL loaded from a classpath file (no annotation value)"
            else:
                s, e = args
                if e - s > 2 and o.toks[s].value == "value" and o.toks[s + 1].value == "=":
                    s += 2
                sql, why = o.fold(s, e, typ)
            pnames, lists, defines = _param_annotations(o, m)
            out.append({"service": service, "file": jf.rel, "line": line, "source": "sqlobject",
                        "dao": typ["name"], "method": m["name"],
                        "returns": classify_return(m["return_tokens"], name),
                        "annotations": _ann_names(m["annotations"]) + [p for p in pnames if p not in
                                                                        _ann_names(m["annotations"])],
                        "raw_sql": sql, "unresolved": why, "_ann": name, "_lists": lists,
                        "_defines": defines, "_type_anns": type_anns})
            log.debug("statement %s:%d %s.%s @%s returns=%s %s", jf.rel, line, typ["name"], m["name"], name,
                      out[-1]["returns"], "resolved" if sql is not None else "unresolved: %s" % why)
    return out


def _chain_after(o, close):
    """Method names called on the result: `).a(..).b(..)` -> ["a", "b"] plus their first str args."""
    t, k, names = o.toks, close + 1, []
    while k + 2 < len(t) and t[k].value == "." and t[k + 1].type == "ident" and t[k + 2].value == "(":
        arg = t[k + 3].value if k + 3 < len(t) and t[k + 3].type == "str" else None
        names.append((t[k + 1].value, arg))
        k = o.match.get(k + 2, k + 2) + 1
    return names


def fluent_statements(jf, service):
    o, t, out = jf.outline, jf.outline.toks, []
    for i, tok in enumerate(t):
        if tok.type != "ident" or tok.value not in FLUENT or i == 0 or t[i - 1].value != "." \
                or i + 1 >= len(t) or t[i + 1].value != "(" or (i + 1) not in o.match:
            continue
        close = o.match[i + 1]
        depth, end = 0, close
        for k in range(i + 2, close):
            depth += (t[k].value in "([{") - (t[k].value in ")]}") if t[k].type == "punct" else 0
            if depth == 0 and t[k].value == ",":
                end = k
                break
        typ, method = o.method_at(i)
        sql, why = o.fold(i + 2, end, typ, method, i)
        chain = _chain_after(o, close)
        names = [c[0] for c in chain]
        returns = "other"
        for n in names:
            returns = {"list": "List", "collectIntoList": "List", "stream": "Stream", "iterator": "Stream",
                       "findOne": "Optional", "findFirst": "Optional", "one": "single", "findOnly": "single",
                       "first": "single", "execute": "int"}.get(n, returns)
        anns = []
        for n, label in (("bindList", "@BindList"), ("define", "@Define"), ("bindBean", "@BindBean")):
            if n in names:
                anns.append(label)
        out.append({"service": service, "file": jf.rel, "line": tok.line, "source": FLUENT[tok.value],
                    "dao": typ["name"] if typ else os.path.basename(jf.rel)[:-5],
                    "method": method["name"] if method else None, "returns": returns, "annotations": anns,
                    "raw_sql": sql, "unresolved": why, "_ann": tok.value,
                    "_lists": [a for n, a in chain if n == "bindList" and a],
                    "_defines": [a for n, a in chain if n == "define" and a], "_type_anns": []})
        log.debug("statement %s:%d %s(...) in %s.%s returns=%s %s", jf.rel, tok.line, tok.value,
                  out[-1]["dao"], out[-1]["method"], returns, "resolved" if sql is not None else why)
    return out


def _stmt_end(o, k):
    t = o.toks
    while k < len(t) and t[k].value != ";":
        k = o.match.get(k, k) + 1 if t[k].type == "punct" and t[k].value in "([{" else k + 1
    return k


def find_loops(jf):
    """[(start, end, line, kind, header)] token ranges whose contents run once per item."""
    o, t, out = jf.outline, jf.outline.toks, []
    for i, tok in enumerate(t):
        if tok.type == "ident" and tok.value in ("for", "while") and i + 1 < len(t) and t[i + 1].value == "(":
            close = o.match.get(i + 1)
            if close is None:
                continue
            nxt = close + 1
            if nxt < len(t) and t[nxt].value == ";":
                continue                      # do { } while (...);
            body_end = o.match.get(nxt, nxt) if nxt < len(t) and t[nxt].value == "{" else _stmt_end(o, nxt)
            start = nxt if tok.value == "for" else i + 1
            header = " ".join(x.value for x in t[i + 2:close]).lower()
            out.append((start, body_end, tok.line, tok.value, header))
        elif tok.type == "ident" and tok.value == "do" and i + 1 < len(t) and t[i + 1].value == "{":
            out.append((i + 1, o.match.get(i + 1, i + 1), tok.line, "do", ""))
        elif tok.type == "ident" and tok.value in LAMBDA_CALLS and i and t[i - 1].value == "." \
                and i + 1 < len(t) and t[i + 1].value == "(" and (i + 1) in o.match:
            if tok.value != "forEach" and not _chain_has_stream(o, i - 1):
                continue
            out.append((i + 1, o.match[i + 1], tok.line, "." + tok.value, ""))
    jf.loops = out
    return out


def _chain_has_stream(o, dot):
    t, k = o.toks, dot - 1
    while k >= 0:
        if t[k].value == ")" and k in o.match:
            k = o.match[k] - 1
            continue
        if t[k].type == "ident":
            if t[k].value in STREAMY:
                return True
            k -= 1
            continue
        if t[k].value in (".", "::"):
            k -= 1
            continue
        return False
    return False


def dao_variables(jf, dao_types):
    """{variable name: DAO type} for this file."""
    t, out = jf.outline.toks, {}
    for i, tok in enumerate(t):
        if tok.type != "ident" or tok.value not in dao_types:
            continue
        nxt = t[i + 1] if i + 1 < len(t) else None
        if nxt is not None and nxt.type == "ident" and nxt.value not in ("class",) and i + 2 < len(t) \
                and t[i + 2].value in ("=", ";", ",", ")", ":"):
            out[nxt.value] = tok.value
        if nxt is not None and nxt.value == "." and i + 3 < len(t) and t[i + 2].value == "class":
            back = i - 1
            while back > 0 and t[back].value not in ("=", ";", "{", "}"):
                back -= 1
            if t[back].value == "=" and t[back - 1].type == "ident":
                out[t[back - 1].value] = tok.value
            if t[i + 3].value == "," and i + 5 < len(t) and t[i + 4].type == "ident" and t[i + 5].value == "->":
                out[t[i + 4].value] = tok.value
    log.debug("DAO variables in %s: %s", jf.rel, out)
    return out


def call_sites(jf, dao_types):
    """[{line, idx, var, type, method, loop, caller_type, caller_method}] for DAO method calls."""
    o, t, out = jf.outline, jf.outline.toks, []
    variables = dao_variables(jf, dao_types)
    for i, tok in enumerate(t):
        if tok.type != "ident" or i + 2 >= len(t):
            continue
        typ = var = None
        if tok.value in variables and t[i + 1].value in (".", "::") and t[i + 2].type == "ident":
            var, typ = tok.value, variables[tok.value]
        elif tok.value == "class" and t[i - 1].value == "." and t[i - 2].value in dao_types \
                and t[i + 1].value == ")" and i + 3 < len(t) and t[i + 2].value == ".":
            var, typ, i = "attach(%s.class)" % t[i - 2].value, t[i - 2].value, i + 1
        if typ is None:
            continue
        name_tok = t[i + 2]
        if name_tok.value not in dao_types[typ]:
            continue
        if t[i + 1].value == "." and (i + 3 >= len(t) or t[i + 3].value != "("):
            continue
        loops = [lp for lp in jf.loops if lp[0] <= i <= lp[1]]
        loop = max(loops, key=lambda lp: lp[0]) if loops else None
        ctype, cmethod = o.method_at(i)
        out.append({"line": name_tok.line, "idx": i, "var": var, "type": typ, "method": name_tok.value,
                    "loop": loop, "caller_type": ctype["name"] if ctype else None,
                    "caller_method": cmethod["name"] if cmethod else None, "file": jf.rel,
                    "caller_anns": _ann_names(cmethod["annotations"]) if cmethod else []})
        log.debug("call %s:%d %s.%s (%s)%s", jf.rel, name_tok.line, var, name_tok.value, typ,
                  " inside %s loop at line %d" % (loop[3], loop[2]) if loop else "")
    return out


def helper_calls(jf):
    """Calls to methods of the enclosing class: bare `name(` or `this.name(` (not declarations, not `new`).

    Returns [{line, idx, name, loop, caller_type, caller_method, caller_anns, file}].
    """
    o, t, out = jf.outline, jf.outline.toks, []
    decl = {m["name_idx"] for typ in o.types for m in typ["methods"]}
    for i, tok in enumerate(t):
        if tok.type != "ident" or i in decl or i + 1 >= len(t) or t[i + 1].value != "(":
            continue
        prev = t[i - 1].value if i else ""
        if prev == "new" or (prev == "." and not (i >= 2 and t[i - 2].value == "this")) or prev == "::":
            continue
        ctype, cmethod = o.method_at(i)
        if ctype is None or tok.value not in {m["name"] for m in ctype["methods"]}:
            continue
        loops = [lp for lp in jf.loops if lp[0] <= i <= lp[1]]
        loop = max(loops, key=lambda lp: lp[0]) if loops else None
        out.append({"line": tok.line, "idx": i, "name": tok.value, "loop": loop, "caller_type": ctype["name"],
                    "caller_method": cmethod["name"] if cmethod else None, "file": jf.rel,
                    "caller_anns": _ann_names(cmethod["annotations"]) if cmethod else []})
        log.debug("helper call %s:%d %s.%s -> %s%s", jf.rel, tok.line, ctype["name"],
                  cmethod["name"] if cmethod else None, tok.value,
                  " inside %s loop at line %d" % (loop[3], loop[2]) if loop else "")
    return out


def loop_subject(jf, loop):
    """The collection a loop walks, when it is a plain name (`for (X x : orgs)`, `orgs.forEach(`)."""
    o, t = jf.outline, jf.outline.toks
    start, _end, _line, kind, header = loop
    if kind == "for" and " : " in header:
        tail = header.rsplit(" : ", 1)[1].split()
        return tail[0] if len(tail) == 1 else None
    if kind.startswith("."):
        k = start - 3
        while k >= 0 and t[k].value == ")" and k in o.match:
            k = o.match[k] - 3
        return t[k].value if k >= 0 and t[k].type == "ident" else None
    return None


def named_call_annotations(jf, into):
    """Add {method name: set of annotations on the calling methods} for every `.name(` call in the file."""
    o, t = jf.outline, jf.outline.toks
    for i in range(1, len(t) - 1):
        if t[i].type == "ident" and t[i - 1].value == "." and t[i + 1].value == "(":
            _typ, m = o.method_at(i)
            if m and m["annotations"]:
                into.setdefault(t[i].value, set()).update(_ann_names(m["annotations"]))
    return into
