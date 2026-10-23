"""QuestDB EXPLAIN text parser."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .common import Node, log

QDB_BOUNDED_SCAN = re.compile(r"^(Interval|Index) (forward|backward) scan")


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
