"""MySQL rules for FORMAT=JSON (table nodes) and FORMAT=TREE (operator lines)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .common import Node, fint, log, loops_of, num
from .mysql import MY_NODE_KEYS
from .rules import hot_nodes, mk, nested_loop_heavy

MY_TREE_INDEX_SCAN = re.compile(r"^Index (range )?scan on ")


def my_tables_under(n: Node) -> List[Node]:
    out = [n] if n.op == "table" else []
    for c in n.children:
        out += my_tables_under(c)
    return out


def my_rows(n: Node) -> Optional[float]:
    return n.est_rows if n.est_rows is not None else n.actual_rows


def my_full_scan(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    scan = n.raw.get("access_type") == "ALL" if n.op == "table" else n.op.startswith("Table scan on")
    rows = my_rows(n)
    if not scan or rows is None or rows < 10_000:
        return None
    return mk("high" if rows >= 100_000 else "medium", "my.full-scan", n,
              f"full table scan of {n.relation}, {fint(rows)} rows per scan; possible_keys "
              f"{n.raw.get('possible_keys') or 'none'}, key {n.raw.get('key') or 'none'}" if n.op == "table"
              else f"full table scan of {n.relation}, est {fint(rows)} rows",
              {"rows": rows, "possible_keys": n.raw.get("possible_keys"), "key": n.raw.get("key")})


def my_index_scan_full(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    scan = n.raw.get("access_type") == "index" if n.op == "table" else n.op.startswith("Index scan on")
    rows = my_rows(n)
    if not scan or rows is None or rows < 10_000:
        return None
    if my_index_scan_filter(n, ctx) is not None:
        log.debug("my.index-scan-full path=%s: skipped, my.index-scan-filter covers this node", n.path)
        return None
    return mk("medium", "my.index-scan-full", n, f"full index scan, {fint(rows)} rows", {"rows": rows})


def my_tree_index_scan_filter(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """TREE plan: an index scan under a Filter that keeps <= 5% of what the scan reads."""
    if not MY_TREE_INDEX_SCAN.match(n.op):
        return None
    parent = ctx["parent"].get(n.path)
    if parent is None or not parent.op.startswith("Filter:"):
        log.debug("my.index-scan-filter path=%s: no (parent is %r, not Filter:)", n.path, parent and parent.op)
        return None
    reverse = "(reverse)" in n.raw.get("line", "")
    if n.actual_rows is not None and parent.actual_rows is not None:
        estimated = False
        read, kept = n.actual_rows * loops_of(n), parent.actual_rows * loops_of(parent)
        fired = read >= 1000 and kept <= 0.05 * read
        detail = f"{fint(read)} rows read, {fint(kept)} kept"
    else:
        # Without ANALYZE the row counts are LIMIT-scaled estimates; a reverse walk under a Limit is the shape.
        estimated = True
        read, kept = n.est_rows, parent.est_rows
        fired = reverse and n.path in ctx["under_limit"]
        detail = f"{fint(read)} rows/scan is a LIMIT-scaled estimate"
    filtered = kept / read * 100 if read and kept is not None else None
    log.debug("my.index-scan-filter path=%s: read=%s kept=%s estimated=%s reverse=%s under_limit=%s -> %s", n.path,
              read, kept, estimated, reverse, n.path in ctx["under_limit"], "fire" if fired else "no")
    if not fired:
        return None
    pct = "?" if filtered is None else f"{filtered:.2f}"
    cond = parent.op[len("Filter:"):].strip()
    return mk("medium", "my.index-scan-filter", n,
              f"ordered index walk on {n.index} filtering {pct}% ({detail}); worst case reads the whole index",
              {"key": n.index, "filtered_pct": filtered, "rows_read": read, "rows_kept": kept,
               "estimated": estimated, "attached_condition": cond, "backward_index_scan": reverse})


def my_index_scan_filter(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.op != "table":
        return my_tree_index_scan_filter(n, ctx)
    if n.raw.get("access_type") != "index":
        return None
    cond, filtered = n.raw.get("attached_condition"), num(n.raw.get("filtered"))
    if not cond or filtered is None or filtered > 5:
        log.debug("my.index-scan-filter path=%s: no (attached_condition=%s, filtered=%s)", n.path, bool(cond),
                  filtered)
        return None
    evidence: Dict[str, Any] = {"key": n.index, "filtered_pct": filtered, "rows_examined_per_scan": n.est_rows,
                                "attached_condition": cond}
    if "backward_index_scan" in n.raw:
        evidence["backward_index_scan"] = n.raw["backward_index_scan"]
    return mk("medium", "my.index-scan-filter", n,
              f"ordered index walk on {n.index} filtering {filtered:.2f}% ({fint(n.est_rows)} rows/scan is a "
              f"LIMIT-scaled estimate); worst case reads the whole index", evidence)


def my_low_filter(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    filtered = num(n.raw.get("filtered"))
    if n.op != "table" or filtered is None or filtered > 10 or (n.est_rows or 0) < 1000:
        return None
    return mk("medium", "my.low-filter", n,
              f"filtered {filtered:.1f}% of {fint(n.est_rows)} rows examined; condition "
              f"{n.raw.get('attached_condition')}",
              {"filtered_pct": filtered, "rows_examined_per_scan": n.est_rows,
               "attached_condition": n.raw.get("attached_condition")})


def my_filesort(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.engine != "mysql":
        return None
    if n.op == "table" or n.op in MY_NODE_KEYS:
        if not n.raw.get("using_filesort"):
            return None
        rows = max((num(t.raw.get("rows_produced_per_join")) or 0.0 for t in my_tables_under(n)), default=0.0)
    elif n.op.startswith("Sort"):
        src = n.children[0] if n.children else n
        rows = src.actual_rows * loops_of(src) if src.actual_rows is not None else (my_rows(src) or 0.0)
    else:
        return None
    return mk("medium" if rows >= 10_000 else "low", "my.filesort", n, f"filesort over {fint(rows)} input rows",
              {"input_rows": rows})


def my_temp_table(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    hit = n.raw.get("using_temporary_table") is True if n.op in MY_NODE_KEYS else "temporary table" in n.op.lower()
    if not hit or n.op == "table":
        return None
    return mk("medium", "my.temp-table", n, f"temporary table in {n.op}", {"operation": n.op})


def my_join_buffer(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    jb = n.raw.get("using_join_buffer") if n.op == "table" else None
    if jb is None and n.op != "table" and ("hash join" in n.op.lower() or "block nested loop" in n.op.lower()):
        jb = n.op
    if not jb:
        return None
    return mk("medium", "my.join-buffer", n, f"join buffer: {jb}", {"using_join_buffer": jb})


def my_key_unused(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pk = n.raw.get("possible_keys")
    if n.op != "table" or not pk or n.raw.get("key"):
        return None
    return mk("medium", "my.key-unused", n, f"possible_keys {pk} but no key chosen", {"possible_keys": pk})


def my_not_covering(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key, produced = n.raw.get("key"), num(n.raw.get("rows_produced_per_join"))
    if (n.op != "table" or n.raw.get("access_type") not in ("ref", "range") or not key or key == "PRIMARY"
            or n.raw.get("using_index") or produced is None):
        return None
    return mk("medium" if produced >= 10_000 else "low", "my.not-covering", n,
              f"{n.raw.get('access_type')} on secondary index {key} without covering, {fint(produced)} rows produced",
              {"key": key, "rows_produced_per_join": produced, "used_columns": n.raw.get("used_columns")})


def my_nested_loop_heavy(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not n.op.lower().startswith("nested loop") or not n.children:
        return None
    inner = max(n.children, key=loops_of)
    return nested_loop_heavy("my.nested-loop-heavy", n, inner, ctx, {"inner_path": inner.path})


def my_plan_rules(nodes: List[Node], meta: Dict[str, Any], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not meta["analyze"]:
        log.debug("rule my.no-analyze -> FIRED low")
        return [mk("low", "my.no-analyze", None, "Estimates only. Capture EXPLAIN ANALYZE (FORMAT=TREE) when it "
                   "is safe to execute: it runs the statement.", {})]
    return hot_nodes(nodes, meta["execution"], "my.hot-node", ctx)
