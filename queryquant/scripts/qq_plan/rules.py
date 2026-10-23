"""Shared rule pieces (hint table, mk, hot-node, misestimate, nested-loop) and the per-engine registry.

Engine rules live in rules_pg.py, rules_mysql.py and rules_questdb.py."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from .common import Node, fint, fms, fratio, log, loops_of

HINTS = {
    "pg.hot-node": "Most of the runtime is spent in this node; start the investigation here.",
    "pg.row-misestimate": "Stale or insufficient statistics (ANALYZE, default_statistics_target), correlated "
                          "columns (CREATE STATISTICS), or non-analysable predicates (functions, casts, LIKE).",
    "pg.seq-scan-filtered": "Index on the filter columns; a partial index if the predicate is fixed.",
    "pg.nested-loop-heavy": "Inner side needs an index, or the planner misestimated the outer row count "
                            "(see misestimate on the outer).",
    "pg.sort-disk": "work_mem for this query, an index matching ORDER BY so the sort disappears, "
                    "or lower LIMIT fan-out.",
    "pg.hash-batches": "work_mem, or reduce the hashed side with earlier filtering.",
    "pg.heap-fetches": "Visibility map not set: run VACUUM; check autovacuum lag on the table.",
    "pg.index-recheck": "Lossy bitmap (work_mem), or the index cannot resolve the predicate exactly.",
    "pg.buffers-read": "Cold cache or working set larger than shared_buffers; temp blocks mean a spill.",
    "pg.parallel-shortfall": "max_parallel_workers / max_worker_processes were exhausted when this ran.",
    "pg.no-analyze": "Without actual rows and times, hot-node and misestimate rules cannot run.",
    "pg.trigger-time": "Trigger work counts toward the statement; check the trigger body and what it scans.",
    "pg.generic-plan": "The plan cache switched this prepared statement to a generic plan. Compare EXPLAIN EXECUTE "
                       "with a custom plan (SET plan_cache_mode = force_custom_plan); fix with plan_cache_mode for "
                       "this statement or an index that serves every parameter value. ANALYZE alone does not change "
                       "a generic plan's choice.",
    "pg.no-partition-pruning": "Every partition was scanned and the filter applies a function or cast to a column. "
                               "If that column is the partition key, the planner cannot prune: rewrite it as a plain "
                               "range on the key (created_at >= X AND created_at < Y). More indexes will not help. "
                               "If the query has no predicate on the key at all, add one.",
    "pg.n-plus-one": "The plan is fine; the call count is the problem. Batch the lookups into one query with = ANY($1) "
                     "(PostgreSQL) or IN (...) (MySQL) and group the rows in the application. No index needed.",
    "my.full-scan": "No usable index for the join/filter columns; check possible_keys vs key.",
    "my.index-scan-full": "Usually an ORDER BY/GROUP BY satisfied by an index but without a range; "
                          "consider a filter column first.",
    "my.index-scan-filter": "The optimizer walks the ORDER BY index and filters; fast only when matches are "
                            "dense near the start. A composite index with the equality columns first and the "
                            "ORDER BY column last lets the LIMIT stop after N entries for every value.",
    "my.low-filter": "The chosen index is not selective for the attached_condition; consider a composite "
                     "index covering the filter columns.",
    "my.filesort": "An index whose order matches ORDER BY (after the WHERE equality prefix).",
    "my.temp-table": "An ORDER BY, GROUP BY, DISTINCT or UNION step materialised a temporary table; an index whose order matches the step removes it. Large ones spill to disk (tmp_table_size).",
    "my.join-buffer": "The joined table has no usable index on the join column.",
    "my.key-unused": "Optimizer rejected the index: check column types/collations match the join "
                     "(implicit conversion), or statistics (ANALYZE TABLE).",
    "my.not-covering": "Each row costs a primary-key lookup; add the selected columns to the index (covering).",
    "my.hot-node": "Most of the runtime is spent in this node; start the investigation here.",
    "my.row-misestimate": "Stale statistics (ANALYZE TABLE), missing histograms (ANALYZE TABLE ... UPDATE "
                          "HISTOGRAM), or non-sargable predicates (functions, casts, LIKE).",
    "my.nested-loop-heavy": "Inner side needs an index, or the optimizer misestimated the outer row count.",
    "my.no-analyze": "Without actual rows and times, hot-node and misestimate rules cannot run.",
    "qdb.full-frame-scan": "Add a WHERE on the designated timestamp (ts IN '2026-10' / BETWEEN / >=) so the scan "
                           "becomes an Interval scan; otherwise an INDEX on the filtered SYMBOL column.",
    "qdb.non-jit-filter": "The filter did not JIT-compile: SQL functions, in() on a symbol, BETWEEN on a "
                          "non-designated timestamp, or an unsupported column type. Rewrite the predicate so "
                          "arithmetic is only + - * / and functions sit on the constant side.",
    "qdb.single-thread-filter": "Single-threaded filter; check cairo.sql.parallel.filter.enabled and that the "
                                "predicate is parallelisable.",
    "qdb.sort": "Ordering holds the result in RAM. ORDER BY the designated timestamp is a Frame backward scan "
                "with no sort; otherwise add a LIMIT or narrow by time first.",
    "qdb.latest-by-scan": "LATEST ON with multiple keys or without a symbol index scans the whole table; "
                          "single-key LATEST ON stops early.",
    "qdb.hash-join": "If this joins two time series, an ASOF JOIN with TOLERANCE is usually cheaper.",
    "qdb.no-analyze": "Time the query from the client (warm cache, run twice) and watch query_activity().",
}
RULE_WIDTH = max(len(r) for r in HINTS)


def mk(sev: str, rule: str, node: Optional[Node], summary: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    if node is None:
        nd: Dict[str, Any] = {"label": "(whole plan)", "type": None, "relation": None, "index": None, "path": None}
    else:
        nd = {"label": node.label, "type": node.op, "relation": node.relation, "index": node.index,
              "path": node.path}
    ev = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in evidence.items()}
    return {"severity": sev, "rule": rule, "node": nd, "summary": summary, "evidence": ev,
            "hint": HINTS[rule], "_self": node.self_time if node else 0.0}


# ---------------------------------------------------------------- shared rules

def hot_nodes(nodes: List[Node], total: Optional[float], rule: str, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not total:
        log.debug("rule %s skipped: no total runtime", rule)
        return out
    ranked = sorted((n for n in nodes if n.self_time > 0), key=lambda n: n.self_time, reverse=True)[:3]
    for n in ranked:
        pct = n.self_time / total
        sev = "high" if pct >= 0.5 else "medium" if pct >= 0.2 else "low"
        if pct >= 0.2:
            ctx["hot"].add(n.path)
        out.append(mk(sev, rule, n, f"{fms(n.self_time)} ms self time, {pct * 100:.1f}% of total {fms(total)} ms",
                      {"self_time_ms": n.self_time, "total_ms": total, "pct": pct * 100, "loops": n.loops}))
        log.debug("rule %s path=%s -> FIRED %s (%.1f%%)", rule, n.path, sev, pct * 100)
    return out


def misestimate(prefix: str) -> Callable[[Node, Dict[str, Any]], Optional[Dict[str, Any]]]:
    rule = f"{prefix}.row-misestimate"

    def check(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if n.est_rows is None or n.actual_rows is None or n.actual_rows * loops_of(n) < 100:
            return None
        r = n.est_rows / max(n.actual_rows, 1.0)
        if 0.1 < r < 10:
            return None
        if r >= 10 and n.path in ctx["under_limit"]:
            log.debug("rule %s path=%s -> suppressed (over-estimate under Limit)", rule, n.path)
            return None
        sev = "high" if r >= 100 or r <= 0.01 else "medium"
        return mk(sev, rule, n, f"est {fint(n.est_rows)} rows/loop, actual {fint(n.actual_rows)} rows/loop "
                                f"({fratio(r)}), {fint(loops_of(n))} loops",
                  {"est_rows": n.est_rows, "actual_rows": n.actual_rows, "loops": n.loops, "est_ratio": r})
    return check


def nested_loop_heavy(rule: str, n: Node, inner: Node, ctx: Dict[str, Any],
                      evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Inner side of a nested loop: >= 1,000 loops, or >= 20% of the runtime over >= 50 loops."""
    if inner.loops is None:
        log.debug("rule %s path=%s: no (inner %s has no loop count)", rule, n.path, inner.path)
        return None
    spent = inner.total_time * inner.loops if inner.total_time is not None else None
    total = ctx.get("total")
    pct = spent / total if spent is not None and total else None
    by_time = pct is not None and pct >= 0.2 and inner.loops >= 50
    log.debug("rule %s path=%s: inner=%s loops=%s inner_ms=%s total_ms=%s pct=%s by_loops=%s by_time=%s", rule,
              n.path, inner.path, inner.loops, spent, total, None if pct is None else round(pct * 100, 1),
              inner.loops >= 1000, by_time)
    if inner.loops < 1000 and not by_time:
        return None
    sev = "high" if inner.loops >= 100_000 or (pct is not None and pct >= 0.5) else "medium"
    share = f" ({pct * 100:.1f}% of runtime)" if pct is not None else ""
    evidence.update({"inner_loops": inner.loops, "inner_total_ms": spent,
                     "inner_pct": pct * 100 if pct is not None else None})
    return mk(sev, rule, n,
              f"inner {inner.label} runs {fint(inner.loops)} loops x {fint(inner.actual_rows)} rows/loop, "
              f"{fms(spent)} ms total{share}", evidence)


# The engine modules import mk and the shared rules above from this module, so they are imported only here,
# after those names exist. qq_plan/__init__.py imports this module before any engine rule module.
from .rules_mysql import (my_filesort, my_full_scan, my_index_scan_filter, my_index_scan_full,  # noqa: E402
                          my_join_buffer, my_key_unused, my_low_filter, my_nested_loop_heavy, my_not_covering,
                          my_plan_rules, my_temp_table)
from .rules_pg import (pg_buffers_read, pg_fold_findings, pg_generic_plan, pg_hash_batches,  # noqa: E402
                       pg_heap_fetches, pg_index_recheck, pg_nested_loop_heavy, pg_no_partition_pruning,
                       pg_parallel_shortfall, pg_plan_rules, pg_seq_scan_filtered, pg_sort_disk, pg_statement_rules)
from .rules_questdb import (qdb_full_frame_scan, qdb_hash_join, qdb_latest_by_scan, qdb_non_jit_filter,  # noqa: E402
                            qdb_single_thread_filter, qdb_sort)

__all__ = ["HINTS", "MY_JSON_RULES", "MY_TREE_RULES", "PG_RULES", "QDB_RULES", "RULE_WIDTH", "hot_nodes",
           "misestimate", "mk", "my_plan_rules", "nested_loop_heavy", "pg_fold_findings", "pg_plan_rules",
           "pg_statement_rules", "run_rules"]


QDB_RULES = [("qdb.full-frame-scan", qdb_full_frame_scan), ("qdb.non-jit-filter", qdb_non_jit_filter),
             ("qdb.single-thread-filter", qdb_single_thread_filter), ("qdb.sort", qdb_sort),
             ("qdb.latest-by-scan", qdb_latest_by_scan), ("qdb.hash-join", qdb_hash_join)]
PG_RULES = [("pg.row-misestimate", misestimate("pg")), ("pg.seq-scan-filtered", pg_seq_scan_filtered),
            ("pg.nested-loop-heavy", pg_nested_loop_heavy), ("pg.sort-disk", pg_sort_disk),
            ("pg.hash-batches", pg_hash_batches), ("pg.heap-fetches", pg_heap_fetches),
            ("pg.index-recheck", pg_index_recheck), ("pg.buffers-read", pg_buffers_read),
            ("pg.parallel-shortfall", pg_parallel_shortfall), ("pg.generic-plan", pg_generic_plan),
            ("pg.no-partition-pruning", pg_no_partition_pruning)]
MY_JSON_RULES = [("my.full-scan", my_full_scan), ("my.index-scan-full", my_index_scan_full),
                 ("my.index-scan-filter", my_index_scan_filter),
                 ("my.low-filter", my_low_filter), ("my.filesort", my_filesort), ("my.temp-table", my_temp_table),
                 ("my.join-buffer", my_join_buffer), ("my.key-unused", my_key_unused),
                 ("my.not-covering", my_not_covering)]
MY_TREE_RULES = [("my.row-misestimate", misestimate("my")), ("my.full-scan", my_full_scan),
                 ("my.index-scan-full", my_index_scan_full), ("my.index-scan-filter", my_index_scan_filter),
                 ("my.filesort", my_filesort),
                 ("my.temp-table", my_temp_table), ("my.join-buffer", my_join_buffer),
                 ("my.nested-loop-heavy", my_nested_loop_heavy)]


def run_rules(nodes: List[Node], rules: List[Tuple[str, Callable[..., Any]]], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for n in nodes:
        for rule_id, fn in rules:
            f = fn(n, ctx)
            log.debug("rule %s path=%s op=%r -> %s", rule_id, n.path, n.op, f"FIRED {f['severity']}" if f else "no")
            if f:
                out.append(f)
    return out
