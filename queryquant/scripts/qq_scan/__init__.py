"""queryquant static query scanner: finds every SQL statement in a JDBI/Liquibase codebase, maps the
schema's indexes, and runs first-pass rules. Never connects to a database.

`scan(root, config, only)` returns the JSON-ready result (scanner_version, root, services, statements,
findings) plus counts; `text_report()` renders the terse ranked list. Every finding carries a
`path_kind`; ranking, small-table demotion and the grouped tail live in rank.py.
"""
import logging
import os

from . import java_sql, liquibase, rank as rank_mod, rules, rules_calls, services as services_mod
from .sql_shape import normalise, shape as read_shape

__version__ = "0.1.0"
log = logging.getLogger("qq_scan")
HOT_WORDS = ("Job", "Scheduler", "Task", "Resource")


def _analyse(stmt):
    raw = stmt.pop("raw_sql")
    why = stmt.pop("unresolved")
    stmt["sql"] = normalise(raw) if raw is not None else None
    stmt["_shape"] = None
    if raw is None:
        stmt["kind"] = "select" if stmt["_ann"] in ("SqlQuery", "createQuery") else "other"
    else:
        sh = read_shape(raw, stmt["_lists"])
        stmt["_shape"] = sh
        stmt["kind"] = sh["kind"]
        why = sh["not_analysed"]
        if why is None and sh["defines"]:
            why = "template placeholder <%s> (@Define) changes the SQL at run time" % sh["defines"][0]
    stmt["analysed"] = why is None
    stmt["not_analysed_reason"] = why
    log.debug("statement %s %s:%d kind=%s analysed=%s%s", stmt["id"], stmt["file"], stmt["line"], stmt["kind"],
              stmt["analysed"], "" if why is None else " (%s)" % why)


def _scan_service(svc, root, config, counter):
    schema = liquibase.load_schema(svc.master, svc.root, root)
    files = []
    for path in svc.java_files:
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        try:
            jf = java_sql.JavaFile(path, rel)
        except OSError as exc:
            log.error("cannot read %s: %s", rel, exc)
            continue
        jf.statements = java_sql.sqlobject_statements(jf, svc.name) + java_sql.fluent_statements(jf, svc.name)
        files.append(jf)
    stmts = sorted((s for jf in files for s in jf.statements), key=lambda s: (s["file"], s["line"]))
    for s in stmts:
        counter[0] += 1
        s["id"] = "S%04d" % counter[0]
        _analyse(s)
    registry = {}
    for s in stmts:
        if s["method"]:
            registry.setdefault(s["dao"], {}).setdefault(s["method"], []).append(s)
    log.debug("DAO types in %s: %s", svc.name, sorted(registry))
    calls, helpers, name_anns = [], [], {}
    for jf in files:
        java_sql.find_loops(jf)
        found_calls = java_sql.call_sites(jf, registry)
        found_helpers = java_sql.helper_calls(jf)
        for c in found_calls + found_helpers:
            c["subject"] = java_sql.loop_subject(jf, c["loop"]) if c["loop"] else None
        calls.extend(found_calls)
        helpers.extend(found_helpers)
        java_sql.named_call_annotations(jf, name_anns)
    hot = set()
    for c in calls:
        if c["loop"] or any(w in (c["caller_type"] or "") for w in HOT_WORDS):
            hot.add((c["type"], c["method"]))
    findings = []
    for s in stmts:
        s["_hot"] = (s["dao"], s["method"]) in hot or any(w in s["dao"] for w in HOT_WORDS)
        found = rules.unchunked_batch(s, None, None)
        if s["analysed"]:
            for rule in rules.STATEMENT_RULES:
                hits = rule(s, s["_shape"], schema.tables)
                log.debug("rule %s on %s: %d finding(s)", rule.__name__, s["id"], len(hits))
                found.extend(hits)
        else:
            log.debug("rules skipped for %s: not analysed", s["id"])
        findings.extend(found)
    findings.extend(rules_calls.n_plus_one(calls, registry))
    findings.extend(rules_calls.helper_n_plus_one(helpers, calls, registry))
    if config.leading_column:
        findings.extend(rules.tenant_column(svc.name, schema, config.leading_column))
    by_id = {s["id"]: s for s in stmts}
    dao_caller_anns = {}
    for c in calls:
        dao_caller_anns.setdefault((c["type"], c["method"]), set()).update(c["caller_anns"])
    for f in findings:
        _classify(f, by_id.get(f["statement_id"]), name_anns, dao_caller_anns, config)
    info = {"name": svc.name, "root": svc.root,
            "master": os.path.relpath(svc.master, root).replace(os.sep, "/") if svc.master else None,
            "changesets": schema.changesets, "tables": schema.export()}
    log.info("service %s: %d changeset files, %d changesets, %d tables, %d indexes, %d statements, %d findings",
             svc.name, len(schema.files), schema.changesets, len(schema.tables),
             sum(len(t["indexes"]) for t in schema.tables.values()), len(stmts), len(findings))
    return info, stmts, findings, schema


def _classify(f, stmt, name_anns, dao_caller_anns, config):
    """Set path_kind, and demote findings that only touch small tables."""
    if "_ctx" in f:
        ctype, cmethod, anns = f["_ctx"]
        f["path_kind"] = rank_mod.path_kind(ctype, cmethod, anns, name_anns.get(cmethod))
    elif stmt is not None:
        f["path_kind"] = rank_mod.path_kind(stmt["dao"], stmt["method"], stmt["annotations"],
                                            dao_caller_anns.get((stmt["dao"], stmt["method"]), set())
                                            | name_anns.get(stmt["method"], set()))
    else:
        f["path_kind"] = "unknown"
    if f["rule"] == "qq.tenant-column-not-leading" or stmt is None or not stmt.get("_shape"):
        names = [f["evidence"].get("table")]
    else:
        names = [t["name"] for t in stmt["_shape"]["tables"] if not t.get("derived")]
    label = rank_mod.small_table_label(names, config.small_tables)
    if label:
        f["severity"] = "low"
        f["summary"] = "%s %s" % (f["summary"], label)
    log.debug("classified %s %s:%s path=%s%s", f["rule"], f["file"], f["line"], f["path_kind"],
              " small-table" if label else "")


def scan(root, config, only=None):
    """Scan `root`. Returns (result, extras) where extras holds counts for the report footer."""
    root = os.path.abspath(root)
    log.debug("scan enter root=%s only=%s", root, only)
    svcs = services_mod.find_services(root, config, only)
    counter, out_services, statements, findings, schemas = [0], [], [], [], {}
    for svc in svcs:
        info, stmts, found, schema = _scan_service(svc, root, config, counter)
        out_services.append(info)
        statements.extend(stmts)
        findings.extend(found)
        schemas[svc.name] = schema
    kept, accepted = [], 0
    for f in findings:
        if config.accepted_by(f):
            accepted += 1
        else:
            kept.append(f)
    hot_ids = {s["id"] for s in statements if s["_hot"]}
    ranked = rank_mod.rank(kept, hot_ids)
    calls = {s["id"]: "%s.%s" % (s["dao"], s["method"]) if s["method"] else s["dao"] for s in statements}
    labels = [f.get("_call") or calls.get(f["statement_id"], "?") for f in ranked]
    result = {"scanner_version": __version__, "root": root, "services": out_services,
              "statements": [{k: v for k, v in s.items() if not k.startswith("_")} for s in statements],
              "findings": [{k: v for k, v in f.items() if not k.startswith("_")} for f in ranked]}
    extras = {"labels": labels, "accepted": accepted,
              "not_analysed": sum(1 for s in statements if not s["analysed"]), "schemas": schemas}
    log.debug("scan exit: %d services, %d statements, %d findings (%d accepted)", len(out_services),
              len(statements), len(ranked), accepted)
    return result, extras


def text_report(result, extras, top):
    return rank_mod.text_report(result, extras, top)
