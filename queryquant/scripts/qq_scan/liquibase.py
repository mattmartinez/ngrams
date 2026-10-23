"""Read a Liquibase master and the formatted-SQL changesets it includes into a table/index map.

Only files reached from the master's `<include file=...>` / `<includeAll path=...>` entries count;
changelog folders often hold archived files that never run. Nested XML masters are followed.
Include paths are tried relative to the master's folder, then to the resources root, then to the
service root. XML `<changeSet>` bodies are not parsed (logged and counted as skipped).

DDL understood (MySQL): CREATE TABLE (inline PRIMARY KEY / UNIQUE / INDEX / KEY / FOREIGN KEY, also
all on one line), ALTER TABLE ADD/DROP/MODIFY/CHANGE/RENAME COLUMN, ADD/DROP/RENAME INDEX|KEY,
ADD/DROP PRIMARY KEY, ADD [CONSTRAINT] UNIQUE|FOREIGN KEY, RENAME TO; CREATE [UNIQUE] INDEX; DROP INDEX;
DROP TABLE; RENAME TABLE. A foreign key with no index already leading on its columns gets the
implicit index InnoDB creates. Table and column names are lower-cased; schema prefixes dropped.
"""
import bisect
import logging
import os
import re
import xml.etree.ElementTree as ET

from .sql_lex import low, name_of, split, tokenize

log = logging.getLogger("qq_scan.liquibase")
_CHANGESET = re.compile(r"^[ \t]*--[ \t]*changeset[ \t]", re.M | re.I)


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _resolve(ref, master_dir, service_root):
    resources = os.path.join(service_root, "src", "main", "resources")
    for base in (master_dir, resources, service_root):
        candidate = os.path.normpath(os.path.join(base, ref))
        if os.path.exists(candidate):
            return candidate
    return None


def master_files(master, service_root, seen=None):
    """Ordered changeset files reachable from `master`. Returns (files, skipped_reasons)."""
    seen = seen if seen is not None else set()
    files, skipped = [], []
    try:
        tree = ET.parse(master)
    except (ET.ParseError, OSError) as exc:
        log.error("cannot parse Liquibase master %s: %s", master, exc)
        return files, ["master unreadable: %s" % exc]
    master_dir = os.path.dirname(master)
    for el in tree.getroot():
        tag = _tag(el)
        if tag == "include":
            ref = el.get("file") or ""
            path = _resolve(ref, master_dir, service_root)
            if path is None:
                log.warning("include not found: %s (from %s)", ref, master)
                skipped.append("include not found: %s" % ref)
            elif path in seen:
                continue
            elif path.endswith(".xml"):
                seen.add(path)
                sub, sub_skipped = master_files(path, service_root, seen)
                files.extend(sub)
                skipped.extend(sub_skipped)
            else:
                seen.add(path)
                files.append(path)
        elif tag == "includeAll":
            ref = el.get("path") or ""
            folder = _resolve(ref, master_dir, service_root)
            if folder is None or not os.path.isdir(folder):
                skipped.append("includeAll folder not found: %s" % ref)
                continue
            for fn in sorted(os.listdir(folder)):
                path = os.path.join(folder, fn)
                if fn.endswith(".sql") and path not in seen:
                    seen.add(path)
                    files.append(path)
        elif tag == "changeSet":
            log.debug("XML changeSet %s in %s not parsed", el.get("id"), master)
            skipped.append("XML changeSet not parsed: %s" % el.get("id"))
    return files, skipped


class Schema:
    """tables: {name: {"columns", "primary_key", "indexes": [{name, columns, unique, src}]}}."""

    def __init__(self):
        self.tables = {}
        self.changesets = 0
        self.files = []
        self.skipped = []

    def table(self, name, create=False):
        if name not in self.tables and create:
            self.tables[name] = {"columns": [], "primary_key": [], "indexes": []}
        return self.tables.get(name)

    def export(self):
        return {n: {"columns": list(t["columns"]), "primary_key": list(t["primary_key"]),
                    "indexes": [{"name": i["name"], "columns": list(i["columns"]), "unique": i["unique"]}
                                for i in t["indexes"]]}
                for n, t in sorted(self.tables.items())}


def _cols(toks):
    """Column names inside an index column list token span: `(a, b(10) DESC, `c`)`."""
    if not toks or toks[0].value != "(":
        return []
    depth, end = 0, len(toks)
    for i, t in enumerate(toks):
        depth += (t.value == "(") - (t.value == ")")
        if depth == 0:
            end = i
            break
    out = []
    for part in split(toks[1:end], {","}):
        name = name_of(part[0])
        if name and not (len(part) > 1 and part[1].value == "("
                         and part[0].type == "ident" and len(part) > 3 and part[2].type != "num"):
            out.append(name)
    return out


def _paren_at(toks, start=0):
    return next((i for i in range(start, len(toks)) if toks[i].value == "("), None)


class _Reader:
    """Applies DDL statements to a Schema."""

    def __init__(self, schema, rel_file, line_of):
        self.schema, self.rel, self.line_of = schema, rel_file, line_of

    def add_index(self, table, name, cols, unique, tok, implicit=False):
        if not cols:
            return
        t = self.schema.table(table, create=True)
        if implicit:
            leading = [t["primary_key"]] + [i["columns"] for i in t["indexes"]]
            if any(c[:len(cols)] == cols for c in leading):
                log.debug("FK on %s%s already has an index", table, cols)
                return
        taken = {i["name"] for i in t["indexes"]}
        base = name or cols[0]
        name, n = base, 2
        while name in taken:
            name, n = "%s_%d" % (base, n), n + 1
        t["indexes"].append({"name": name, "columns": cols, "unique": unique,
                             "src": (self.rel, self.line_of(tok.start))})
        log.debug("index %s.%s %s unique=%s%s (%s:%d)", table, name, cols, unique,
                  " implicit-fk" if implicit else "", self.rel, self.line_of(tok.start))

    def key_def(self, table, d, fks):
        """Table-level key definitions. Returns True if `d` was one."""
        words = [low(x) for x in d]
        i, cname = 0, None
        if words[0] == "constraint":
            if len(d) > 1 and low(d[1]) not in ("primary", "unique", "foreign", "check"):
                cname = name_of(d[1])
                i = 2
            else:
                i = 1
        w = words[i] if i < len(words) else None
        if w == "primary":
            self.schema.table(table, create=True)["primary_key"] = _cols(d[_paren_at(d, i):])
            log.debug("primary key %s %s", table, self.schema.tables[table]["primary_key"])
        elif w == "unique" or (w in ("index", "key") and i == 0):
            j = i + 1
            if w == "unique" and j < len(d) and low(d[j]) in ("index", "key"):
                j += 1
            name = name_of(d[j]) if j < len(d) and d[j].value != "(" and low(d[j]) != "using" else None
            p = _paren_at(d, j)
            self.add_index(table, name or cname, _cols(d[p:]) if p is not None else [], w == "unique", d[0])
        elif w == "foreign":
            p = _paren_at(d, i)
            fk_name = name_of(d[i + 2]) if i + 2 < len(d) and d[i + 2].value != "(" else None
            fks.append((cname or fk_name, _cols(d[p:]) if p is not None else [], d[0]))
        elif w in ("fulltext", "spatial", "check", "constraint"):
            log.debug("skip %s definition on %s", w, table)
        else:
            return False
        return True

    def column_def(self, table, d):
        t = self.schema.table(table, create=True)
        col = name_of(d[0])
        if col is None:
            return
        if col not in t["columns"]:
            t["columns"].append(col)
        words = [low(x) for x in d]
        for k, w in enumerate(words):
            if w == "primary" and k + 1 < len(words) and words[k + 1] == "key":
                t["primary_key"] = [col]
            elif w == "unique":
                self.add_index(table, col, [col], True, d[k])

    def finish_fks(self, table, fks):
        for name, cols, tok in fks:
            self.add_index(table, name, cols, False, tok, implicit=True)

    def create_table(self, toks):
        i = 2 + (low(toks[1]) == "temporary")
        if [low(x) for x in toks[i:i + 3]] == ["if", "not", "exists"]:
            i += 3
        if i >= len(toks):
            return
        name, i = self.table_name(toks, i)
        p = _paren_at(toks, i)
        if p is None:
            return
        self.schema.tables[name] = {"columns": [], "primary_key": [], "indexes": []}
        depth, end = 0, len(toks)
        for k in range(p, len(toks)):
            depth += (toks[k].value == "(") - (toks[k].value == ")")
            if depth == 0:
                end = k
                break
        fks = []
        for d in split(toks[p + 1:end], {","}):
            if not self.key_def(name, d, fks):
                self.column_def(name, d)
        self.finish_fks(name, fks)
        log.debug("table %s: %d columns, pk=%s, %d indexes", name, len(self.schema.tables[name]["columns"]),
                  self.schema.tables[name]["primary_key"], len(self.schema.tables[name]["indexes"]))

    @staticmethod
    def table_name(toks, i):
        name = name_of(toks[i])
        if i + 2 < len(toks) and toks[i + 1].value == "." and name_of(toks[i + 2]):
            return name_of(toks[i + 2]), i + 3
        return name, i + 1

    def rename_column(self, t, old, new):
        t["columns"] = [new if c == old else c for c in t["columns"]]
        t["primary_key"] = [new if c == old else c for c in t["primary_key"]]
        for idx in t["indexes"]:
            idx["columns"] = [new if c == old else c for c in idx["columns"]]

    def alter_table(self, toks):
        name, i = self.table_name(toks, 2)
        t = self.schema.table(name, create=True)
        for act in split(toks[i:], {","}):
            words = [low(x) for x in act]
            w0 = words[0]
            if w0 == "add":
                rest = act[2:] if len(act) > 1 and words[1] == "column" else act[1:]
                fks = []
                if rest and rest[0].value == "(":
                    for d in split(rest[1:-1], {","}):
                        self.column_def(name, d)
                elif rest and not self.key_def(name, rest, fks):
                    self.column_def(name, rest)
                self.finish_fks(name, fks)
            elif w0 == "drop" and len(act) > 1:
                w1 = words[1]
                if w1 == "primary":
                    t["primary_key"] = []
                elif w1 in ("index", "key", "constraint", "check") and len(act) > 2:
                    gone = name_of(act[2])
                    t["indexes"] = [x for x in t["indexes"] if x["name"] != gone]
                elif w1 == "foreign":
                    continue
                else:
                    col = name_of(act[2] if w1 == "column" and len(act) > 2 else act[1])
                    t["columns"] = [c for c in t["columns"] if c != col]
                    t["primary_key"] = [c for c in t["primary_key"] if c != col]
                    for idx in t["indexes"]:
                        idx["columns"] = [c for c in idx["columns"] if c != col]
                    t["indexes"] = [x for x in t["indexes"] if x["columns"]]
            elif w0 == "change" and len(act) > 2:
                k = 2 if words[1] == "column" else 1
                if k + 1 < len(act):
                    self.rename_column(t, name_of(act[k]), name_of(act[k + 1]))
            elif w0 == "rename" and len(act) > 1:
                w1 = words[1]
                if w1 == "column" and len(act) > 4:
                    self.rename_column(t, name_of(act[2]), name_of(act[4]))
                elif w1 in ("index", "key") and len(act) > 4:
                    for idx in t["indexes"]:
                        if idx["name"] == name_of(act[2]):
                            idx["name"] = name_of(act[4])
                else:
                    new, _ = self.table_name(act, 2 if w1 in ("to", "as") else 1)
                    self.schema.tables[new] = self.schema.tables.pop(name)
                    log.debug("table renamed %s -> %s", name, new)
                    name, t = new, self.schema.tables[new]

    def statement(self, toks):
        words = [low(x) for x in toks[:4]]
        if words[:2] == ["create", "table"] or words[:3] == ["create", "temporary", "table"]:
            self.create_table(toks)
        elif words[:2] == ["alter", "table"]:
            self.alter_table(toks)
        elif words[0] == "create" and "index" in words[1:3]:
            k = words.index("index")
            on = next((j for j, x in enumerate(toks) if low(x) == "on"), None)
            if on is not None:
                table, j = self.table_name(toks, on + 1)
                self.add_index(table, name_of(toks[k + 1]), _cols(toks[j:]), words[1] == "unique", toks[0])
        elif words[:2] == ["drop", "index"] and len(toks) > 4:
            table, _ = self.table_name(toks, 4)
            t = self.schema.table(table)
            if t:
                t["indexes"] = [x for x in t["indexes"] if x["name"] != name_of(toks[2])]
        elif words[:2] == ["drop", "table"]:
            k = 4 if words[2:4] == ["if", "exists"] else 2
            for part in split(toks[k:], {","}):
                self.schema.tables.pop(self.table_name(part, 0)[0], None)
        elif words[:2] == ["rename", "table"]:
            for part in split(toks[2:], {","}):
                old, j = self.table_name(part, 0)
                if old in self.schema.tables and j + 1 < len(part):
                    self.schema.tables[self.table_name(part, j + 1)[0]] = self.schema.tables.pop(old)


def read_sql_file(schema, path, rel):
    """Apply one formatted-SQL changeset file. Statements split at `;` and at changeset markers."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        log.error("cannot read changeset %s: %s", rel, exc)
        schema.skipped.append("unreadable: %s" % rel)
        return
    marks = [m.start() for m in _CHANGESET.finditer(text)]
    schema.changesets += len(marks)
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def line_of(offset):
        return bisect.bisect_right(starts, offset)

    reader, cur = _Reader(schema, rel, line_of), []
    for tok in tokenize(text) + [None]:
        boundary = tok is None or tok.value == ";" or (
            cur and bisect.bisect_right(marks, cur[-1].start) != bisect.bisect_right(marks, tok.start))
        if boundary and cur:
            try:
                reader.statement(cur)
            except (IndexError, KeyError, TypeError) as exc:
                log.warning("could not read DDL at %s:%d (%s): %s", rel, line_of(cur[0].start), exc,
                            " ".join(t.value for t in cur[:8]))
                schema.skipped.append("DDL not read: %s:%d" % (rel, line_of(cur[0].start)))
            cur = []
        if tok is not None and tok.value != ";":
            cur.append(tok)
    log.debug("changeset file %s: %d changesets", rel, len(marks))


def load_schema(master, service_root, scan_root):
    """Build the Schema for one service. `master` may be None (no Liquibase): empty schema."""
    schema = Schema()
    if not master:
        log.debug("no Liquibase master under %s", service_root)
        return schema
    files, skipped = master_files(master, service_root)
    schema.skipped.extend(skipped)
    for path in files:
        rel = os.path.relpath(path, scan_root).replace(os.sep, "/")
        log.debug("changeset file considered: %s", rel)
        schema.files.append(rel)
        read_sql_file(schema, path, rel)
    log.debug("schema for %s: %d files, %d changesets, %d tables, %d indexes", service_root, len(files),
              schema.changesets, len(schema.tables), sum(len(t["indexes"]) for t in schema.tables.values()))
    return schema
