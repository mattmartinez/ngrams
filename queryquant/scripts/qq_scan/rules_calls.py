"""Call-site rules: qq.n-plus-one, for direct DAO calls and for same-class helpers.

Direct: a read DAO method (Optional/single/scalar, or List filtered by equality or IN) called inside a
loop or stream lambda. Not when the loop walks chunks (its header names a chunk, partition or batch),
or when the call is a page fetch (List with LIMIT :bind).
Helper: a loop or lambda calls a method of the same class (bare `name(` or `this.name(`) that reaches
such reads through same-class helpers, up to depth 3. `nested: true` when one of the reached calls
sits inside a loop of its own (one query per inner item, per outer item). The inner direct finding
is still reported; the outer one is added at the loop's helper call line.
"""
import logging

from .rules import _finding

log = logging.getLogger("qq_scan.rules")
BATCH_WORDS = ("chunk", "partition", "batch")


def is_small_read(stmt):
    """Reads that return one row or a keyed list: the kind that becomes N+1 inside a loop."""
    shape = stmt.get("_shape")
    if stmt.get("_ann") in ("SqlUpdate", "SqlBatch", "createUpdate"):
        return False
    kind = shape["kind"] if shape else ("select" if stmt.get("_ann") in ("SqlQuery", "createQuery") else "other")
    if kind != "select":
        return False
    if stmt["returns"] in ("Optional", "single", "int"):
        return True
    if stmt["returns"] in ("List", "other") and shape and not shape["not_analysed"]:
        return any(p["op"] in ("eq", "in") for p in shape["preds"])
    return False


def n_plus_one(calls, registry):
    out = []
    for call in calls:
        if call["loop"] is None:
            continue
        stmts = [s for s in registry.get(call["type"], {}).get(call["method"], []) if is_small_read(s)]
        if not stmts:
            log.debug("call %s:%d %s.%s in loop: not a small read; no finding", call["file"], call["line"],
                      call["var"], call["method"])
            continue
        s = stmts[0]
        _start, _end, loop_line, kind, header = call["loop"]
        if any(w in header for w in BATCH_WORDS):
            log.debug("call %s:%d %s.%s: loop walks chunks (%s); batched, no finding", call["file"],
                      call["line"], call["var"], call["method"], header[:60])
            continue
        limit = s["_shape"]["limit"] if s.get("_shape") else None
        if s["returns"] in ("List", "Stream") and limit and (limit.startswith(":") or limit == "?"):
            log.debug("call %s:%d %s.%s: page fetch (LIMIT %s) in a loop; paging, no finding", call["file"],
                      call["line"], call["var"], call["method"], limit)
            continue
        where = "%s loop at line %d" % (kind, loop_line) if not kind.startswith(".") else \
            "%s(...) lambda at line %d" % (kind, loop_line)
        if call.get("subject"):
            where += " over %s" % call["subject"]
        f = _finding("qq.n-plus-one", "high", s, "one query per item (N+1): %s.%s runs inside the %s" %
                     (call["var"], call["method"], where),
                     {"call": "%s.%s" % (call["var"], call["method"]), "dao_method": "%s.%s" % (s["dao"], s["method"]),
                      "loop_line": loop_line, "loop_kind": kind, "caller": "%s.%s" % (call["caller_type"],
                                                                                      call["caller_method"]),
                      "statement_file": s["file"], "statement_line": s["line"], "sql": s["sql"]},
                     file=call["file"], line=call["line"])
        f["_call"] = "%s.%s" % (call["var"], call["method"])
        f["_ctx"] = (call["caller_type"], call["caller_method"], call["caller_anns"])
        out.append(f)
    return out


def _reached(type_name, helper, dao_calls, helpers, registry, depth=1, seen=None):
    """Read DAO calls reachable from `type_name.helper` through same-class helpers (depth <= 3).

    Returns [(call, statement, via)] where via is the helper chain that leads to the call.
    """
    seen = (seen or set()) | {helper}
    out = []
    for call in dao_calls.get((type_name, helper), []):
        reads = [s for s in registry.get(call["type"], {}).get(call["method"], []) if is_small_read(s)]
        if reads:
            out.append((call, reads[0], [helper]))
    if depth < 3:
        for h in helpers.get((type_name, helper), []):
            if h["name"] not in seen:
                for call, stmt, via in _reached(type_name, h["name"], dao_calls, helpers, registry, depth + 1, seen):
                    out.append((call, stmt, [helper] + via))
    return out


def helper_n_plus_one(helper_calls, calls, registry):
    """A loop or lambda that calls a same-class helper which (transitively) runs DAO reads."""
    dao_calls, helpers, out = {}, {}, []
    for c in calls:
        dao_calls.setdefault((c["caller_type"], c["caller_method"]), []).append(c)
    for h in helper_calls:
        helpers.setdefault((h["caller_type"], h["caller_method"]), []).append(h)
    for h in helper_calls:
        if h["loop"] is None or any(w in h["loop"][4] for w in BATCH_WORDS):
            continue
        reached = _reached(h["caller_type"], h["name"], dao_calls, helpers, registry)
        if not reached:
            log.debug("helper call %s:%d %s in loop reaches no DAO read; no finding", h["file"], h["line"], h["name"])
            continue
        nested = [r for r in reached if r[0]["loop"] is not None]
        call, stmt, _via = (nested or reached)[0]
        subject = h.get("subject") or "the loop at line %d" % h["loop"][2]
        names = []
        for c, _s, _v in reached:
            label = "%s.%s" % (c["var"], c["method"])
            if label not in names:
                names.append(label)
        if nested:
            inner = nested[0][0]
            summary = "nested N+1: for every item in %s, %s runs %s; %s.%s runs once per item in %s" % (
                subject, h["name"], ", ".join(names), inner["var"], inner["method"],
                inner.get("subject") or "its loop at line %d" % inner["loop"][2])
        else:
            summary = "one query per item (N+1): for every item in %s, %s runs %s" % (subject, h["name"], ", ".join(names))
        f = _finding("qq.n-plus-one", "high", stmt, summary,
                     {"call": h["name"], "helper": "%s.%s" % (h["caller_type"], h["name"]), "dao_calls": names,
                      "nested": bool(nested), "loop_line": h["loop"][2], "loop_kind": h["loop"][3],
                      "caller": "%s.%s" % (h["caller_type"], h["caller_method"]),
                      "reaches": [{"call": "%s.%s" % (c["var"], c["method"]), "file": c["file"], "line": c["line"],
                                   "in_loop_line": c["loop"][2] if c["loop"] else None, "via": v,
                                   "statement_id": s["id"]} for c, s, v in reached],
                      "statement_file": stmt["file"], "statement_line": stmt["line"], "sql": stmt["sql"]},
                     file=h["file"], line=h["line"])
        f["_call"] = "%s.%s" % (h["caller_type"], h["name"])
        f["_ctx"] = (h["caller_type"], h["caller_method"], h["caller_anns"])
        f["_nested"] = bool(nested)
        out.append(f)
    return out
