"""Path kinds, small-table demotion, ranking and the text report for the scanner.

path_kind: read | write | job | unknown (see `path_kind`). Small tables: Quartz (`qrtz_*`), Liquibase
(`databasechangelog*`), `flyway_schema_history`, plus the config's `small_tables` globs; a finding
whose tables are all small drops to low severity with a summary suffix.
Ranking: confirmed (none in this pass) -> structural rules -> index rules -> low severity. Inside the
structural group: nested N+1, read-path N+1, read-path unbounded lists, job-path findings, write-path
N+1, then the rest. Then severity, hot path (loop / Job, Scheduler, Task, Resource class), file, line.
Text: one line per top finding, up to 5 grouped lines for the rest, then the counts line.
"""
import collections
import fnmatch
import logging
import os

from .rules import STRUCTURAL

log = logging.getLogger("qq_scan.rank")
FRAMEWORK_TABLES = ("qrtz_*", "databasechangelog*", "flyway_schema_history")
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
NOUNS = {"qq.n-plus-one": "N+1 lookups", "qq.unbounded-list": "unbounded lists",
         "qq.catch-all-filter": "catch-all filters", "qq.offset-paging": "OFFSET-paged queries",
         "qq.unchunked-batch": "unchunked batches", "qq.no-index-for-filter": "filters with no usable index",
         "qq.no-index-for-sort": "sorts with no matching index",
         "qq.tenant-column-not-leading": "indexes not led by the tenant column"}
WHERE = {"read": "on read paths", "write": "on write paths", "job": "in jobs", "unknown": "on unclassified paths"}
HINTS = {("qq.n-plus-one", "write"): "low impact unless saves have 100+ items",
         ("qq.n-plus-one", "read"): "one query per row on every call",
         ("qq.n-plus-one", "job"): "one query per item in a background job",
         ("qq.unbounded-list", "read"): "grows with the table on every call",
         ("qq.no-index-for-filter", None): "needs table size to judge",
         ("qq.no-index-for-sort", None): "needs table size to judge"}
READ_PREFIXES = ("get", "find", "list", "search", "load", "fetch", "count", "export")
WRITE_PREFIXES = ("insert", "update", "save", "create", "delete", "add", "remove", "set", "process")
JOB_WORDS = ("Task", "Job", "Scheduler", "Worker", "Cleanup")


def path_kind(type_name, method, anns, caller_anns):
    """read | write | job | unknown for the code path a finding sits on.

    Order: job class; the method's own @GET / @POST|@PUT|@DELETE; its name prefix; the HTTP verbs of
    methods that call it by name (`caller_anns`, a set of annotation names).
    """
    if any(w in (type_name or "") for w in JOB_WORDS):
        return "job"
    anns = set(anns or ())
    if "@GET" in anns:
        return "read"
    if anns & {"@POST", "@PUT", "@DELETE"}:
        return "write"
    m = method or ""
    if m.startswith(READ_PREFIXES):
        return "read"
    if m.startswith(WRITE_PREFIXES):
        return "write"
    caller_anns = caller_anns or set()
    if "@GET" in caller_anns:
        return "read"
    if caller_anns & {"@POST", "@PUT", "@DELETE"}:
        return "write"
    return "unknown"



def small_table_label(names, extra_globs):
    """Suffix for a finding whose tables are all small, else None."""
    names = [n for n in names if n]
    if not names:
        return None
    if all(any(fnmatch.fnmatchcase(n, g) for g in FRAMEWORK_TABLES) for n in names):
        return "(framework table, usually small)"
    globs = FRAMEWORK_TABLES + tuple(g.lower() for g in extra_globs)
    if all(any(fnmatch.fnmatchcase(n, g) for g in globs) for n in names):
        return "(small table per config)"
    return None


def _structural_rank(f):
    rule, pk = f["rule"], f["path_kind"]
    if rule == "qq.n-plus-one" and f["evidence"].get("nested"):
        return 0
    if rule == "qq.n-plus-one" and pk == "read":
        return 1
    if rule == "qq.unbounded-list" and pk == "read":
        return 2
    if pk == "job":
        return 3
    if rule == "qq.n-plus-one" and pk == "write":
        return 4
    return 5


def rank(findings, hot_ids):
    def key(f):
        if f.get("confirmed"):
            group = 0
        elif f["severity"] == "low":
            group = 3
        else:
            group = 1 if f["rule"] in STRUCTURAL else 2
        hot = f["rule"] == "qq.n-plus-one" or f["statement_id"] in hot_ids
        return (group, _structural_rank(f) if group == 1 else 0, SEVERITY_RANK[f["severity"]], not hot,
                f["file"] or "", f["line"] or 0)
    return sorted(findings, key=key)


def _owner(f, label):
    if f["rule"] == "qq.n-plus-one":
        return os.path.basename(f["file"] or "?").rsplit(".", 1)[0]
    if f["rule"] == "qq.tenant-column-not-leading":
        return f["evidence"].get("table", "?")
    return label.split(".")[0]


def text_report(result, extras, top):
    lines, findings, labels = [], result["findings"], extras["labels"]
    for n, (f, label) in enumerate(zip(findings[:top], labels), 1):
        loc = "%s:%s" % (os.path.basename(f["file"] or "?"), f["line"])
        lines.append("%d. %s · %s · %s · %s · %s path · %s" % (n, f["service"], loc, label, f["summary"],
                                                             f["path_kind"], f["evidence_level"]))
    groups = collections.OrderedDict()
    for f, label in zip(findings[top:], labels[top:]):
        groups.setdefault((f["rule"], f["path_kind"]), []).append(_owner(f, label))
    for (rule, pk), owners in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:5]:
        counts = collections.Counter(owners).most_common(3)
        who = ", ".join("%s ×%d" % c for c in counts) + (", …" if len(set(owners)) > 3 else "")
        hint = HINTS.get((rule, pk)) or HINTS.get((rule, None))
        lines.append("+ %d more %s %s (%s)%s" % (len(owners), NOUNS.get(rule, rule), WHERE[pk], who,
                                                 " — " + hint if hint else ""))
    lines.append("… and %d more (%d not analysed, %d accepted by config)" % (
        max(0, len(findings) - top), extras["not_analysed"], extras["accepted"]))
    return "\n".join(lines)
