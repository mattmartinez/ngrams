"""queryquant plan checker: parse a saved plan, run the rules, render findings. No database access."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .common import (NOTE_RULES, SEVERITY_RANK, Node, compute_self_times, dedupe_misestimates, detect, fms, limit_guard,
                     log, loops_of)
from .mysql import parse_mysql_json, parse_mysql_tree
from .pg import parse_pg, parse_pg_text
from .pg_statements import parse_pg_statements
from .questdb import QDB_BOUNDED_SCAN, parse_questdb
from .rules import (MY_JSON_RULES, MY_TREE_RULES, PG_RULES, QDB_RULES, RULE_WIDTH, mk, my_plan_rules, pg_fold_findings,
                    pg_plan_rules, pg_statement_rules, run_rules)

__all__ = ["NOTE_RULES", "Node", "analyse", "detect", "header", "render_text"]


def analyse(kind: str, doc: Any) -> Tuple[Dict[str, Any], List[Node], List[Dict[str, Any]]]:
    parser = {"pg-json": parse_pg, "pg-text": parse_pg_text, "mysql-json": parse_mysql_json, "mysql-tree": parse_mysql_tree,
              "questdb-tree": parse_questdb, "pg-statements": parse_pg_statements}[kind]
    nodes, meta = parser(doc)
    log.debug("parse: %s produced %d nodes, analyze=%s", kind, len(nodes), meta["analyze"])
    compute_self_times(nodes)
    under_limit = limit_guard(nodes)
    root = nodes[0]
    total = meta.get("execution") or (root.total_time * loops_of(root) if root.total_time is not None else None)
    parent = {c.path: n for n in nodes for c in n.children}
    log.debug("context: total_ms=%s for runtime shares, %d parent links", total, len(parent))
    ctx: Dict[str, Any] = {"hot": set(), "under_limit": under_limit, "total": total, "parent": parent}
    if kind in ("pg-json", "pg-text"):
        findings = pg_fold_findings(nodes, dedupe_misestimates(nodes, pg_plan_rules(nodes, meta, ctx)
                                                               + run_rules(nodes, PG_RULES, ctx)))
    elif kind == "pg-statements":
        findings = pg_statement_rules(nodes, meta, ctx)
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
    note = meta.get("note") or (", ".join(flags) if flags else "estimates only")
    s = f"plan: {meta['engine']} ({note})  nodes: {count}"
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
