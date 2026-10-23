"""A small SQL tokenizer and clause reader for MySQL-flavoured statements. Not a full parser.

Tokens come from sql_lex. `shape(sql, list_binds)` reads one statement into a dict the rules use:
    kind, tables [{name, schema, alias}], has_where, preds [{alias, column, op}], join_preds (same,
    op "join", plus rhs_ref (alias, column) when the other side is a plain column),
    catch_all [{bind, columns}], order_by [(alias, column, dir)], order_expr, group_by, limit,
    offset, for_update, select_star, defines, not_analysed (reason or None).
Predicate ops: eq, in, range, like, like-leading, other. `IS NULL` counts as eq, `IS NOT NULL` as range.
Anything outside what it understands sets `not_analysed` instead of guessing.
"""
import logging
import re

from .sql_lex import Tok, depths, low, name_of, normalise, split, strip_parens, tokenize

log = logging.getLogger("qq_scan.sql_shape")

JOIN_WORDS = {"join", "inner", "left", "right", "outer", "cross", "straight_join", "natural"}
VALUE_WORDS = {"null", "true", "false", "interval", "day", "hour", "minute", "second", "month", "year",
               "week", "microsecond", "quarter", "current_timestamp", "current_date", "current_time",
               "utc_timestamp", "localtime", "localtimestamp", "unknown", "and", "or", "not", "is",
               "case", "when", "then", "else", "end", "as", "binary", "distinct", "separator", "asc",
               "desc", "escape", "collate", "unsigned", "signed", "char", "date", "datetime", "decimal",
               "using", "utf8mb4", "select", "from", "where", "in", "exists", "like", "between"}


def _colref(toks, i):
    """Parse [alias.]column at i. Returns (alias, column, next_index) or None."""
    if i >= len(toks) or name_of(toks[i]) is None:
        return None
    if i + 2 < len(toks) and toks[i + 1].value == "." and name_of(toks[i + 2]) is not None:
        if i + 4 < len(toks) and toks[i + 3].value == "." and name_of(toks[i + 4]) is not None:
            j = i + 5          # schema.table.column
            alias, col = name_of(toks[i + 2]), name_of(toks[i + 4])
        else:
            j = i + 3
            alias, col = name_of(toks[i]), name_of(toks[i + 2])
    else:
        j, alias, col = i + 1, None, name_of(toks[i])
        if toks[i].type == "ident" and col in VALUE_WORDS - {"date"}:
            return None
    if j < len(toks) and toks[j].value == "(":
        return None            # function call, not a column
    return alias, col, j


def _value_refs_columns(toks):
    for i, t in enumerate(toks):
        if t.type == "qident":
            return True
        if t.type == "ident" and t.value.lower() not in VALUE_WORDS:
            nxt = toks[i + 1].value if i + 1 < len(toks) else ""
            prev = toks[i - 1].value if i else ""
            if nxt != "(" and prev != "." and not (i and toks[i - 1].value.lower() == "interval"):
                return True
    return False


def _predicate(toks):
    """Classify one AND-term. Returns dict(alias, column, op, rhs) or None."""
    toks = strip_parens(toks)
    ref = _colref(toks, 0)
    if ref is None:
        if len(toks) >= 3 and toks[-2].value != "." and _colref(toks, len(toks) - 1):
            alias, col, _ = _colref(toks, len(toks) - 1)
            if toks[-2].value == "=" and not _value_refs_columns(toks[:-2]):
                return {"alias": alias, "column": col, "op": "eq", "rhs": toks[:-2]}
        return None
    alias, col, j = ref
    rest = toks[j:]
    words = [t.value.lower() for t in rest[:3]]
    if not words:
        return None
    if words[0] in ("=", "<=>"):
        op, rhs = "eq", rest[1:]
    elif words[0] in ("<", ">", "<=", ">="):
        op, rhs = "range", rest[1:]
    elif words[0] == "in":
        op, rhs = "in", rest[1:]
    elif words[0] == "between":
        op, rhs = "range", rest[1:]
    elif words[0] == "like":
        rhs = rest[1:]
        lead = rhs and rhs[0].type == "str" and rhs[0].value[1:2] == "%"
        lead = lead or (rhs and low(rhs[0]) == "concat" and len(rhs) > 2 and rhs[2].value.startswith("'%"))
        op = "like-leading" if lead else "like"
    elif words[:2] == ["is", "null"]:
        op, rhs = "eq", []
    elif words[:3] == ["is", "not", "null"]:
        op, rhs = "range", []
    else:
        op, rhs = "other", rest
    if op != "in" and _value_refs_columns(rhs):
        other = _colref(rhs, 0)
        return {"alias": alias, "column": col, "op": "join", "rhs": rhs,
                "rhs_ref": other[:2] if other and other[2] == len(rhs) else None}
    return {"alias": alias, "column": col, "op": op, "rhs": rhs}


def _first_column(toks):
    for i, t in enumerate(toks):
        ref = _colref(toks, i) if t.type in ("ident", "qident") else None
        if ref and (t.type == "qident" or t.value.lower() not in VALUE_WORDS):
            return ref[1]
    return None


def _catch_all(term_toks):
    """`(:x IS NULL OR col = :x)` and variants (any operator, `col IN (<list>)`, nested AND).

    Returns [{bind, columns}].
    """
    out = []
    terms = [strip_parens(t) for t in split(term_toks, {"or"})]
    for k, nt in enumerate(terms):
        if not (len(nt) == 3 and nt[0].type == "bind" and nt[1].value.lower() == "is"
                and nt[2].value.lower() == "null"):
            continue
        bind, cols = nt[0].value, []
        for j, t in enumerate(terms):
            if j == k:
                continue
            uses_bind = any(x.type == "bind" and x.value == bind for x in t)
            p = _predicate(t)
            list_in = len(terms) == 2 and p is not None and p["op"] == "in" and any(
                x.type == "bind" for x in p["rhs"])
            if uses_bind or list_in:
                col = p["column"] if p and p["op"] != "join" else _first_column(t)
                if col:
                    cols.append(col)
        if cols:
            out.append({"bind": bind, "columns": cols})
    return out


def _where_terms(toks, s):
    """Walk AND-terms (recursing into parenthesised AND groups); OR groups only feed catch_all."""
    for term in split(toks, {"and"}):
        term = strip_parens(term)
        if len(split(term, {"and"})) > 1:
            _where_terms(term, s)
            continue
        if len(split(term, {"or"})) > 1:
            s["catch_all"].extend(_catch_all(term))
            continue
        p = _predicate(term)
        if p is not None:
            (s["join_preds"] if p["op"] == "join" else s["preds"]).append(p)


_COALESCE = re.compile(r"\b(?:coalesce|ifnull)\s*\(\s*(:\w+)\s*,\s*([\w.`]+)\s*\)\s*=\s*([\w.`]+)", re.I)


def _table_refs(toks, shape):
    """Read a FROM / UPDATE table list into shape['tables'] and shape['join_preds']."""
    items, cur, depth = [], [], 0
    for t in toks:
        lv = t.value.lower()
        if t.value == "(":
            depth += 1
        elif t.value == ")":
            depth -= 1
        if depth == 0 and (lv == "," or (t.type == "ident" and lv in JOIN_WORDS)):
            if cur:
                items.append(cur)
            cur = []
            continue
        cur.append(t)
    if cur:
        items.append(cur)
    for item in items:
        on_at = next((i for i, t in enumerate(item) if low(t) in ("on", "using")), len(item))
        head, cond = item[:on_at], item[on_at + 1:]
        if not head:
            continue
        if head[0].type == "define":
            shape["not_analysed"] = "template placeholder %s used as a table" % head[0].value
            return
        if head[0].value == "(":
            alias = name_of(head[-1]) if head[-1].value != ")" else None
            shape["tables"].append({"name": None, "schema": None, "alias": alias, "derived": True})
            continue
        if name_of(head[0]) is None:
            shape["not_analysed"] = "cannot read table list near %r" % head[0].value
            return
        schema, name, k = None, name_of(head[0]), 1
        if len(head) > 2 and head[1].value == "." and name_of(head[2]):
            schema, name, k = name, name_of(head[2]), 3
        rest = [t for t in head[k:] if low(t) != "as"]
        alias = name_of(rest[0]) if rest and name_of(rest[0]) not in (None, "use", "force", "ignore") else None
        shape["tables"].append({"name": name, "schema": schema, "alias": alias or name, "derived": False})
        for term in split(cond, {"and"}):
            p = _predicate(term)
            if p:
                shape["join_preds"].append(p)


def _clauses(toks, start_words):
    """Index of each depth-0 clause keyword in toks."""
    found, dep = {}, depths(toks)
    for i, t in enumerate(toks):
        if dep[i] == 0 and t.type == "ident":
            lv = t.value.lower()
            if lv in start_words and lv not in found:
                if lv in ("group", "order") and not (i + 1 < len(toks) and low(toks[i + 1]) == "by"):
                    continue
                found[lv] = i
    return found


def shape(sql, list_binds=()):
    """Read one statement. `list_binds` are `<name>` placeholders known to be @BindList lists."""
    toks = tokenize(sql)
    s = {"kind": "other", "tables": [], "has_where": False, "preds": [], "join_preds": [],
         "catch_all": [], "order_by": [], "order_expr": False, "group_by": [], "limit": None,
         "offset": None, "for_update": False, "select_star": False, "defines": [], "not_analysed": None}
    if not toks:
        s["not_analysed"] = "empty SQL"
        return s
    defines = [t.value[1:-1] for t in toks if t.type == "define"]
    s["defines"] = [d for d in defines if d not in list_binds]
    toks = [Tok("bind", ":" + t.value[1:-1], t.start, t.end) if t.type == "define" and t.value[1:-1] in list_binds
            else t for t in toks]
    first = low(toks[0]) or ""
    s["kind"] = {"select": "select", "insert": "insert", "replace": "insert", "update": "update",
                 "delete": "delete"}.get(first, "other")
    if first == "with":
        s["not_analysed"] = "common table expression (WITH)"
        return s
    if s["kind"] == "other":
        s["not_analysed"] = "statement type %s is not analysed" % (toks[0].value.upper())
        return s
    marks = _clauses(toks, {"from", "where", "group", "having", "order", "limit", "offset", "for", "union",
                            "set", "values", "lock", "into", "select", "on"})
    if "union" in marks:
        s["not_analysed"] = "UNION"
        return s
    if s["kind"] == "insert":
        into = marks.get("into", 0)
        if into + 1 < len(toks) and name_of(toks[into + 1]):
            k = into + 1
            schema = None
            name = name_of(toks[k])
            if k + 2 < len(toks) and toks[k + 1].value == ".":
                schema, name = name, name_of(toks[k + 2])
            s["tables"].append({"name": name, "schema": schema, "alias": name, "derived": False})
        return s
    order = [w for w in ("where", "group", "having", "order", "limit", "offset", "for", "lock") if w in marks]

    def span(word):
        if word not in marks:
            return []
        start = marks[word] + (2 if word in ("group", "order") else 1)
        later = [marks[w] for w in order if marks[w] > marks[word]]
        return toks[start:min(later) if later else len(toks)]

    if s["kind"] == "select":
        if "from" not in marks:
            inner = [i for i in range(len(toks) - 1) if toks[i].value == "(" and low(toks[i + 1]) == "select"]
            if inner:
                depth, k = 0, inner[0]
                for k in range(inner[0], len(toks)):
                    depth += (toks[k].value == "(") - (toks[k].value == ")")
                    if depth == 0:
                        break
                log.debug("SELECT without FROM: reading its first subquery instead")
                return shape(sql[toks[inner[0] + 1].start:toks[k].start], list_binds)
            s["not_analysed"] = "SELECT without FROM"
            return s
        sel = toks[1:marks["from"]]
        s["select_star"] = any(t.value == "*" and (i == 0 or sel[i - 1].value in (",", ".") or
                                                    low(sel[i - 1]) == "distinct")
                               for i, t in enumerate(sel))
        later = [marks[w] for w in order if marks[w] > marks["from"]]
        _table_refs(toks[marks["from"] + 1:min(later) if later else len(toks)], s)
    elif s["kind"] == "update":
        end = marks.get("set", len(toks))
        _table_refs([t for t in toks[1:end] if low(t) not in ("low_priority", "ignore")], s)
    else:
        if "from" not in marks:
            s["not_analysed"] = "DELETE without FROM"
            return s
        later = [marks[w] for w in order if marks[w] > marks["from"]]
        refs = toks[marks["from"] + 1:min(later) if later else len(toks)]
        use = next((i for i, t in enumerate(refs) if low(t) == "using"), None)
        _table_refs(refs[:use] if use is not None else refs, s)
    if s["not_analysed"]:
        return s
    where = span("where")
    s["has_where"] = bool(where)
    _where_terms(where, s)
    for m in _COALESCE.finditer(normalise(sql)):
        if m.group(2).replace("`", "").lower() == m.group(3).replace("`", "").lower():
            s["catch_all"].append({"bind": m.group(1), "columns": [m.group(2).split(".")[-1].strip("`").lower()]})
    for item in split(span("order"), {","}):
        ref = _colref(item, 0)
        tail = [t.value.lower() for t in item[ref[2]:]] if ref else None
        if ref is None or tail not in ([], ["asc"], ["desc"]):
            s["order_expr"] = True
            continue
        s["order_by"].append((ref[0], ref[1], "desc" if tail == ["desc"] else "asc"))
    for item in split(span("group"), {","}):
        ref = _colref(item, 0)
        s["group_by"].append((ref[0], ref[1]) if ref else None)
    lim = span("limit")
    if lim:
        parts = split(lim, {",", "offset"})
        s["limit"] = parts[-1][0].value if len(parts) == 2 and "," in [t.value for t in lim] else parts[0][0].value
        if len(parts) == 2:
            s["offset"] = parts[0][0].value if "," in [t.value for t in lim] else parts[1][0].value
    off = span("offset")
    if off:
        s["offset"] = off[0].value
    s["for_update"] = "for" in marks and any(low(t) == "update" for t in toks[marks["for"]:])
    return s
