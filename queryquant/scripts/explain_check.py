#!/usr/bin/env python3
"""Deterministic triage for SQL query plans. Reads a saved plan; never connects to a database.

Usage:
    python3 explain_check.py PLANFILE [--format auto|pg-json|pg-text|pg-statements|mysql-json|mysql-tree|questdb-tree]
                             [--top N] [--json] [--verbose]
    python3 explain_check.py - < plan.json        # read the plan from stdin

Input formats (--format auto detects them):
    pg-json     PostgreSQL EXPLAIN (FORMAT JSON): a JSON array whose first element has
                "Plan", or a bare object with "Plan". psql's aligned wrapper ("QUERY PLAN"
                header, dashed rule, trailing "+" marks, "(1 row)") is stripped first.
    pg-text     PostgreSQL plain-text EXPLAIN [ANALYZE, BUFFERS]: any line with "(cost=.. rows=.. width=..)"
                (MySQL never prints width, so this wins over mysql-tree). Nodes are the root line and the
                "->" lines; nesting is by the column of "->". Other lines ("Filter:", "Buffers:",
                "Sort Method:", "Batches:", ...) are properties of the nearest less-indented node and are
                mapped onto the FORMAT JSON keys, so every pg rule runs unchanged. Lines back at the root's
                indentation ("Planning Time:", "Execution Time:", "Trigger ...") are the footer; a PG 13+
                "Planning:" block's "Buffers:" line becomes top-level "Planning" buffer fields.
    pg-statements  pg_stat_statements as aligned psql text (references/postgres.md §5): the first line is a
                header naming calls, mean_ms (or mean_exec_time) and query; total_exec_time is read as
                total_ms. One node per statement row; header, dashed rule and "(N rows)" are skipped.
    mysql-json  MySQL EXPLAIN FORMAT=JSON: an object with "query_block". A "\\G" style
                "EXPLAIN:" prefix or a "| ... |" table border is stripped first.
    mysql-tree  MySQL EXPLAIN ANALYZE / FORMAT=TREE text: lines "-> op  (cost=..) (actual time=..)".
                Nesting comes from indentation (any consistent width).
    questdb-tree QuestDB EXPLAIN text: not JSON, no "->" lines, and at least one line starting
                with a known operator (PageFrame, Frame/Interval/Row/Index scan, Async Filter, ...).
                Lines starting lowercase ("filter:", "keys:", "intervals:") are properties of the
                nearest less-indented node above. Indentation is irregular, so parent = nearest
                less-indented node, not a fixed step. psql's wrapper is stripped as for pg-json.

Per-node figures:
    self_time = actual total time x loops - sum(child actual total time x child loops),
                clamped at 0. Both engines report per-loop averages, hence the multiply.
    est_ratio = estimated rows / max(actual rows, 1), per loop. >= 10 means over-estimated,
                <= 0.1 means under-estimated.

Rules (severity in brackets):
    pg.hot-node            top 3 nodes by self_time [high >= 50% of runtime, medium >= 20%, else low]
    pg.row-misestimate     est_ratio >= 10 or <= 0.1 with >= 100 actual rows [high at 100x]. A parent
                           whose ratio points the same way and is within 2x of a firing child's is
                           folded into the deepest origin ("; propagates to ..."). Over-estimates on
                           nodes below a Limit are suppressed (the Limit stopped them early). Same for my.
    pg.seq-scan-filtered   Seq Scan discarding >= 10x what it keeps and >= 10,000 rows; without
                           ANALYZE, a filtered Seq Scan estimated at >= 10,000 rows [medium; high if hot]
    pg.nested-loop-heavy   inner side of a Nested Loop runs >= 1,000 loops, or its time (actual total time x
                           loops) is >= 20% of the runtime over >= 50 loops [medium; high at >= 50% of the
                           runtime or 100,000 loops]
    pg.sort-disk           Sort spilled to disk [high]
    pg.hash-batches        Hash split into more than one batch [medium; high at 8]
    pg.heap-fetches        Index Only Scan with heap fetches >= 10% of rows [medium]
    pg.index-recheck       Bitmap Heap Scan recheck removed >= the rows it kept [low; medium if hot or any
                           lossy heap blocks ("lossy bitmap (work_mem)")]
    pg.buffers-read        node's own shared reads >= 10,000 blocks, or any temp writes [medium; temp high]
    pg.parallel-shortfall  Workers Launched < Workers Planned [low]
    pg.trigger-time        a trigger took >= 10% of Execution Time [medium]
    pg.no-analyze          plan has no actual rows anywhere [low]
    pg.generic-plan        Index Cond / Filter / Recheck Cond uses a bind parameter ($1, ...) and the node
                           misestimates >= 10x (same test as pg.row-misestimate, ANALYZE only) [high]. The
                           pg.row-misestimate finding on that node gets a generic-plan hint instead.
    pg.no-partition-pruning  Append / Merge Append over scans of >= 4 relations named <parent>_..., Subplans
                           Removed absent or 0, and a child Filter applies a function (date_trunc, date_part,
                           extract, to_char, timezone, date) or a cast to a column [high]. pg.seq-scan-filtered
                           findings on its child scans are folded into it.
    pg.n-plus-one          pg-statements only: calls >= 1,000, mean <= 5 ms, rows/calls <= 100, top 3 by
                           total_ms, and the query is a "WHERE <col> = $n" lookup without ANY( / IN ( [high]
    my.full-scan           access_type ALL / "Table scan on" over >= 10,000 rows [high at 100,000]
    my.index-scan-full     access_type index / "Index scan on" over >= 10,000 rows [medium]; skipped when
                           my.index-scan-filter fires on the same node
    my.index-scan-filter   access_type index with an attached_condition and filtered <= 5%, any row count:
                           an ORDER BY index walk that filters as it goes [medium]. Tree: "Index [range] scan
                           on" directly under "Filter:" that keeps <= 5% of >= 1,000 rows read (rows x loops);
                           without ANALYZE, a "(reverse)" index scan under a Limit
    my.low-filter          filtered <= 10% with >= 1,000 rows examined per scan [medium]
    my.filesort            filesort [medium at >= 10,000 input rows, else low]
    my.temp-table          temporary table for GROUP BY / DISTINCT [medium]
    my.join-buffer         join buffer (Block Nested Loop / hash join) [medium]
    my.key-unused          possible_keys listed but no key chosen [medium]
    my.not-covering        ref/range on a secondary index, not covering [medium at >= 10,000 rows, else low]
    my.hot-node, my.row-misestimate, my.nested-loop-heavy   tree only, same thresholds as pg
    my.no-analyze          no actual times anywhere [low]
    qdb.full-frame-scan    Frame scan on a table, and no Interval or Index scan anywhere in the plan [high]
    qdb.non-jit-filter     Async Filter (the predicate did not JIT-compile) [medium]
    qdb.single-thread-filter  plain Filter node [medium]
    qdb.sort               Sort / Sort light / Encode sort [medium; low when bounded by lo:/hi:]
    qdb.latest-by-scan     LatestBy with no Index or Interval scan beneath it [medium]
    qdb.hash-join          Hash Join / Hash Join Light [low]
    qdb.no-analyze         always: QuestDB EXPLAIN never executes [low]
"Hot" (for the severity bumps above) means a top-3 node holding >= 20% of the runtime.

Output: findings sorted by severity, then self_time descending. --json prints them as a JSON
array. --verbose logs every node visited and every rule evaluated to stderr.
Exit status: 0 when the plan parsed (with or without findings); 2 on parse or usage error.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import List, Optional

# python3 -I leaves the script's directory off sys.path; add it so the qq_plan package next to this file imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qq_plan import analyse, detect, render_text  # noqa: E402

log = logging.getLogger("explain_check")


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Rank actionable findings in a saved query plan (no DB access).")
    ap.add_argument("planfile", help="plan file, or - for stdin")
    ap.add_argument("--format", default="auto", choices=["auto", "pg-json", "pg-text", "pg-statements", "mysql-json",
                                                         "mysql-tree", "questdb-tree"])
    ap.add_argument("--top", type=int, default=10, help="max findings to print (default 10)")
    ap.add_argument("--json", action="store_true", help="print findings as a JSON array")
    ap.add_argument("--verbose", action="store_true", help="log every node and rule evaluation to stderr")

    def usage_error(msg: str) -> None:
        log.error("usage: %s", msg)
        sys.exit(2)

    ap.error = usage_error  # type: ignore[method-assign]
    args = ap.parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    log.debug("start: planfile=%s format=%s top=%s json=%s", args.planfile, args.format, args.top, args.json)
    if args.top < 1:
        usage_error("--top must be >= 1")
    try:
        if args.planfile == "-":
            text = sys.stdin.read()
        else:
            with open(args.planfile, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        log.debug("read %d chars from %s", len(text), args.planfile)
        kind, doc = detect(text, args.format)
        meta, nodes, findings = analyse(kind, doc)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.debug("failure detail", exc_info=True)
        log.error("cannot parse %s: %s", args.planfile, exc)
        return 2
    shown = findings[: args.top]
    for f in findings:
        f.pop("_self", None)
    log.debug("done: %d findings, showing %d", len(findings), len(shown))
    if args.json:
        print(json.dumps(shown, indent=2))
    else:
        print(render_text(meta, nodes, shown, len(findings)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
