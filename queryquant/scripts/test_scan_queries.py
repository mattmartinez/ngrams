"""Tests for scan_queries.py and the qq_scan package. Stdlib only; builds tiny services in a temp dir."""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "scan_queries.py")
sys.path.insert(0, HERE)
from qq_scan import config as config_mod  # noqa: E402
from qq_scan import liquibase, scan, sql_shape  # noqa: E402

logging.getLogger("qq_scan").addHandler(logging.NullHandler())  # keep expected error logs out of test output

MASTER = """<?xml version="1.0" encoding="UTF-8"?>
<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog">
    <include file="changelog/001-base.sql"/>
    <include file="changelog/002-index.sql"/>
</databaseChangeLog>
"""
BASE_SQL = """-- liquibase formatted sql

-- changeset t:001-1
CREATE TABLE IF NOT EXISTS `user` (id bigint NOT NULL AUTO_INCREMENT COMMENT 'pk, with a comma', user_guid varchar(36) NOT NULL, email varchar(255), status varchar(20), create_date timestamp DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id), CONSTRAINT user_guid UNIQUE (user_guid)) ENGINE=InnoDB;

-- changeset t:001-2
CREATE TABLE role (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    role_guid VARCHAR(36) NOT NULL,
    name VARCHAR(64) NOT NULL,
    UNIQUE KEY uk_role_guid (role_guid)
);

-- changeset t:001-3
CREATE TABLE user_role (user_id bigint NOT NULL, role_id bigint NOT NULL, tenant_id varchar(36), INDEX fk_user_role_user (user_id), FOREIGN KEY (role_id) REFERENCES role (id));

-- changeset t:001-4
CREATE TABLE event (id bigint NOT NULL AUTO_INCREMENT, tenant_id varchar(36) NOT NULL, kind varchar(20), created_at datetime, PRIMARY KEY (id))
-- rollback DROP TABLE event;
"""
INDEX_SQL = """--liquibase formatted sql
--changeset t:002-1
ALTER TABLE event ADD INDEX idx_event_tenant_created (tenant_id, created_at);
--changeset t:002-2
CREATE INDEX idx_event_kind ON event (kind, id);
ALTER TABLE `user` CHANGE COLUMN email email_address VARCHAR(255) NULL;
"""
ARCHIVED_SQL = """-- liquibase formatted sql
-- changeset t:000-1
CREATE TABLE ghost (id bigint, PRIMARY KEY (id));
"""

USER_DAO = '''package svc.dao;

import java.util.List;
import java.util.Optional;

public interface UserDAO {

    String COLS = "SELECT id, user_guid, email_address FROM `user` ";

    @SqlQuery(COLS + "WHERE user_guid = :guid")
    Optional<User> findByGuid(@Bind("guid") String guid);

    @SqlQuery("SELECT * FROM `user` "
            + "WHERE status = :status")
    List<User> findByStatus(@Bind("status") String status);

    @SqlQuery("""
        SELECT * FROM `user`
         WHERE (:status IS NULL OR status = :status)
         ORDER BY create_date DESC
         LIMIT :limit OFFSET :offset
        """)
    List<User> search(@Bind("status") String status, @Bind("limit") int limit, @Bind("offset") int offset);

    @SqlQuery("SELECT * FROM `user`")
    List<User> getAll();

    @SqlQuery("SELECT * FROM `user` WHERE user_guid IN (<guids>)")
    List<User> findByGuids(@BindList("guids") List<String> guids);

    @SqlUpdate("UPDATE `user` SET status = :status WHERE id = :id")
    void setStatus(@Bind("id") long id, @Bind("status") String status);

    @SqlBatch("INSERT INTO `user` (user_guid) VALUES (:guid)")
    void insertAll(@BindBean List<User> users);

    @BatchChunkSize(500)
    @SqlBatch("INSERT INTO `user` (user_guid) VALUES (:guid)")
    void insertAllChunked(@BindBean List<User> users);

    @SqlQuery("SELECT * FROM `user` WHERE <where> LIMIT 10")
    List<User> dynamic(@Define("where") String where);
}
'''
EVENT_DAO = '''package svc.dao;

@BatchChunkSize(1000)
public interface EventDAO {
    @SqlQuery("SELECT * FROM event WHERE tenant_id = :p AND id < :after ORDER BY id DESC LIMIT :limit")
    List<Event> page(@Bind("p") String p, @Bind("after") long after, @Bind("limit") int limit);

    @SqlQuery("SELECT * FROM event WHERE tenant_id = :p ORDER BY created_at LIMIT 50")
    List<Event> recent(@Bind("p") String p);

    @SqlQuery("SELECT * FROM event WHERE kind = :k ORDER BY created_at DESC LIMIT 20")
    List<Event> byKind(@Bind("k") String k);

    @SqlBatch("INSERT INTO event (tenant_id, kind) VALUES (:p, :k)")
    void insertAll(@BindBean List<Event> events);

    @SqlQuery("SELECT * FROM event WHERE kind = :k")
    List<Event> kindRows(@Bind("k") String k);

    @SqlQuery("SELECT * FROM user_role WHERE user_id = :u")
    List<Event> userRoles(@Bind("u") long u);

    @SqlQuery("SELECT * FROM QRTZ_LOCKS")
    List<Lock> locks();
}
'''
EVENT_REPO = '''package svc.dao;

public class EventRepo {
    private static final String BY_ID = "SELECT * FROM event WHERE id = :id";
    private final Jdbi jdbi;

    public Event byId(long id) {
        return jdbi.withHandle(h -> h.createQuery(BY_ID).bind("id", id).mapTo(Event.class).one());
    }

    public List<Event> missing(Handle handle) {
        String sql = "SELECT * FROM ghost WHERE id = :id";
        return handle.createQuery(sql).bind("id", 1).mapTo(Event.class).list();
    }
}
'''
USER_SERVICE = '''package svc.service;

public class UserService {
    private final UserDAO userDAO;
    private final EventRepo eventRepo;

    public void touchAll(List<String> guids) {
        for (String guid : guids) {
            User u = userDAO.findByGuid(guid).orElseThrow();
            userDAO.setStatus(u.getId(), "SEEN");
        }
    }

    public List<User> viaStream(List<String> guids) {
        return guids.stream()
                .map(g -> userDAO.findByGuid(g))
                .collect(Collectors.toList());
    }

    public void viaRepo(List<Long> ids) {
        ids.forEach(id -> eventRepo.byId(id));
    }

    public void chunked(List<String> guids) {
        for (List<String> chunk : Lists.partition(guids, 100)) {
            userDAO.findByGuids(chunk);
        }
    }

    public Optional<User> notALoop(Optional<String> guid) {
        return guid.map(g -> userDAO.findByGuid(g).orElse(null));
    }

    public List<User> listNested(List<String> guids) {
        guids.forEach(g -> {
            loadOne(g);
        });
        return null;
    }

    private void loadOne(String g) {
        loadInner(g);
    }

    private void loadInner(String g) {
        for (String x : List.of(g)) {
            userDAO.findByGuid(x);
        }
    }

    @POST
    public void saveAll(List<String> guids) {
        for (String g : guids) {
            this.lookup(g);
        }
    }

    private User lookup(String g) {
        return userDAO.findByGuid(g).orElse(null);
    }

    public void paging(String p) {
        long after = Long.MAX_VALUE;
        while (true) {
            EventDAO dao = handle.attach(EventDAO.class);
            List<Event> page = dao.page(p, after, 100);
            if (page.isEmpty()) {
                break;
            }
        }
    }
}
'''


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def build_service(root):
    """A one-service repo at root (folder name 'svc' when root ends in svc)."""
    write(root, "settings.gradle", "rootProject.name = 'svc'\n")
    write(root, "svc-service/src/main/resources/migrations.xml", MASTER)
    write(root, "svc-service/src/main/resources/changelog/001-base.sql", BASE_SQL)
    write(root, "svc-service/src/main/resources/changelog/002-index.sql", INDEX_SQL)
    write(root, "svc-service/src/main/resources/changelog/000-archived.sql", ARCHIVED_SQL)
    java = "svc-service/src/main/java/svc/"
    write(root, java + "dao/UserDAO.java", USER_DAO)
    write(root, java + "dao/EventDAO.java", EVENT_DAO)
    write(root, java + "dao/EventRepo.java", EVENT_REPO)
    write(root, java + "service/UserService.java", USER_SERVICE)
    write(root, "svc-service/src/test/java/svc/FakeDAO.java",
          'interface FakeDAO { @SqlQuery("SELECT * FROM nowhere") List<X> all(); }\n')
    write(root, "svc-service/bin/main/svc/dao/UserDAO.java", USER_DAO)


def line_of(text, needle, nth=1):
    lines = [i + 1 for i, line in enumerate(text.splitlines()) if needle in line]
    return lines[nth - 1]


class ScanFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmp.name, "svc")
        build_service(cls.root)
        cls.result, cls.extras = scan(cls.root, config_mod.Config())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def stmt(self, dao, method):
        hits = [s for s in self.result["statements"] if s["dao"] == dao and s["method"] == method]
        self.assertEqual(len(hits), 1, "%s.%s: %s" % (dao, method, hits))
        return hits[0]

    def findings(self, rule, **match):
        return [f for f in self.result["findings"] if f["rule"] == rule
                and all(f.get(k) == v for k, v in match.items())]

    def finding_for(self, rule, dao, method):
        sid = self.stmt(dao, method)["id"]
        return [f for f in self.result["findings"] if f["rule"] == rule and f["statement_id"] == sid]


class ServicesAndSchema(ScanFixture):
    def test_one_service_named_after_root(self):
        names = [s["name"] for s in self.result["services"]]
        self.assertEqual(names, ["svc"])
        self.assertEqual(self.result["services"][0]["master"], "svc-service/src/main/resources/migrations.xml")

    def test_only_included_changesets(self):
        svc = self.result["services"][0]
        self.assertNotIn("ghost", svc["tables"])
        self.assertEqual(svc["changesets"], 6)

    def test_one_line_create_table(self):
        user = self.result["services"][0]["tables"]["user"]
        self.assertEqual(user["primary_key"], ["id"])
        self.assertIn({"name": "user_guid", "columns": ["user_guid"], "unique": True}, user["indexes"])
        self.assertIn("email_address", user["columns"])
        self.assertNotIn("email", user["columns"])

    def test_alter_add_index_create_index_and_fk(self):
        tables = self.result["services"][0]["tables"]
        event = {i["name"]: i["columns"] for i in tables["event"]["indexes"]}
        self.assertEqual(event["idx_event_tenant_created"], ["tenant_id", "created_at"])
        self.assertEqual(event["idx_event_kind"], ["kind", "id"])
        user_role = {i["name"]: i["columns"] for i in tables["user_role"]["indexes"]}
        self.assertEqual(user_role.get("role_id"), ["role_id"], "implicit FK index")
        self.assertEqual(tables["role"]["primary_key"], ["id"])

    def test_statement_without_semicolon_ends_at_next_changeset(self):
        self.assertIn("event", self.result["services"][0]["tables"])


class Extraction(ScanFixture):
    def test_counts_and_ignored_folders(self):
        files = {s["file"] for s in self.result["statements"]}
        self.assertFalse([f for f in files if "/bin/" in f or "src/test" in f])
        self.assertEqual(sum(1 for s in self.result["statements"] if s["source"] == "sqlobject"), 16)

    def test_constant_plus_literal(self):
        s = self.stmt("UserDAO", "findByGuid")
        self.assertEqual(s["sql"], "SELECT id, user_guid, email_address FROM `user` WHERE user_guid = :guid")
        self.assertEqual(s["line"], line_of(USER_DAO, "@SqlQuery(COLS"))
        self.assertEqual((s["kind"], s["source"], s["returns"]), ("select", "sqlobject", "Optional"))

    def test_concatenation(self):
        s = self.stmt("UserDAO", "findByStatus")
        self.assertEqual(s["sql"], "SELECT * FROM `user` WHERE status = :status")
        self.assertEqual(s["returns"], "List")

    def test_text_block(self):
        s = self.stmt("UserDAO", "search")
        self.assertTrue(s["sql"].startswith("SELECT * FROM `user` WHERE (:status IS NULL OR status = :status)"))
        self.assertEqual(s["line"], line_of(USER_DAO, '@SqlQuery("""'))

    def test_annotations_and_returns(self):
        self.assertIn("@BindList", self.stmt("UserDAO", "findByGuids")["annotations"])
        self.assertIn("@BatchChunkSize", self.stmt("UserDAO", "insertAllChunked")["annotations"])
        self.assertEqual(self.stmt("UserDAO", "setStatus")["returns"], "void")
        self.assertEqual(self.stmt("UserDAO", "setStatus")["kind"], "update")

    def test_define_is_not_analysed(self):
        s = self.stmt("UserDAO", "dynamic")
        self.assertFalse(s["analysed"])
        self.assertIn("<where>", s["not_analysed_reason"])

    def test_fluent_constant_and_local(self):
        s = self.stmt("EventRepo", "byId")
        self.assertEqual((s["source"], s["returns"], s["sql"]), ("fluent", "single", "SELECT * FROM event WHERE id = :id"))
        self.assertEqual(s["line"], line_of(EVENT_REPO, "createQuery(BY_ID)"))
        local = self.stmt("EventRepo", "missing")
        self.assertEqual(local["sql"], "SELECT * FROM ghost WHERE id = :id")
        self.assertEqual(local["returns"], "List")


class Rules(ScanFixture):
    def test_no_index_for_filter(self):
        f = self.finding_for("qq.no-index-for-filter", "UserDAO", "findByStatus")
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["severity"], f[0]["needs_table_size"]), ("medium", True))
        self.assertEqual(f[0]["evidence"]["equality_columns"], ["status"])
        self.assertIn("PRIMARY(id)", f[0]["evidence"]["indexes"])

    def test_indexed_filters_are_not_flagged(self):
        for method in ("findByGuid", "findByGuids", "setStatus"):
            self.assertEqual(self.finding_for("qq.no-index-for-filter", "UserDAO", method), [], method)
        self.assertEqual(self.finding_for("qq.no-index-for-filter", "EventDAO", "page"), [])

    def test_table_not_in_schema_is_low(self):
        f = self.finding_for("qq.no-index-for-filter", "EventRepo", "missing")
        self.assertEqual([(x["severity"], x["evidence"]["note"]) for x in f], [("low", "table not found")])

    def test_no_index_for_sort(self):
        self.assertEqual(len(self.finding_for("qq.no-index-for-sort", "EventDAO", "byKind")), 1)
        self.assertEqual(self.finding_for("qq.no-index-for-sort", "EventDAO", "recent"), [])
        self.assertEqual(self.finding_for("qq.no-index-for-sort", "EventDAO", "page"), [], "keyset on pk suffix")

    def test_catch_all_filter(self):
        f = self.finding_for("qq.catch-all-filter", "UserDAO", "search")
        self.assertEqual([(x["severity"], x["evidence"]["columns"]) for x in f], [("high", ["status"])])

    def test_offset_paging(self):
        self.assertEqual(len(self.finding_for("qq.offset-paging", "UserDAO", "search")), 1)
        self.assertEqual(self.finding_for("qq.offset-paging", "EventDAO", "page"), [])

    def test_unbounded_list(self):
        self.assertEqual([x["severity"] for x in self.finding_for("qq.unbounded-list", "UserDAO", "getAll")],
                         ["high"])
        self.assertEqual([x["severity"] for x in self.finding_for("qq.unbounded-list", "UserDAO", "findByStatus")],
                         ["medium"])
        self.assertEqual(self.finding_for("qq.unbounded-list", "UserDAO", "findByGuids"), [])
        nonunique = self.finding_for("qq.unbounded-list", "EventDAO", "kindRows")
        self.assertEqual([(x["severity"], x["evidence"]["non_unique_indexed_filters"]) for x in nonunique],
                         [("medium", ["kind"])])
        self.assertEqual(self.finding_for("qq.unbounded-list", "EventDAO", "userRoles"), [], "parent key")

    def test_framework_table_is_low(self):
        f = self.finding_for("qq.unbounded-list", "EventDAO", "locks")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "low")
        self.assertTrue(f[0]["summary"].endswith("(framework table, usually small)"))
        self.assertEqual(self.result["findings"][-1]["severity"], "low")

    def test_path_kinds(self):
        kinds = {(x["file"].split("/")[-1], x["line"]): x["path_kind"] for x in self.findings("qq.n-plus-one")}
        self.assertEqual(kinds[("UserService.java", line_of(USER_SERVICE, "this.lookup(g)"))], "write")
        self.assertEqual(kinds[("UserService.java", line_of(USER_SERVICE, "loadOne(g);"))], "read")
        self.assertEqual(kinds[("UserService.java", line_of(USER_SERVICE, "userDAO.findByGuid(guid)"))], "unknown")
        self.assertEqual(self.finding_for("qq.unbounded-list", "UserDAO", "getAll")[0]["path_kind"], "read")
        self.assertEqual(self.finding_for("qq.unchunked-batch", "UserDAO", "insertAll")[0]["path_kind"], "write")

    def test_nested_ranks_first(self):
        first = self.result["findings"][0]
        self.assertEqual((first["rule"], first["evidence"]["nested"]), ("qq.n-plus-one", True))

    def test_unchunked_batch(self):
        f = self.findings("qq.unchunked-batch")
        self.assertEqual([(x["file"].split("/")[-1], x["line"]) for x in f],
                         [("UserDAO.java", line_of(USER_DAO, "@SqlBatch", 1))])

    def test_n_plus_one_loop_and_lambda(self):
        f = self.findings("qq.n-plus-one")
        got = sorted((x["file"].split("/")[-1], x["line"], x["evidence"]["call"]) for x in f)
        want = sorted([
            ("UserService.java", line_of(USER_SERVICE, "userDAO.findByGuid(guid)"), "userDAO.findByGuid"),
            ("UserService.java", line_of(USER_SERVICE, ".map(g -> userDAO.findByGuid(g))"), "userDAO.findByGuid"),
            ("UserService.java", line_of(USER_SERVICE, "eventRepo.byId(id)"), "eventRepo.byId"),
            ("UserService.java", line_of(USER_SERVICE, "userDAO.findByGuid(x)"), "userDAO.findByGuid"),
            ("UserService.java", line_of(USER_SERVICE, "loadOne(g);"), "loadOne"),
            ("UserService.java", line_of(USER_SERVICE, "this.lookup(g)"), "lookup"),
        ])
        self.assertEqual(got, want)
        loop = [x for x in f if x["line"] == line_of(USER_SERVICE, "userDAO.findByGuid(guid)")][0]
        self.assertEqual(loop["evidence"]["loop_line"], line_of(USER_SERVICE, "for (String guid"))
        self.assertEqual(loop["statement_id"], self.stmt("UserDAO", "findByGuid")["id"])
        self.assertEqual(loop["severity"], "high")

    def test_helper_n_plus_one(self):
        nested = [x for x in self.findings("qq.n-plus-one") if x["line"] == line_of(USER_SERVICE, "loadOne(g);")][0]
        self.assertTrue(nested["evidence"]["nested"])
        self.assertTrue(nested["summary"].startswith("nested N+1: for every item in guids, loadOne runs userDAO.findByGuid"))
        self.assertEqual(nested["evidence"]["reaches"][0]["via"], ["loadOne", "loadInner"])
        self.assertEqual(nested["evidence"]["loop_line"], line_of(USER_SERVICE, "guids.forEach(g -> {"))
        flat = [x for x in self.findings("qq.n-plus-one") if x["line"] == line_of(USER_SERVICE, "this.lookup(g)")][0]
        self.assertFalse(flat["evidence"]["nested"])
        self.assertEqual(flat["statement_id"], self.stmt("UserDAO", "findByGuid")["id"])

    def test_finding_shape(self):
        keys = {"rule", "severity", "service", "file", "line", "statement_id", "summary", "evidence",
                "evidence_level", "needs_table_size", "path_kind"}
        for f in self.result["findings"]:
            self.assertEqual(set(f), keys)
            self.assertEqual(f["evidence_level"], "from code")


class Shape(unittest.TestCase):
    def test_variants(self):
        s = sql_shape.shape("SELECT a FROM t WHERE (a = 1 AND (:x IS NULL OR c IN (<cs>))) "
                            "AND COALESCE(:y, d) = d ORDER BY a LIMIT 5, 10", ["cs"])
        self.assertEqual(sorted(c["columns"][0] for c in s["catch_all"]), ["c", "d"])
        self.assertEqual((s["limit"], s["offset"]), ("10", "5"))
        self.assertEqual(sql_shape.shape("WITH x AS (SELECT 1) SELECT * FROM x")["not_analysed"],
                         "common table expression (WITH)")
        self.assertEqual(sql_shape.shape("SELECT a FROM t UNION SELECT a FROM u")["not_analysed"], "UNION")
        ex = sql_shape.shape("SELECT EXISTS (SELECT 1 FROM t WHERE id = :id)")
        self.assertEqual((ex["tables"][0]["name"], ex["preds"][0]["column"]), ("t", "id"))

    def test_join_columns(self):
        s = sql_shape.shape("SELECT * FROM a JOIN b ON b.a_id = a.id WHERE a.x BETWEEN 1 AND 2")
        self.assertEqual([(p["alias"], p["column"], p["op"]) for p in s["preds"]], [("a", "x", "range")])
        self.assertEqual(s["join_preds"][0]["rhs_ref"], ("a", "id"))


class Config(unittest.TestCase):
    def test_accept_ignore_and_leading_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "svc")
            build_service(root)
            write(root, ".queryquant.json", json.dumps({
                "ignore": ["**/service/**"],
                "accept": [{"rule": "qq.unbounded-list", "path": "**/UserDAO.java", "reason": "small table"}],
                "leading_column": {"column": "tenant_id"},
                "small_tables": ["event"]}))
            cfg = config_mod.load(root)
            result, extras = scan(root, cfg)
        rules = [f["rule"] for f in result["findings"]]
        self.assertNotIn("qq.n-plus-one", rules, "service/ folder ignored")
        self.assertFalse([f for f in result["findings"] if f["rule"] == "qq.unbounded-list"
                          and f["file"].endswith("UserDAO.java")])
        self.assertEqual(extras["accepted"], 2)
        tenant = sorted((f["evidence"]["table"], f["evidence"]["index"]) for f in result["findings"]
                        if f["rule"] == "qq.tenant-column-not-leading")
        self.assertEqual(tenant, [("event", "idx_event_kind"), ("user_role", "fk_user_role_user"),
                                  ("user_role", "role_id")])
        f = [x for x in result["findings"] if x["rule"] == "qq.tenant-column-not-leading"
             and x["evidence"]["table"] == "user_role"][0]
        self.assertTrue(f["file"].endswith(".sql") and f["line"] > 0 and f["statement_id"] is None)
        sort = [x for x in result["findings"] if x["rule"] == "qq.no-index-for-sort"
                and x["evidence"]["table"] == "event"][0]
        self.assertEqual(sort["severity"], "low")
        self.assertTrue(sort["summary"].endswith("(small table per config)"))

    def test_bad_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, ".queryquant.json", "{not json")
            with self.assertRaises(ValueError):
                config_mod.load(tmp)


class Liquibase(unittest.TestCase):
    def test_drop_rename_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "c.sql", textwrap.dedent("""\
                -- liquibase formatted sql
                -- changeset a:1
                CREATE TABLE t (id INT, a INT COMMENT 'it''s; tricky', b INT, PRIMARY KEY (id), KEY k_a (a), KEY k_b (b(10) DESC));
                ALTER TABLE t DROP INDEX k_a, RENAME INDEX k_b TO k_b2, ADD UNIQUE KEY uk_ab (a, b);
                ALTER TABLE t RENAME TO t2;
                DROP INDEX uk_ab ON t2;
                """))
            schema = liquibase.Schema()
            liquibase.read_sql_file(schema, path, "c.sql")
        t2 = schema.export()["t2"]
        self.assertEqual([(i["name"], i["columns"]) for i in t2["indexes"]], [("k_b2", ["b"])])
        self.assertEqual(t2["columns"], ["id", "a", "b"])


class Cli(unittest.TestCase):
    def test_text_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "svc")
            build_service(root)
            out = os.path.join(tmp, "out.json")
            proc = subprocess.run([sys.executable, "-I", SCRIPT, root, "--top", "3", "--json", out],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
        lines = proc.stdout.strip().splitlines()
        pattern = re.compile(r"^\d+\. svc · \S+\.java:\d+ · \S+ · .+ · (read|write|job|unknown) path · from code$")
        for line in lines[:3]:
            self.assertRegex(line, pattern)
        groups = lines[3:-1]
        self.assertTrue(1 <= len(groups) <= 5, groups)
        for line in groups:
            self.assertRegex(line, r"^\+ \d+ more .+ \(\S+ ×\d+.*\)")
        counts = [int(line.split()[1]) for line in groups]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertRegex(lines[-1], r"^… and \d+ more \(1 not analysed, 0 accepted by config\)$")
        self.assertIn("N+1", lines[0])
        self.assertEqual(set(data), {"scanner_version", "root", "services", "statements", "findings"})
        self.assertEqual(set(data["statements"][0]), {
            "id", "service", "file", "line", "kind", "source", "dao", "method", "returns", "annotations",
            "sql", "analysed", "not_analysed_reason"})
        self.assertEqual(data["statements"][0]["id"], "S0001")

    def test_verbose_logs_to_stderr_and_bad_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_service(tmp)
            proc = subprocess.run([sys.executable, "-I", SCRIPT, tmp, "--verbose", "--top", "1"],
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for needle in ("file considered:", "statement ", "finding qq."):
            self.assertIn(needle, proc.stderr)
        bad = subprocess.run([sys.executable, "-I", SCRIPT, "/nonexistent/qq"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
