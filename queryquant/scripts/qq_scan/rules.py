"""First-pass static rules. Each finding: rule, severity, service, file, line, statement_id, summary,
evidence, evidence_level ("from code"), needs_table_size.

Index model (MySQL/InnoDB): the primary key plus every secondary index; a secondary index also carries
the primary key columns after its own (InnoDB appends them), which matters for ORDER BY.
  qq.no-index-for-filter  a table's WHERE has equality / IN / range columns but no index (or primary
                          key) starts with any of them or with one of its join columns. Table missing
                          from the schema: low finding.
  qq.no-index-for-sort    ORDER BY on the first table, with LIMIT or a List result, and no index whose
                          leading columns are (equality columns, in any order) then the sort columns in
                          order, all one direction. Skipped with GROUP BY or ORDER BY expressions.
  qq.catch-all-filter     (:x IS NULL OR col ...) or COALESCE(:x, col) = col.
  qq.offset-paging        OFFSET (or LIMIT a, b) with a bind value or a literal >= 1000.
  qq.unbounded-list       SELECT returning List/Stream, no LIMIT, and no equality/IN covering a primary
                          or unique key (high when there is no WHERE at all). Skipped when the method
                          name reads as parent-scoped (`...For|Of|In<X>`, `...By...Id|Guid|Uuid`) or
                          every equality/IN filter is a parent key (`*_id`, `*_guid`, `*_uuid`).
  qq.n-plus-one           see rules_calls.py.
  qq.unchunked-batch      @SqlBatch with no @BatchChunkSize on the method or its interface.
  qq.tenant-column-not-leading  (config leading_column) a secondary index on a table that has that
                          column, whose first column is a different one.
"""
import logging
import re

log = logging.getLogger("qq_scan.rules")

STRUCTURAL = ("qq.n-plus-one", "qq.catch-all-filter", "qq.unbounded-list", "qq.unchunked-batch",
              "qq.offset-paging")


def _finding(rule, severity, stmt, summary, evidence, needs_size=False, file=None, line=None, service=None):
    f = {"rule": rule, "severity": severity, "service": service or stmt["service"],
         "file": file or stmt["file"], "line": line or stmt["line"],
         "statement_id": stmt["id"] if stmt else None, "summary": summary, "evidence": evidence,
         "evidence_level": "from code", "needs_table_size": needs_size}
    log.debug("finding %s %s %s:%s %s", rule, severity, f["file"], f["line"], summary)
    return f


def _indexes(table):
    pk = list(table["primary_key"])
    out = [("PRIMARY", pk, True)] if pk else []
    for idx in table["indexes"]:
        out.append((idx["name"], idx["columns"] + [c for c in pk if c not in idx["columns"]], idx["unique"]))
    return out


def _index_text(table):
    return ["%s(%s)" % (n, ", ".join(c)) for n, c, _u in
            ([("PRIMARY", table["primary_key"], True)] if table["primary_key"] else []) +
            [(i["name"], i["columns"], i["unique"]) for i in table["indexes"]]]


def _tables(shape, schema):
    """[(alias, name, table-or-None)] for the statement's real tables."""
    return [(t["alias"], t["name"], schema.get(t["name"])) for t in shape["tables"] if not t.get("derived")]


def _owner(pred_alias, column, tables):
    if pred_alias:
        for alias, name, table in tables:
            if pred_alias in (alias, name):
                return alias, name, table
        return None
    if len(tables) == 1:
        return tables[0]
    hits = [t for t in tables if t[2] is not None and column in t[2]["columns"]]
    return hits[0] if len(hits) == 1 else None


def _preds_by_table(shape, tables):
    """{table: {table, eq, in, range, other, join}}; join holds columns used in join conditions."""
    out = {}

    def add(alias, column, bucket):
        owner = _owner(alias, column, tables)
        if owner is None:
            log.debug("cannot attribute column %s.%s to a table", alias, column)
            return
        cols = out.setdefault(owner[1], {"table": owner[2], "eq": [], "in": [], "range": [], "other": [],
                                         "join": []})
        if column not in cols[bucket]:
            cols[bucket].append(column)

    for p in shape["preds"]:
        add(p["alias"], p["column"],
            p["op"] if p["op"] in ("eq", "in", "range") else ("range" if p["op"] == "like" else "other"))
    for p in shape["join_preds"]:
        add(p["alias"], p["column"], "join")
        if p.get("rhs_ref"):
            add(p["rhs_ref"][0], p["rhs_ref"][1], "join")
    return out


def no_index_for_filter(stmt, shape, schema):
    if shape["kind"] not in ("select", "update", "delete") or not shape["has_where"]:
        return []
    found = []
    for name, cols in _preds_by_table(shape, _tables(shape, schema)).items():
        usable = cols["eq"] + cols["in"] + cols["range"]
        if not usable:
            continue
        if cols["table"] is None:
            found.append(_finding("qq.no-index-for-filter", "low", stmt,
                                  "table %s is not in this service's Liquibase schema; index use unknown" % name,
                                  {"table": name, "table_found": False, "note": "table not found",
                                   "filter_columns": usable}))
            continue
        hits = [n for n, c, _u in _indexes(cols["table"]) if c and c[0] in usable + cols["join"]]
        log.debug("filter %s on %s: eq=%s in=%s range=%s usable indexes=%s", stmt["id"], name, cols["eq"],
                  cols["in"], cols["range"], hits)
        if not hits:
            found.append(_finding(
                "qq.no-index-for-filter", "medium", stmt,
                "no index on %s starts with %s; MySQL reads the whole table" % (name, " or ".join(usable)),
                {"table": name, "equality_columns": cols["eq"], "in_columns": cols["in"],
                 "range_columns": cols["range"], "indexes": _index_text(cols["table"])}, needs_size=True))
    return found


def no_index_for_sort(stmt, shape, schema):
    if shape["kind"] != "select" or not shape["order_by"] or shape["order_expr"] or shape["group_by"]:
        return []
    if shape["limit"] is None and stmt["returns"] != "List":
        return []
    tables = _tables(shape, schema)
    owners = {(_owner(a, c, tables) or (None, None, None))[1] for a, c, _d in shape["order_by"]}
    if len(owners) != 1 or None in owners or not tables or tables[0][1] not in owners:
        log.debug("sort %s: sort columns not all on the first table (%s); skipped", stmt["id"], owners)
        return []
    name, table = tables[0][1], tables[0][2]
    if table is None or any(c not in table["columns"] for _a, c, _d in shape["order_by"]):
        log.debug("sort %s: table %s not found or sorts by a select alias; skipped", stmt["id"], name)
        return []
    eq = _preds_by_table(shape, tables).get(name, {}).get("eq", [])
    if any(u and c and set(c) <= set(eq) for _n, c, u in _indexes(table)):
        log.debug("sort %s: equality covers a unique key; one row, skipped", stmt["id"])
        return []
    sort = [(c, d) for _a, c, d in shape["order_by"] if c not in eq]
    if not sort:
        return []
    dirs = {d for _c, d in sort}
    want = [c for c, _d in sort]
    for idx_name, cols, _u in _indexes(table):
        k = 0
        while k < len(cols) and cols[k] in eq:
            k += 1
        if len(dirs) == 1 and cols[k:k + len(want)] == want:
            log.debug("sort %s served by %s", stmt["id"], idx_name)
            return []
    return [_finding("qq.no-index-for-sort", "medium", stmt,
                     "no index on %s matches ORDER BY %s; MySQL sorts every matching row" %
                     (name, ", ".join("%s %s" % (c, d.upper()) for c, d in sort)),
                     {"table": name, "equality_columns": eq, "order_by": ["%s %s" % s for s in sort],
                      "mixed_directions": len(dirs) > 1, "limit": shape["limit"],
                      "indexes": _index_text(table)}, needs_size=True)]


def catch_all(stmt, shape, _schema):
    if not shape["catch_all"]:
        return []
    cols = sorted({c for ca in shape["catch_all"] for c in ca["columns"]})
    return [_finding("qq.catch-all-filter", "high", stmt,
                     "optional filter (:x IS NULL OR col = :x) on %s; MySQL cannot plan an index for it" %
                     ", ".join(cols),
                     {"patterns": shape["catch_all"], "columns": cols})]


def offset_paging(stmt, shape, _schema):
    off = shape["offset"]
    if shape["kind"] != "select" or off is None:
        return []
    if not (off.startswith(":") or off == "?" or (off.isdigit() and int(off) >= 1000)):
        log.debug("offset %s: small literal offset %s; skipped", stmt["id"], off)
        return []
    return [_finding("qq.offset-paging", "medium", stmt,
                     "OFFSET paging; every page re-reads and discards all earlier rows",
                     {"limit": shape["limit"], "offset": off})]


PARENT_KEY_SUFFIXES = ("_id", "_guid", "_uuid")
_BOUNDED_NAME = re.compile(r"(For|Of|In)[A-Z]|By\w*(Id|ID|Guid|GUID|Uuid|UUID)s?$")


def unbounded_list(stmt, shape, schema):
    if shape["kind"] != "select" or stmt["returns"] not in ("List", "Stream") or shape["limit"] is not None:
        return []
    names = [t["name"] for t in shape["tables"] if not t.get("derived")]
    if not shape["has_where"]:
        return [_finding("qq.unbounded-list", "high", stmt,
                         "returns every row of %s (no WHERE, no LIMIT)" % ", ".join(names),
                         {"tables": names, "where": False, "limit": None})]
    tables = _tables(shape, schema)
    by_table = _preds_by_table(shape, tables)
    unknown = False
    for name, cols in by_table.items():
        if cols["table"] is None:
            unknown = unknown or bool(cols["eq"] or cols["in"])
            continue
        keyed = set(cols["eq"] + cols["in"])
        uniques = ([cols["table"]["primary_key"]] if cols["table"]["primary_key"] else []) + \
            [i["columns"] for i in cols["table"]["indexes"] if i["unique"]]
        if any(c and set(c) <= keyed for c in uniques):
            log.debug("unbounded %s: equality covers a unique key of %s; bounded", stmt["id"], name)
            return []
    if unknown:
        log.debug("unbounded %s: filtered table missing from schema; skipped", stmt["id"])
        return []
    if stmt["method"] and _BOUNDED_NAME.search(stmt["method"]):
        log.debug("unbounded %s: method name %s suggests a bounded set; skipped", stmt["id"], stmt["method"])
        return []
    keys = [c for cols in by_table.values() for c in cols["eq"] + cols["in"]]
    if keys and all(c.endswith(PARENT_KEY_SUFFIXES) for c in keys):
        log.debug("unbounded %s: filters only on parent keys %s; bounded", stmt["id"], keys)
        return []
    filters = sorted({c for cols in by_table.values() for k in ("eq", "in", "range", "other") for c in cols[k]})
    indexed = sorted({c for cols in by_table.values() if cols["table"] for c in cols["eq"] + cols["in"]
                      if any(ix and ix[0] == c for _n, ix, _u in _indexes(cols["table"]))})
    why = ("filters only on non-unique %s" % ", ".join(indexed)) if indexed else "no filter on an indexed key"
    return [_finding("qq.unbounded-list", "medium", stmt,
                     "returns a %s with no LIMIT and %s; can return a large share of %s" %
                     (stmt["returns"], why, ", ".join(names)),
                     {"tables": names, "where": True, "filter_columns": filters,
                      "non_unique_indexed_filters": indexed, "limit": None})]


def unchunked_batch(stmt, _shape, _schema):
    if stmt.get("_ann") != "SqlBatch" or "@BatchChunkSize" in stmt["annotations"] \
            or "@BatchChunkSize" in stmt.get("_type_anns", []):
        return []
    return [_finding("qq.unchunked-batch", "medium", stmt,
                     "@SqlBatch without @BatchChunkSize sends the whole list as one batch",
                     {"annotations": stmt["annotations"], "interface_annotations": stmt.get("_type_anns", [])})]


STATEMENT_RULES = (no_index_for_filter, no_index_for_sort, catch_all, offset_paging, unbounded_list)


def tenant_column(service, schema, leading):
    """Config rule: every secondary index on a table with `leading['column']` should start with it."""
    col, out = leading["column"], []
    for name, table in sorted(schema.tables.items()):
        if col not in table["columns"]:
            continue
        for idx in table["indexes"]:
            if idx["columns"] and idx["columns"][0] != col:
                rel, line = idx.get("src", (None, None))
                f = _finding("qq.tenant-column-not-leading", leading["severity"], None,
                             "index %s on %s starts with %s, not %s" % (idx["name"], name, idx["columns"][0], col),
                             {"table": name, "index": idx["name"], "columns": idx["columns"], "expected_first": col},
                             file=rel, line=line, service=service)
                f["_call"] = "%s.%s" % (name, idx["name"])
                out.append(f)
    return out
