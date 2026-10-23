#!/usr/bin/env python3
"""Deterministic triage for SQL query plans. Reads a saved plan; never connects to a database.

Usage:
    python3 explain_check.py PLANFILE [--format auto|pg-json|pg-text|mysql-json|mysql-tree|questdb-tree]
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
                indentation ("Planning Time:", "Execution Time:", "Trigger ...") are the footer.
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
    pg.nested-loop-heavy   inner side of a Nested Loop runs >= 1,000 loops [high at 100,000]
    pg.sort-disk           Sort spilled to disk [high]
    pg.hash-batches        Hash split into more than one batch [medium; high at 8]
    pg.heap-fetches        Index Only Scan with heap fetches >= 10% of rows [medium]
    pg.index-recheck       Bitmap Heap Scan recheck removed >= the rows it kept [low; medium if hot]
    pg.buffers-read        node's own shared reads >= 10,000 blocks, or any temp writes [medium; temp high]
    pg.parallel-shortfall  Workers Launched < Workers Planned [low]
    pg.trigger-time        a trigger took >= 10% of Execution Time [medium]
    pg.no-analyze          plan has no actual rows anywhere [low]
    my.full-scan           access_type ALL / "Table scan on" over >= 10,000 rows [high at 100,000]
    my.index-scan-full     access_type index / "Index scan on" over >= 10,000 rows [medium]; skipped when
                           my.index-scan-filter fires on the same node
    my.index-scan-filter   access_type index with an attached_condition and filtered <= 5%, any row count:
                           an ORDER BY index walk that filters as it goes [medium]
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
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("explain_check")

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
NOTE_RULES = {"pg.no-analyze", "my.no-analyze", "qdb.no-analyze"}
NUM = r"\d+(?:\.\d+)?(?:e[+-]?\d+)?"
TREE_LINE = re.compile(r"^( *)-> (.*)$")
TREE_COST = re.compile(r"\(cost=(?P<c1>" + NUM + r")(?:\.\.(?P<c2>" + NUM + r"))? rows=(?P<rows>" + NUM + r")\)")
TREE_ACTUAL = re.compile(r"\(actual time=(?P<t1>" + NUM + r")\.\.(?P<t2>" + NUM + r") rows=(?P<rows>" + NUM
                         + r") loops=(?P<loops>" + NUM + r")\)")
QDB_NODE_PREFIXES = ("PageFrame", "Frame forward scan", "Frame backward scan", "Interval forward scan",
                     "Interval backward scan", "Row forward scan", "Row backward scan", "Async JIT Filter",
                     "Async Filter", "Index forward scan", "Index backward scan", "SampleBy", "LatestBy",
                     "VirtualRecord", "Selected Record", "Sort light", "Encode sort", "GroupBy", "Async Group By",
                     "AsOf Join", "Lt Join", "Hash Join")
PGT_COST = re.compile(r"\(cost=(?P<c1>" + NUM + r")\.\.(?P<c2>" + NUM + r") rows=(?P<rows>" + NUM
                      + r") width=(?P<width>\d+)\)")
PGT_ACTUAL = re.compile(r"\(actual (?:time=(?P<t1>" + NUM + r")\.\.(?P<t2>" + NUM + r") )?rows=(?P<rows>" + NUM
                        + r") loops=(?P<loops>" + NUM + r")\)")
PGT_HEAD = re.compile(r"^(?P<type>.+?)(?: using (?P<index>\S+))?(?: on (?P<rel>\S+)(?: (?P<alias>\S+))?)?$")
PGT_JOIN = re.compile(r"^(?P<base>Hash|Merge|Nested Loop) (?P<jt>Left|Right|Full|Semi|Anti|Right Semi|Right Anti)"
                      r" Join$")
PGT_INT_PROPS = {"Rows Removed by Filter", "Rows Removed by Index Recheck", "Rows Removed by Join Filter",
                 "Heap Fetches", "Workers Planned", "Workers Launched"}
PGT_TEXT_PROPS = {"Filter", "Index Cond", "Recheck Cond", "Hash Cond", "Merge Cond", "Join Filter"}
QDB_BOUNDED_SCAN = re.compile(r"^(Interval|Index) (forward|backward) scan")
MY_NODE_KEYS = {"query_block", "ordering_operation", "grouping_operation", "duplicates_removal",
                "materialized_from_subquery", "union_result"}

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


@dataclass
class Node:
    engine: str
    op: str
    path: str
    label: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)
    relation: Optional[str] = None
    index: Optional[str] = None
    est_rows: Optional[float] = None
    actual_rows: Optional[float] = None  # per loop
    loops: Optional[float] = None
    total_time: Optional[float] = None  # ms, per loop
    self_time: float = 0.0
    children: List["Node"] = field(default_factory=list, repr=False)


def num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fint(v: Optional[float]) -> str:
    return "?" if v is None else f"{v:,.0f}"


def fms(v: Optional[float]) -> str:
    return "?" if v is None else f"{v:,.1f}"


def fratio(r: float) -> str:
    if r <= 0:
        return "inf x under"
    return f"{1 / r:,.0f}x under" if r < 1 else f"{r:,.0f}x over"


def loops_of(n: Node) -> float:
    return n.loops if n.loops is not None else 1.0


def mk(sev: str, rule: str, node: Optional[Node], summary: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    if node is None:
        nd: Dict[str, Any] = {"label": "(whole plan)", "type": None, "relation": None, "index": None, "path": None}
    else:
        nd = {"label": node.label, "type": node.op, "relation": node.relation, "index": node.index,
              "path": node.path}
    ev = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in evidence.items()}
    return {"severity": sev, "rule": rule, "node": nd, "summary": summary, "evidence": ev,
            "hint": HINTS[rule], "_self": node.self_time if node else 0.0}


# ---------------------------------------------------------------- input and detection

def unwrap_client(text: str) -> str:
    """Strip psql aligned output and mysql client decorations around a plan."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\|\s", "", line.rstrip())
        line = re.sub(r"\s*[+|]$", "", line)
        line = re.sub(r"^(\s*)EXPLAIN: ", r"\1", line)
        s = line.strip()
        if s in ("QUERY PLAN", "EXPLAIN") or re.fullmatch(r"[+\-]+", s) or re.fullmatch(r"\(\d+ rows?\)", s) \
                or re.fullmatch(r"\*+ \d+\. row \*+", s):
            continue
        out.append(line)
    return "\n".join(out)


def detect(text: str, fmt: str) -> Tuple[str, Any]:
    cleaned = unwrap_client(text)
    kind: Optional[str] = None
    doc: Any = None
    for name, candidate in (("raw", text), ("unwrapped", cleaned)):
        try:
            doc = json.loads(candidate)
            log.debug("detect: %s input parsed as JSON (%s)", name, type(doc).__name__)
            break
        except ValueError as exc:
            log.debug("detect: %s input is not JSON: %s", name, exc)
            doc = None
    if doc is not None:
        if isinstance(doc, list) and doc and isinstance(doc[0], dict) and "Plan" in doc[0]:
            kind, doc = "pg-json", doc[0]
        elif isinstance(doc, dict) and "Plan" in doc:
            kind = "pg-json"
        elif isinstance(doc, dict) and "query_block" in doc:
            kind = "mysql-json"
        else:
            log.debug("detect: JSON has neither \"Plan\" nor \"query_block\"")
    elif any(PGT_COST.search(ln) for ln in cleaned.splitlines()):
        kind, doc = "pg-text", [ln for ln in cleaned.expandtabs(8).splitlines() if ln.strip()]
    else:
        lines = [ln for ln in cleaned.expandtabs(4).splitlines() if TREE_LINE.match(ln)]
        if lines and any("(cost=" in ln or "(actual time=" in ln for ln in lines):
            kind, doc = "mysql-tree", lines
        elif not lines and any(ln.strip().startswith(QDB_NODE_PREFIXES) for ln in cleaned.splitlines()):
            kind, doc = "questdb-tree", [ln for ln in cleaned.expandtabs(4).splitlines() if ln.strip()]
        else:
            log.debug("detect: %d '-> ' lines (none with cost/actual) and no QuestDB operator lines", len(lines))
    log.debug("detect: result=%s requested=%s", kind, fmt)
    if kind is None:
        raise ValueError("input is not a recognised plan (pg-json, pg-text, mysql-json, mysql-tree or questdb-tree)")
    if fmt != "auto" and fmt != kind:
        raise ValueError(f"--format {fmt} requested but input looks like {kind}")
    return kind, doc


# ---------------------------------------------------------------- parsers

def pg_label(p: Dict[str, Any]) -> str:
    s = str(p.get("Node Type", "?"))
    if p.get("Sort Key"):
        s += f" ({', '.join(map(str, p['Sort Key']))})"
    if p.get("Index Name"):
        s += f" using {p['Index Name']}"
    if p.get("Relation Name"):
        s += f" on {p['Relation Name']}"
        if p.get("Alias") and p["Alias"] != p["Relation Name"]:
            s += f" {p['Alias']}"
    return s


def parse_pg(top: Dict[str, Any]) -> Tuple[List[Node], Dict[str, Any]]:
    if not isinstance(top.get("Plan"), dict):
        raise ValueError('"Plan" is not an object')
    nodes: List[Node] = []

    def build(p: Dict[str, Any], path: str) -> Node:
        n = Node(engine="pg", op=str(p.get("Node Type", "?")), path=path, label=pg_label(p), raw=p,
                 relation=p.get("Relation Name"), index=p.get("Index Name"), est_rows=num(p.get("Plan Rows")),
                 actual_rows=num(p.get("Actual Rows")), loops=num(p.get("Actual Loops")),
                 total_time=num(p.get("Actual Total Time")))
        nodes.append(n)
        for i, child in enumerate(p.get("Plans") or []):
            if isinstance(child, dict):
                n.children.append(build(child, f"{path}.{i}"))
        return n

    build(top["Plan"], "0")
    meta = {
        "engine": "postgres",
        "analyze": any(n.actual_rows is not None for n in nodes),
        "buffers": any("Shared Hit Blocks" in n.raw for n in nodes),
        "execution": num(top.get("Execution Time")),
        "planning": num(top.get("Planning Time")),
        "triggers": top.get("Triggers") or [],
    }
    return nodes, meta


def my_table_node(t: Dict[str, Any], path: str) -> Node:
    name = t.get("table_name", "?")
    access = t.get("access_type", "?")
    key = t.get("key")
    label = f"table {name} ({access}" + (f" via {key}" if key else "") + ")"
    return Node(engine="mysql", op="table", path=path, label=label, raw=t, relation=name, index=key,
                est_rows=num(t.get("rows_examined_per_scan")))


def parse_mysql_json(doc: Dict[str, Any]) -> Tuple[List[Node], Dict[str, Any]]:
    nodes: List[Node] = []

    def walk(obj: Any, path: str, parent: Optional[Node]) -> None:
        if isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]", parent)
            return
        if not isinstance(obj, dict):
            return
        for key, val in obj.items():
            sub = f"{path}.{key}" if path else key
            if key == "table" and isinstance(val, dict):
                n = my_table_node(val, sub)
            elif key in MY_NODE_KEYS and isinstance(val, dict):
                n = Node(engine="mysql", op=key, path=sub, label=key, raw=val)
            else:
                walk(val, sub, parent)
                continue
            nodes.append(n)
            if parent is not None:
                parent.children.append(n)
            walk(val, sub, n)

    walk(doc, "", None)
    if not nodes:
        raise ValueError("query_block contains no tables or operations")
    cost = num((doc.get("query_block") or {}).get("cost_info", {}).get("query_cost"))
    return nodes, {"engine": "mysql-json", "analyze": False, "query_cost": cost}


def parse_mysql_tree(lines: List[str]) -> Tuple[List[Node], Dict[str, Any]]:
    nodes: List[Node] = []
    roots: List[Node] = []
    stack: List[Tuple[int, Node]] = []
    for line in lines:
        m = TREE_LINE.match(line)
        if not m:
            continue
        indent, rest = len(m.group(1)), m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        path = f"{parent.path}.{len(parent.children)}" if parent else str(len(roots))
        op = rest.split("  (")[0].strip()
        n = Node(engine="mysql", op=op, path=path, label=op, raw={"line": rest})
        rel = re.search(r"\bon (\S+)", op)
        idx = re.search(r"\busing (\S+)", op)
        n.relation = rel.group(1) if rel else None
        n.index = idx.group(1) if idx and idx.group(1) != "temporary" else None
        cm = TREE_COST.search(rest)
        if cm:
            n.est_rows = float(cm.group("rows"))
        am = TREE_ACTUAL.search(rest)
        if am:
            n.total_time, n.actual_rows, n.loops = float(am.group("t2")), float(am.group("rows")), float(am.group("loops"))
        elif "(never executed)" in rest:
            n.total_time, n.actual_rows, n.loops = 0.0, 0.0, 0.0
        nodes.append(n)
        (parent.children if parent else roots).append(n)
        stack.append((indent, n))
    analyze = any(n.actual_rows is not None for n in nodes)
    total = sum(r.total_time * loops_of(r) for r in roots if r.total_time is not None) if analyze else None
    return nodes, {"engine": "mysql-tree", "analyze": analyze, "execution": total}


def pgt_node(text: str) -> Dict[str, Any]:
    """Turn one plan line ("Seq Scan on users u  (cost=..) (actual ..)") into a FORMAT JSON style dict."""
    head = text.split("  (", 1)[0].strip()
    p: Dict[str, Any] = {}
    hm = PGT_HEAD.match(head)
    ntype = hm.group("type") if hm else head
    if hm and hm.group("rel"):
        if ntype == "Bitmap Index Scan":
            p["Index Name"] = hm.group("rel")
        else:
            p["Relation Name"], p["Alias"] = hm.group("rel"), hm.group("alias") or hm.group("rel")
    if hm and hm.group("index"):
        p["Index Name"] = hm.group("index")
    if ntype.startswith("Parallel "):
        ntype, p["Parallel Aware"] = ntype[len("Parallel "):], True
    if ntype.endswith(" Backward"):
        ntype, p["Scan Direction"] = ntype[: -len(" Backward")], "Backward"
    jm = PGT_JOIN.match(ntype)
    if jm:
        ntype = "Nested Loop" if jm.group("base") == "Nested Loop" else f"{jm.group('base')} Join"
        p["Join Type"] = jm.group("jt")
    p["Node Type"] = ntype
    cm = PGT_COST.search(text)
    if cm:
        p.update({"Startup Cost": float(cm.group("c1")), "Total Cost": float(cm.group("c2")),
                  "Plan Rows": float(cm.group("rows")), "Plan Width": int(cm.group("width"))})
    am = PGT_ACTUAL.search(text)
    if am:
        p.update({"Actual Rows": float(am.group("rows")), "Actual Loops": float(am.group("loops"))})
        if am.group("t2") is not None:
            p.update({"Actual Startup Time": float(am.group("t1")), "Actual Total Time": float(am.group("t2"))})
    elif "(never executed)" in text:
        p.update({"Actual Rows": 0.0, "Actual Loops": 0.0, "Actual Startup Time": 0.0, "Actual Total Time": 0.0})
    return p


def pgt_property(p: Dict[str, Any], text: str) -> bool:
    """Map one property line onto FORMAT JSON keys. Returns False for lines this script does not use."""
    key, sep, val = text.partition(": ")
    val = val.strip()
    if not sep:
        return False
    if key in PGT_INT_PROPS:
        p[key] = num(val)
    elif key in PGT_TEXT_PROPS:
        p[key] = val
    elif key in ("Sort Key", "Group Key"):
        p[key] = [k.strip() for k in val.split(", ")]
    elif key == "Sort Method":
        m = re.match(r"(?P<m>.+?)(?:\s+(?P<t>Disk|Memory): (?P<kb>\d+)kB)?$", val)
        p["Sort Method"] = m.group("m") if m else val
        p["Sort Space Type"] = "Disk" if m and m.group("t") == "Disk" else "Memory"
        if m and m.group("kb"):
            p["Sort Space Used"] = float(m.group("kb"))
    elif key == "Buckets":
        bm = re.search(r"Batches: (\d+)(?: \(originally (\d+)\))?", val)
        mm = re.search(r"Memory Usage: (\d+)kB", val)
        if bm:
            p["Hash Batches"] = float(bm.group(1))
            p["Original Hash Batches"] = float(bm.group(2) or bm.group(1))
        if mm:
            p["Peak Memory Usage"] = float(mm.group(1))
    elif key == "Buffers":
        # "shared hit=1 read=2 dirtied=3, temp read=4 written=5": each segment names its buffer class.
        for k in ("Hit", "Read", "Dirtied", "Written"):
            p.setdefault(f"Shared {k} Blocks", 0.0)
        p.setdefault("Temp Read Blocks", 0.0)
        p.setdefault("Temp Written Blocks", 0.0)
        for seg in val.split(", "):
            words = seg.split()
            if not words or words[0] not in ("shared", "local", "temp"):
                continue
            for kv in words[1:]:
                k, _, v = kv.partition("=")
                p[f"{words[0].capitalize()} {k.capitalize()} Blocks"] = num(v)
    else:
        return False
    return True


def parse_pg_text(lines: List[str]) -> Tuple[List[Node], Dict[str, Any]]:
    top: Dict[str, Any] = {"Triggers": []}
    stack: List[Tuple[int, Dict[str, Any]]] = []
    root_indent: Optional[int] = None
    footer = False
    for line in lines:
        text = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        is_node = text.startswith("->") or (root_indent is None and PGT_COST.search(text) is not None)
        if not is_node and root_indent is not None and indent <= root_indent:
            footer = True
        if footer or root_indent is None and not is_node:
            tm = re.match(r"(Planning|Execution) Time: (" + NUM + ") ms", text)
            gm = re.match(r"Trigger (?P<name>.+?)(?: on (?P<rel>\S+))?: time=(?P<t>" + NUM + r") calls=(?P<c>\d+)", text)
            if tm:
                top[f"{tm.group(1)} Time"] = float(tm.group(2))
            elif gm:
                top["Triggers"].append({"Trigger Name": gm.group("name"), "Relation": gm.group("rel"),
                                        "Time": float(gm.group("t")), "Calls": float(gm.group("c"))})
            log.debug("pg-text: footer line %s: %r", "used" if tm or gm else "ignored", text)
            continue
        if is_node:
            # The "->" column is the node's depth; the root has no arrow and sits at its own indentation.
            if text.startswith("->"):
                text = text[2:].strip()
            node = pgt_node(text)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if stack:
                stack[-1][1].setdefault("Plans", []).append(node)
            elif root_indent is None:
                top["Plan"], root_indent = node, indent
            else:
                raise ValueError(f"second root node in text plan: {text!r}")
            stack.append((indent, node))
            log.debug("pg-text: node %r at indent %d", node.get("Node Type"), indent)
            continue
        owner = next((nd for ind, nd in reversed(stack) if ind < indent), None)
        used = owner is not None and pgt_property(owner, text)
        log.debug("pg-text: property %s: %r", "mapped" if used else "ignored", text)
    if "Plan" not in top:
        raise ValueError("no plan node found in text plan")
    nodes, meta = parse_pg(top)
    meta["engine"] = "postgres-text"
    return nodes, meta


def parse_questdb(lines: List[str]) -> Tuple[List[Node], Dict[str, Any]]:
    nodes: List[Node] = []
    roots: List[Node] = []
    stack: List[Tuple[int, Node]] = []
    for line in lines:
        indent, text = len(line) - len(line.lstrip(" ")), line.strip()
        if text[0].islower():
            # Property line ("filter: ...", "keys: [...]"): belongs to the nearest node above that is less
            # indented. Not pushed on the stack, so deeper child nodes still find their real parent.
            owner = next((nd for ind, nd in reversed(stack) if ind < indent), None)
            if owner is None:
                log.debug("questdb: property with no owning node, ignored: %r", text)
                continue
            key, _, val = text.partition(":")
            props = owner.raw["props"]
            props[key.strip()] = f"{props[key.strip()]}; {val.strip()}" if key.strip() in props else val.strip()
            log.debug("questdb: property %r -> node path=%s", text, owner.path)
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        path = f"{parent.path}.{len(parent.children)}" if parent else str(len(roots))
        rel = re.search(r"(?:\bon|table): (\S+)", text)
        n = Node(engine="questdb", op=text, path=path, label=text, raw={"line": text, "props": {}},
                 relation=rel.group(1) if rel else None)
        nodes.append(n)
        (parent.children if parent else roots).append(n)
        stack.append((indent, n))
    if not nodes:
        raise ValueError("QuestDB plan has no operator lines")
    return nodes, {"engine": "questdb", "analyze": False}


def compute_self_times(nodes: List[Node]) -> None:
    for n in nodes:
        if n.total_time is not None:
            child = sum(c.total_time * loops_of(c) for c in n.children if c.total_time is not None)
            n.self_time = max(0.0, n.total_time * loops_of(n) - child)
        log.debug("visit path=%s op=%r rel=%s idx=%s est=%s actual=%s loops=%s total_ms=%s self_ms=%.3f children=%d",
                  n.path, n.op, n.relation, n.index, n.est_rows, n.actual_rows, n.loops, n.total_time,
                  n.self_time, len(n.children))


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


# ---------------------------------------------------------------- postgres rules

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
    if inner.loops is None or inner.loops < 1000:
        return None
    sev = "high" if inner.loops >= 100_000 else "medium"
    spent = inner.total_time * inner.loops if inner.total_time is not None else None
    return mk(sev, "pg.nested-loop-heavy", n,
              f"inner {inner.label} runs {fint(inner.loops)} loops x {fint(inner.actual_rows)} rows/loop, "
              f"{fms(spent)} ms total",
              {"inner_path": inner.path, "inner_type": inner.op, "inner_index": inner.index,
               "inner_loops": inner.loops, "inner_total_ms": spent})


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
    sev = "medium" if n.path in ctx["hot"] else "low"
    return mk(sev, "pg.index-recheck", n, f"recheck removed {fint(rr)} rows/loop, kept {fint(n.actual_rows)}",
              {"rows_removed_by_recheck": rr, "actual_rows": n.actual_rows,
               "lossy_heap_blocks": num(n.raw.get("Lossy Heap Blocks"))})


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
    root = nodes[0]
    total = meta["execution"] or (root.total_time * loops_of(root) if root.total_time is not None else None)
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


# ---------------------------------------------------------------- mysql rules

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


def my_index_scan_filter(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.op != "table" or n.raw.get("access_type") != "index":
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
    if inner.loops is None or inner.loops < 1000:
        return None
    spent = inner.total_time * inner.loops if inner.total_time is not None else None
    return mk("high" if inner.loops >= 100_000 else "medium", "my.nested-loop-heavy", n,
              f"inner {inner.label} runs {fint(inner.loops)} loops x {fint(inner.actual_rows)} rows/loop, "
              f"{fms(spent)} ms total",
              {"inner_path": inner.path, "inner_loops": inner.loops, "inner_total_ms": spent})


def my_plan_rules(nodes: List[Node], meta: Dict[str, Any], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not meta["analyze"]:
        log.debug("rule my.no-analyze -> FIRED low")
        return [mk("low", "my.no-analyze", None, "Estimates only. Capture EXPLAIN ANALYZE (FORMAT=TREE) when it "
                   "is safe to execute: it runs the statement.", {})]
    return hot_nodes(nodes, meta["execution"], "my.hot-node", ctx)


def qdb_subtree(n: Node) -> List[Node]:
    out = [n]
    for c in n.children:
        out += qdb_subtree(c)
    return out


def qdb_filter_text(n: Node) -> Optional[str]:
    if "filter" in n.raw["props"]:
        return n.raw["props"]["filter"]
    return n.op.split("filter:", 1)[1].strip() if "filter:" in n.op else None


def qdb_full_frame_scan(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not re.match(r"Frame (forward|backward) scan on:", n.op) or ctx["qdb_bounded"]:
        return None
    return mk("high", "qdb.full-frame-scan", n,
              f"full scan of {n.relation}: no predicate on the designated timestamp and no symbol index used",
              {"table": n.relation, "scan": n.op})


def qdb_non_jit_filter(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not n.op.startswith("Async Filter"):
        return None
    flt = qdb_filter_text(n)
    return mk("medium", "qdb.non-jit-filter", n, f"filter not JIT-compiled: {flt}", {"filter": flt})


def qdb_single_thread_filter(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if n.op != "Filter" and not n.op.startswith("Filter "):
        return None
    flt = qdb_filter_text(n)
    return mk("medium", "qdb.single-thread-filter", n, f"single-threaded filter: {flt}", {"filter": flt})


def qdb_sort(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not re.match(r"(Sort|Encode sort)( light)?(\s|$)", n.op):
        return None
    text = " ".join([n.op] + [f"{k}: {v}" for k, v in n.raw["props"].items()])
    bounded = "lo:" in text or "hi:" in text
    keys = n.raw["props"].get("keys")
    return mk("low" if bounded else "medium", "qdb.sort", n,
              f"{n.op} keys {keys or '?'}" + ("; bounded by LIMIT" if bounded else "; unbounded"),
              {"keys": keys, "bounded": bounded})


def qdb_latest_by_scan(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not n.op.startswith("LatestBy") or any(QDB_BOUNDED_SCAN.match(d.op) for d in qdb_subtree(n)):
        return None
    return mk("medium", "qdb.latest-by-scan", n, f"{n.op} with no Index or Interval scan beneath it", {"op": n.op})


def qdb_hash_join(n: Node, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not n.op.startswith("Hash Join"):
        return None
    return mk("low", "qdb.hash-join", n, f"{n.op}", {"op": n.op})


def dedupe_misestimates(nodes: List[Node], findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold a parent's misestimate into a firing child's when it is the same error carried upward."""
    by_path = {f["node"]["path"]: f for f in findings if f["rule"].endswith(".row-misestimate")}
    origin: Dict[str, Dict[str, Any]] = {}
    dropped: set = set()
    for n in sorted(nodes, key=lambda x: x.path.count("."), reverse=True):
        f = by_path.get(n.path)
        if f is None:
            continue
        r = n.est_rows / max(n.actual_rows, 1.0)
        for c in n.children:
            if c.path not in by_path:
                continue
            rc = c.est_rows / max(c.actual_rows, 1.0)
            # Same direction (both under or both over) and the two ratios within a factor of 2.
            if (r < 1) == (rc < 1) and max(r, rc) / max(min(r, rc), 1e-12) <= 2:
                src = origin.get(c.path, by_path[c.path])
                src["summary"] += f"; propagates to {n.op} [path {n.path}]"
                origin[n.path] = src
                dropped.add(id(f))
                log.debug("dedupe: misestimate at path=%s (ratio %.4g) folded into origin path=%s via child %s "
                          "(ratio %.4g)", n.path, r, src["node"]["path"], c.path, rc)
                break
        else:
            log.debug("dedupe: misestimate at path=%s (ratio %.4g) kept as its own origin", n.path, r)
    return [f for f in findings if id(f) not in dropped]


QDB_RULES = [("qdb.full-frame-scan", qdb_full_frame_scan), ("qdb.non-jit-filter", qdb_non_jit_filter),
             ("qdb.single-thread-filter", qdb_single_thread_filter), ("qdb.sort", qdb_sort),
             ("qdb.latest-by-scan", qdb_latest_by_scan), ("qdb.hash-join", qdb_hash_join)]
PG_RULES = [("pg.row-misestimate", misestimate("pg")), ("pg.seq-scan-filtered", pg_seq_scan_filtered),
            ("pg.nested-loop-heavy", pg_nested_loop_heavy), ("pg.sort-disk", pg_sort_disk),
            ("pg.hash-batches", pg_hash_batches), ("pg.heap-fetches", pg_heap_fetches),
            ("pg.index-recheck", pg_index_recheck), ("pg.buffers-read", pg_buffers_read),
            ("pg.parallel-shortfall", pg_parallel_shortfall)]
MY_JSON_RULES = [("my.full-scan", my_full_scan), ("my.index-scan-full", my_index_scan_full),
                 ("my.index-scan-filter", my_index_scan_filter),
                 ("my.low-filter", my_low_filter), ("my.filesort", my_filesort), ("my.temp-table", my_temp_table),
                 ("my.join-buffer", my_join_buffer), ("my.key-unused", my_key_unused),
                 ("my.not-covering", my_not_covering)]
MY_TREE_RULES = [("my.row-misestimate", misestimate("my")), ("my.full-scan", my_full_scan),
                 ("my.index-scan-full", my_index_scan_full), ("my.filesort", my_filesort),
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


def analyse(kind: str, doc: Any) -> Tuple[Dict[str, Any], List[Node], List[Dict[str, Any]]]:
    parser = {"pg-json": parse_pg, "pg-text": parse_pg_text, "mysql-json": parse_mysql_json, "mysql-tree": parse_mysql_tree,
              "questdb-tree": parse_questdb}[kind]
    nodes, meta = parser(doc)
    log.debug("parse: %s produced %d nodes, analyze=%s", kind, len(nodes), meta["analyze"])
    compute_self_times(nodes)
    # A node's ancestors are its path prefixes ("0.1.2" -> "0", "0.1"); both pg and mysql-tree use dotted paths.
    limits = {n.path for n in nodes if n.op == "Limit" or n.op.startswith("Limit:")}
    under_limit = {n.path for n in nodes
                   if any(".".join(n.path.split(".")[:i]) in limits for i in range(1, n.path.count(".") + 1))}
    log.debug("limit guard: Limit nodes=%s, nodes below a Limit=%s", sorted(limits), sorted(under_limit))
    ctx: Dict[str, Any] = {"hot": set(), "under_limit": under_limit}
    if kind in ("pg-json", "pg-text"):
        findings = dedupe_misestimates(nodes, pg_plan_rules(nodes, meta, ctx) + run_rules(nodes, PG_RULES, ctx))
    elif kind == "mysql-json":
        findings = my_plan_rules(nodes, meta, ctx) + run_rules(nodes, MY_JSON_RULES, ctx)
    elif kind == "mysql-tree":
        findings = dedupe_misestimates(nodes, my_plan_rules(nodes, meta, ctx) + run_rules(nodes, MY_TREE_RULES, ctx))
    else:
        ctx["qdb_bounded"] = any(QDB_BOUNDED_SCAN.match(n.op) for n in nodes)
        log.debug("questdb: interval/index scan present=%s", ctx["qdb_bounded"])
        findings = [mk("low", "qdb.no-analyze", None, "QuestDB EXPLAIN never executes; there are no actual rows "
                       "or times.", {})] + run_rules(nodes, QDB_RULES, ctx)
    findings.sort(key=lambda f: (SEVERITY_RANK[f["severity"]], -f["_self"], f["rule"], f["node"]["path"] or ""))
    return meta, nodes, findings


# ---------------------------------------------------------------- output

def header(meta: Dict[str, Any], count: int) -> str:
    flags = [f for f, on in (("ANALYZE", meta["analyze"]), ("BUFFERS", meta.get("buffers"))) if on]
    s = f"plan: {meta['engine']} ({', '.join(flags) if flags else 'estimates only'})  nodes: {count}"
    if meta.get("execution") is not None:
        s += f"  execution: {fms(meta['execution'])} ms"
    if meta.get("planning") is not None:
        s += f"  planning: {fms(meta['planning'])} ms"
    if meta.get("query_cost") is not None:
        s += f"  query_cost: {meta['query_cost']:,.1f}"
    return s


def render_text(meta: Dict[str, Any], nodes: List[Node], findings: List[Dict[str, Any]], total: int) -> str:
    lines = [header(meta, len(nodes))]
    for f in findings:
        nd = f["node"]
        where = nd["label"] + (f" [path {nd['path']}]" if nd["path"] is not None else "")
        lines.append(f"{f['severity'].upper():<6} {f['rule']:<{RULE_WIDTH}} {where}")
        lines.append(f"       {f['summary']}")
        lines.append(f"       hint: {f['hint']}")
    if total > len(findings):
        lines.append(f"... {total - len(findings)} more finding(s); raise --top to see them.")
    if all(f["rule"] in NOTE_RULES for f in findings) and total == len(findings):
        lines.append("no findings beyond informational.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Rank actionable findings in a saved query plan (no DB access).")
    ap.add_argument("planfile", help="plan file, or - for stdin")
    ap.add_argument("--format", default="auto", choices=["auto", "pg-json", "pg-text", "mysql-json", "mysql-tree", "questdb-tree"])
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
