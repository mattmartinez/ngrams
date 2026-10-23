"""pg_stat_statements parser: the aligned psql output of the statement-stats query in references/postgres.md §5."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .common import Node, log, num

# PG 13+ column names, for output that skipped the reference query's AS aliases.
PGSS_ALIASES = {"mean_exec_time": "mean_ms", "total_exec_time": "total_ms"}
LABEL_WIDTH = 80


def parse_pg_statements(lines: List[str]) -> Tuple[List[Node], Dict[str, Any]]:
    """One Node per statement row. total_time carries total_ms so findings sort by total time."""
    cols = [PGSS_ALIASES.get(c, c) for c in (c.strip().lower() for c in lines[0].split("|"))]
    log.debug("pg-statements: columns %s", cols)
    last_is_query = cols[-1] == "query"
    nodes: List[Node] = []
    for line in lines[1:]:
        # The query is the last column in the reference query; split only as far as it so a "|" inside stays.
        cells = line.split("|", len(cols) - 1) if last_is_query else line.split("|")
        row = {c: cell.strip() for c, cell in zip(cols, cells)}
        calls = num(row.get("calls") or None)
        if len(cells) != len(cols) or calls is None:
            log.debug("pg-statements: not a statement row (%d cells, calls=%r), ignored: %r", len(cells),
                      row.get("calls"), line)
            continue
        mean, total, rows = (num(row.get(k) or None) for k in ("mean_ms", "total_ms", "rows"))
        if total is None and mean is not None:
            total = mean * calls
        query = row.get("query", "").rstrip("+").rstrip()
        label = query if len(query) <= LABEL_WIDTH else query[: LABEL_WIDTH - 3] + "..."
        n = Node(engine="pg", op="statement", path=str(len(nodes)), label=label, total_time=total,
                 raw={"calls": calls, "mean_ms": mean, "total_ms": total, "rows": rows, "query": query})
        nodes.append(n)
        log.debug("pg-statements: row %s calls=%s mean_ms=%s total_ms=%s rows=%s query=%r", n.path, calls, mean, total,
                  rows, query)
    if not nodes:
        raise ValueError("pg_stat_statements output has no statement rows")
    return nodes, {"engine": "pg-statements", "analyze": False,
                   "note": f"pg_stat_statements, {len(nodes)} statements"}
