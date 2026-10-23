"""PostgreSQL rules (FORMAT JSON and text plans share them: the text parser maps onto the JSON keys)."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from .common import Node, fint, fms, log, loops_of, num
from .rules import hot_nodes, mk, nested_loop_heavy


def own_blocks(n: Node, key: str) -> Optional[float]:
    # Postgres buffer counters include all children; subtract them so only the node doing the I/O reports it.
    v = num(n.raw.get(key))
    if v is None:
        return None
    return max(0.0, v - sum(num(c.raw.get(key)) or 0.0 for c in n.children))


def pg_seq_scan_filtered(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.op != "Seq Scan":
        return None
    flt = n.raw.get("Filter")
    removed = num(n.raw.get("Rows Removed by Filter"))
    if n.actual_rows is not None and removed is not None:
        total_removed = removed * loops_of(n)
        if removed < 10 * n.actual_rows or total_removed < 10_000:
            return None
        sev = "high" if n.path in ctx["hot"] else "medium"
        return mk(sev, "pg.seq-scan-filtered", n,
                  f"filter removed {fint(total_removed)} rows, kept {fint(n.actual_rows * loops_of(n))}; filter {flt}",
                  {"rows_removed": total_removed, "actual_rows": n.actual_rows * loops_of(n), "filter": flt})
    if n.actual_rows is None and flt and (n.est_rows or 0) >= 10_000:
        return mk("medium", "pg.seq-scan-filtered", n, f"est {fint(n.est_rows)} rows after filter {flt}",
                  {"est_rows": n.est_rows, "filter": flt})
    return None


def pg_nested_loop_heavy(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.op != "Nested Loop" or len(n.children) < 2:
        return None
    inner = next((c for c in n.children if c.raw.get("Parent Relationship") == "Inner"), n.children[1])
    return nested_loop_heavy("pg.nested-loop-heavy", n, inner, ctx,
                             {"inner_path": inner.path, "inner_type": inner.op, "inner_index": inner.index})


def pg_sort_disk(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.op != "Sort":
        return None
    method, stype = str(n.raw.get("Sort Method", "")), n.raw.get("Sort Space Type")
    if "external" not in method.lower() and stype != "Disk":
        return None
    space = num(n.raw.get("Sort Space Used"))
    rows = n.actual_rows * loops_of(n) if n.actual_rows is not None else None
    return mk("high", "pg.sort-disk", n, f"Sort Method {method}, {fint(space)} kB on disk; {fint(rows)} rows",
              {"sort_method": method, "sort_space_type": stype, "sort_space_kb": space, "rows": rows})


def pg_hash_batches(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    batches, orig = num(n.raw.get("Hash Batches")), num(n.raw.get("Original Hash Batches"))
    if n.op != "Hash" or batches is None or (batches <= 1 and (orig is None or orig == batches)):
        return None
    sev = "high" if batches >= 8 else "medium"
    peak = num(n.raw.get("Peak Memory Usage"))
    return mk(sev, "pg.hash-batches", n,
              f"Hash Batches {fint(batches)} (planned {fint(orig)}), peak memory {fint(peak)} kB",
              {"hash_batches": batches, "original_hash_batches": orig, "peak_memory_kb": peak})


def pg_heap_fetches(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    hf = num(n.raw.get("Heap Fetches"))
    if n.op != "Index Only Scan" or not hf or n.actual_rows is None:
        return None
    rows = n.actual_rows * loops_of(n)
    if hf < 0.1 * rows:
        return None
    return mk("medium", "pg.heap-fetches", n,
              f"{fint(hf)} heap fetches for {fint(rows)} rows ({hf / max(rows, 1) * 100:.0f}%)",
              {"heap_fetches": hf, "rows": rows})


def pg_index_recheck(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rr = num(n.raw.get("Rows Removed by Index Recheck"))
    if n.op != "Bitmap Heap Scan" or not rr or n.actual_rows is None or rr < n.actual_rows:
        return None
    lossy = num(n.raw.get("Lossy Heap Blocks"))
    sev = "medium" if n.path in ctx["hot"] or (lossy or 0) > 0 else "low"
    log.debug("rule pg.index-recheck path=%s: removed=%s kept=%s lossy=%s hot=%s", n.path, rr, n.actual_rows, lossy,
              n.path in ctx["hot"])
    summary = f"recheck removed {fint(rr)} rows/loop, kept {fint(n.actual_rows)}"
    if lossy:
        summary += f"; {fint(lossy)} lossy heap blocks: lossy bitmap (work_mem)"
    return mk(sev, "pg.index-recheck", n, summary,
              {"rows_removed_by_recheck": rr, "actual_rows": n.actual_rows, "lossy_heap_blocks": lossy,
               "exact_heap_blocks": num(n.raw.get("Exact Heap Blocks"))})


def pg_buffers_read(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    read, hit = own_blocks(n, "Shared Read Blocks"), own_blocks(n, "Shared Hit Blocks")
    temp_w, temp_r = own_blocks(n, "Temp Written Blocks"), own_blocks(n, "Temp Read Blocks")
    if not ((read or 0) >= 10_000 or (temp_w or 0) > 0):
        return None
    parts = [f"shared read {fint(read)} blocks"] if read else []
    if read is not None and hit is not None and read + hit > 0:
        parts.append(f"hit ratio {hit / (hit + read) * 100:.1f}%")
    if temp_w:
        parts.append(f"temp written {fint(temp_w)} / read {fint(temp_r)} blocks")
    return mk("high" if temp_w else "medium", "pg.buffers-read", n, ", ".join(parts) + " (this node only)",
              {"shared_read": read, "shared_hit": hit, "temp_written": temp_w, "temp_read": temp_r})


def pg_parallel_shortfall(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    wp, wl = num(n.raw.get("Workers Planned")), num(n.raw.get("Workers Launched"))
    if wp is None or wl is None or wl >= wp:
        return None
    return mk("low", "pg.parallel-shortfall", n, f"launched {fint(wl)} of {fint(wp)} planned workers",
              {"workers_planned": wp, "workers_launched": wl})


def pg_plan_rules(nodes: List[Node], meta: Dict[str, Any], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not meta["analyze"]:
        out.append(mk("low", "pg.no-analyze", None, "Estimates only. Capture EXPLAIN (ANALYZE, BUFFERS) when it is "
                      "safe to execute (SELECT, or DML inside BEGIN…ROLLBACK).", {}))
        log.debug("rule pg.no-analyze -> FIRED low")
        return out
    total = ctx["total"]  # set by analyse(): Execution Time, else the top node's time x loops
    out += hot_nodes(nodes, total, "pg.hot-node", ctx)
    exec_ms = meta["execution"]
    for t in meta["triggers"]:
        tt = num(t.get("Time")) if isinstance(t, dict) else None
        fired = bool(exec_ms and tt is not None and tt >= 0.1 * exec_ms)
        log.debug("rule pg.trigger-time trigger=%s time=%s -> %s", t, tt, "FIRED medium" if fired else "no")
        if fired:
            out.append(mk("medium", "pg.trigger-time", None,
                          f"trigger {t.get('Trigger Name')} on {t.get('Relation')}: {fms(tt)} ms over "
                          f"{fint(num(t.get('Calls')))} calls, {tt / exec_ms * 100:.1f}% of execution",
                          {"trigger": t.get("Trigger Name"), "time_ms": tt, "calls": num(t.get("Calls")),
                           "execution_ms": exec_ms}))
    return out


# ---------------------------------------------------------------- generic plans, partition pruning, N+1

BIND_PARAM = re.compile(r"\$\d+\b")
# A function or cast applied to a column: the shapes that stop the planner pruning on that column. The cast
# alternative needs a column-like name right before "::" (a constant cast ends in a quote, "'...'::date").
PRUNE_BLOCKER = re.compile(r"\b(?:date_trunc|date_part|extract|to_char|timezone|date)\s*\("
                           r"|(?<![:\w.'])[a-z_][\w.]*\)?::", re.IGNORECASE)
PARTITION_ALIAS = re.compile(r"^(?P<parent>.+)_\d+$")
LOOKUP_BY_KEY = re.compile(r"\bwhere\b.*?[a-z_][\w.\"]*\s*=\s*\$\d+", re.IGNORECASE | re.DOTALL)
SET_LOOKUP = re.compile(r"\bany\s*\(|\bin\s*\(", re.IGNORECASE)
GENERIC_MISESTIMATE_HINT = "Estimate is for an average parameter value (generic plan); see pg.generic-plan."


def pg_generic_plan(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    conds = {k: n.raw[k] for k in ("Index Cond", "Filter", "Recheck Cond")
             if BIND_PARAM.search(str(n.raw.get(k) or ""))}
    if not conds:
        return None
    if n.est_rows is None or n.actual_rows is None or n.actual_rows * loops_of(n) < 100:
        log.debug("rule pg.generic-plan path=%s: bind parameter in %s but no ANALYZE rows to compare", n.path,
                  sorted(conds))
        return None
    r = n.est_rows / max(n.actual_rows, 1.0)
    # Same misestimate test as pg.row-misestimate, including the Limit guard on over-estimates.
    if 0.1 < r < 10 or (r >= 10 and n.path in ctx["under_limit"]):
        log.debug("rule pg.generic-plan path=%s: bind parameter but estimate holds (ratio %.4g)", n.path, r)
        return None
    factor = r if r >= 1 else (1 / r if r > 0 else float("inf"))
    return mk("high", "pg.generic-plan", n,
              f"generic (prepared-statement) plan: {n.label} was planned for an average parameter value and "
              f"misestimates this one {factor:,.0f}x",
              {"conditions": conds, "est_rows": n.est_rows, "actual_rows": n.actual_rows, "loops": n.loops,
               "est_ratio": r})


def partition_parent(scans: List[Node]) -> Optional[str]:
    """Parent table name shared by partition scans: from aliases "<parent>_<n>", else the relations' common prefix."""
    parents = {m.group("parent") for m in (PARTITION_ALIAS.match(str(c.raw.get("Alias") or "")) for c in scans) if m}
    if len(parents) == 1 and all(c.relation.startswith(next(iter(parents)) + "_") for c in scans):
        return next(iter(parents))
    rels = [c.relation for c in scans]
    prefix = os.path.commonprefix(rels)
    prefix = prefix[: prefix.rfind("_")] if "_" in prefix else ""
    return prefix if prefix and all(r.startswith(prefix + "_") for r in rels) else None


def pg_no_partition_pruning(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.op not in ("Append", "Merge Append"):
        return None
    scans = [c for c in n.children if c.relation]
    parent = partition_parent(scans) if len({c.relation for c in scans}) >= 4 else None
    removed = num(n.raw.get("Subplans Removed"))
    filters = [str(c.raw["Filter"]) for c in scans
               if c.raw.get("Filter") and PRUNE_BLOCKER.search(str(c.raw["Filter"]))]
    log.debug("rule pg.no-partition-pruning path=%s: %d scans, parent=%s, subplans_removed=%s, blocking filters=%d",
              n.path, len(scans), parent, removed, len(filters))
    if parent is None or removed or not filters:
        return None
    with_rows = None if any(c.actual_rows is None for c in scans) else sum(1 for c in scans if c.actual_rows > 0)
    return mk("high", "pg.no-partition-pruning", n,
              f"no partition pruning: {len(scans)} partitions of {parent} scanned, {fint(with_rows)} of them returned "
              f"rows; filter {filters[0]}",
              {"parent": parent, "partitions_scanned": len(scans), "partitions_with_rows": with_rows,
               "subplans_removed": removed, "filter": filters[0]})


def pg_fold_findings(nodes: List[Node], findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-point misestimate hints at pg.generic-plan, and fold partition seq-scan findings into pruning."""
    generic = {f["node"]["path"] for f in findings if f["rule"] == "pg.generic-plan"}
    for f in findings:
        if f["rule"] == "pg.row-misestimate" and f["node"]["path"] in generic:
            f["hint"] = GENERIC_MISESTIMATE_HINT
            log.debug("fold: pg.row-misestimate path=%s hint -> generic-plan hint", f["node"]["path"])
    dropped: set = set()
    for p in (f for f in findings if f["rule"] == "pg.no-partition-pruning"):
        path = p["node"]["path"]
        folded = [f for f in findings if f["rule"] == "pg.seq-scan-filtered"
                  and f["node"]["path"].rsplit(".", 1)[0] == path]
        dropped |= {id(f) for f in folded}
        if folded:
            p["summary"] += f"; {len(folded)} partition scans folded into pg.no-partition-pruning"
            p["evidence"]["folded_seq_scan_findings"] = len(folded)
        log.debug("fold: %d pg.seq-scan-filtered findings under Append path=%s folded into pg.no-partition-pruning",
                  len(folded), path)
    return [f for f in findings if id(f) not in dropped]


def pg_statement_rules(nodes: List[Node], meta: Dict[str, Any], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """pg_stat_statements input: pg.n-plus-one over the statement rows."""
    top3 = {n.path for n in sorted(nodes, key=lambda n: n.raw["total_ms"] or 0.0, reverse=True)[:3]}
    out: List[Dict[str, Any]] = []
    for n in nodes:
        calls, mean, total, rows, query = (n.raw[k] for k in ("calls", "mean_ms", "total_ms", "rows", "query"))
        per_call = rows / calls if rows is not None and calls else None
        checks = {"calls>=1000": calls >= 1000, "mean<=5ms": mean is not None and mean <= 5,
                  "rows/call<=100": per_call is not None and per_call <= 100, "top3_total": n.path in top3,
                  "key_lookup": bool(LOOKUP_BY_KEY.search(query)) and not SET_LOOKUP.search(query)}
        log.debug("rule pg.n-plus-one path=%s %s", n.path, checks)
        if not all(checks.values()):
            continue
        out.append(mk("high", "pg.n-plus-one", n,
                      f"{fint(calls)} calls at {mean:,.2f} ms each = {fint(total)} ms total; one lookup per parent "
                      f"row (N+1)",
                      {"calls": calls, "mean_ms": mean, "total_ms": total, "rows": rows, "rows_per_call": per_call,
                       "query": query}))
    return out
