"""QuestDB rules. QuestDB EXPLAIN never executes, so every rule reads the plan shape only."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .common import Node
from .questdb import QDB_BOUNDED_SCAN
from .rules import mk


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
