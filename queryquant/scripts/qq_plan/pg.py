"""PostgreSQL parsers: EXPLAIN (FORMAT JSON) and plain-text EXPLAIN [ANALYZE, BUFFERS]."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .common import NUM, PGT_COST, Node, log, num

PGT_ACTUAL = re.compile(r"\(actual (?:time=(?P<t1>" + NUM + r")\.\.(?P<t2>" + NUM + r") )?rows=(?P<rows>" + NUM
                        + r") loops=(?P<loops>" + NUM + r")\)")
PGT_HEAD = re.compile(r"^(?P<type>.+?)(?: using (?P<index>\S+))?(?: on (?P<rel>\S+)(?: (?P<alias>\S+))?)?$")
PGT_JOIN = re.compile(r"^(?P<base>Hash|Merge|Nested Loop) (?P<jt>Left|Right|Full|Semi|Anti|Right Semi|Right Anti)"
                      r" Join$")
PGT_INT_PROPS = {"Rows Removed by Filter", "Rows Removed by Index Recheck", "Rows Removed by Join Filter",
                 "Heap Fetches", "Workers Planned", "Workers Launched"}
PGT_TEXT_PROPS = {"Filter", "Index Cond", "Recheck Cond", "Hash Cond", "Merge Cond", "Join Filter"}


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
    elif key == "Heap Blocks":
        # "exact=12 lossy=34": text omits a zero part; FORMAT JSON always carries both keys.
        p["Exact Heap Blocks"], p["Lossy Heap Blocks"] = 0.0, 0.0
        for kv in val.split():
            k, _, v = kv.partition("=")
            if k in ("exact", "lossy"):
                p[f"{k.capitalize()} Heap Blocks"] = num(v)
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
    footer = planning = False
    for line in lines:
        text = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        is_node = text.startswith("->") or (root_indent is None and PGT_COST.search(text) is not None)
        if not is_node and root_indent is not None and indent <= root_indent:
            footer = True
        if footer or root_indent is None and not is_node:
            tm = re.match(r"(Planning|Execution) Time: (" + NUM + ") ms", text)
            gm = re.match(r"Trigger (?P<name>.+?)(?: on (?P<rel>\S+))?: time=(?P<t>" + NUM + r") calls=(?P<c>\d+)", text)
            # PG 13+ prints "Planning:" then an indented "Buffers: ..." line; FORMAT JSON nests it as "Planning".
            pb = planning and text.startswith("Buffers: ")
            if tm:
                top[f"{tm.group(1)} Time"] = float(tm.group(2))
            elif gm:
                top["Triggers"].append({"Trigger Name": gm.group("name"), "Relation": gm.group("rel"),
                                        "Time": float(gm.group("t")), "Calls": float(gm.group("c"))})
            elif pb:
                pgt_property(top.setdefault("Planning", {}), text)
            planning = text == "Planning:"
            log.debug("pg-text: footer line %s: %r", "used" if tm or gm or pb or planning else "ignored", text)
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
