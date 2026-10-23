"""MySQL parsers: EXPLAIN FORMAT=JSON and EXPLAIN [ANALYZE] FORMAT=TREE."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .common import NUM, TREE_LINE, Node, loops_of, num

TREE_COST = re.compile(r"\(cost=(?P<c1>" + NUM + r")(?:\.\.(?P<c2>" + NUM + r"))? rows=(?P<rows>" + NUM + r")\)")
TREE_ACTUAL = re.compile(r"\(actual time=(?P<t1>" + NUM + r")\.\.(?P<t2>" + NUM + r") rows=(?P<rows>" + NUM
                         + r") loops=(?P<loops>" + NUM + r")\)")
MY_NODE_KEYS = {"query_block", "ordering_operation", "grouping_operation", "duplicates_removal",
                "materialized_from_subquery", "union_result"}


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
