"""Shared pieces: the Node model, number formatting, input unwrap/detection, self time, the Limit guard
and misestimate dedupe."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("explain_check")

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
NOTE_RULES = {"pg.no-analyze", "my.no-analyze", "qdb.no-analyze"}
NUM = r"\d+(?:\.\d+)?(?:e[+-]?\d+)?"
TREE_LINE = re.compile(r"^( *)-> (.*)$")
QDB_NODE_PREFIXES = ("PageFrame", "Frame forward scan", "Frame backward scan", "Interval forward scan",
                     "Interval backward scan", "Row forward scan", "Row backward scan", "Async JIT Filter",
                     "Async Filter", "Index forward scan", "Index backward scan", "SampleBy", "LatestBy",
                     "VirtualRecord", "Selected Record", "Sort light", "Encode sort", "GroupBy", "Async Group By",
                     "AsOf Join", "Lt Join", "Hash Join")
PGT_COST = re.compile(r"\(cost=(?P<c1>" + NUM + r")\.\.(?P<c2>" + NUM + r") rows=(?P<rows>" + NUM
                      + r") width=(?P<width>\d+)\)")


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


def is_pg_statements(cleaned: str) -> bool:
    """pg_stat_statements text: the first line is a header naming calls, mean_ms (or mean_exec_time) and query."""
    first = next((ln for ln in cleaned.splitlines() if ln.strip()), "")
    cols = {c.strip().lower() for c in first.split("|")}
    hit = "calls" in cols and "query" in cols and bool(cols & {"mean_ms", "mean_exec_time"})
    log.debug("detect: pg-statements header %s (columns %s)", "found" if hit else "not found", sorted(cols))
    return hit


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
    elif is_pg_statements(cleaned):
        kind, doc = "pg-statements", [ln for ln in cleaned.splitlines() if ln.strip()]
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
        raise ValueError("input is not a recognised plan (pg-json, pg-text, pg-statements, mysql-json, mysql-tree or questdb-tree)")
    if fmt != "auto" and fmt != kind:
        raise ValueError(f"--format {fmt} requested but input looks like {kind}")
    return kind, doc


def compute_self_times(nodes: List[Node]) -> None:
    for n in nodes:
        if n.total_time is not None:
            child = sum(c.total_time * loops_of(c) for c in n.children if c.total_time is not None)
            n.self_time = max(0.0, n.total_time * loops_of(n) - child)
        log.debug("visit path=%s op=%r rel=%s idx=%s est=%s actual=%s loops=%s total_ms=%s self_ms=%.3f children=%d",
                  n.path, n.op, n.relation, n.index, n.est_rows, n.actual_rows, n.loops, n.total_time,
                  n.self_time, len(n.children))


def limit_guard(nodes: List[Node]) -> Set[str]:
    """Paths of the nodes that sit below a Limit (pg "Limit", mysql-tree "Limit: N row(s)")."""
    # A node's ancestors are its path prefixes ("0.1.2" -> "0", "0.1"); both pg and mysql-tree use dotted paths.
    limits = {n.path for n in nodes if n.op == "Limit" or n.op.startswith("Limit:")}
    under_limit = {n.path for n in nodes
                   if any(".".join(n.path.split(".")[:i]) in limits for i in range(1, n.path.count(".") + 1))}
    log.debug("limit guard: Limit nodes=%s, nodes below a Limit=%s", sorted(limits), sorted(under_limit))
    return under_limit


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
