"""Fixture tests for explain_check.py. Runs the script as a subprocess; stdlib only."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "explain_check.py")
FIXTURES = os.path.join(HERE, "fixtures")
EVALS = os.path.join(os.path.dirname(HERE), "evals")
PG_NESTED_EVAL = os.path.join(EVALS, "pg-nested-loop-misestimate", "files", "plan.json")
MYSQL_EVAL = os.path.join(EVALS, "mysql-covering-index", "files", "plan.json")
QDB_EVAL = os.path.join(EVALS, "questdb-designated-timestamp", "files", "plan.txt")
PROD_DELETE_CONTEXT = os.path.join(EVALS, "prod-delete-explain-analyze", "files", "context.md")

sys.path.insert(0, HERE)
import explain_check  # noqa: E402  (for node lists, which the CLI does not print)


def run(fixture, *extra, stdin=None):
    args = [sys.executable, "-I", SCRIPT, fixture if stdin is None else "-", "--json", *extra]
    path = os.path.join(FIXTURES, fixture)
    if stdin is not None:
        with open(path, encoding="utf-8") as fh:
            stdin = fh.read()
    else:
        args[3] = path
    return subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=30)


def findings(fixture):
    proc = run(fixture, "--top", "100")
    if proc.returncode != 0:
        raise AssertionError(f"{fixture}: exit {proc.returncode}: {proc.stderr}")
    return json.loads(proc.stdout)


def rules(fixture):
    return {f["rule"] for f in findings(fixture)}


def of_rule(fixture, rule):
    return [f for f in findings(fixture) if f["rule"] == rule]


def node_labels(fixture):
    with open(os.path.join(FIXTURES, fixture), encoding="utf-8") as fh:
        kind, doc = explain_check.detect(fh.read(), "auto")
    return [n.label for n in explain_check.analyse(kind, doc)[1]]


def write_tmp(tmp, name, obj):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)
    return path


class ExplainCheckTest(unittest.TestCase):
    def assert_rules(self, fixture, expected):
        got = rules(fixture)
        self.assertTrue(set(expected) <= got, f"{fixture}: missing {set(expected) - got}; got {sorted(got)}")

    def assert_absent(self, fixture, unexpected):
        got = rules(fixture)
        self.assertFalse(set(unexpected) & got, f"{fixture}: unexpected {set(unexpected) & got}; got {sorted(got)}")

    # ------------------------------------------------------------ PostgreSQL

    def test_pg_nested_loop_misestimate(self):
        for plan in ("pg_nested_loop_misestimate.json", PG_NESTED_EVAL):
            with self.subTest(plan=plan):
                mis = of_rule(plan, "pg.row-misestimate")
                self.assertEqual(len(mis), 1, mis)
                self.assertIn("users u", mis[0]["node"]["label"])
                self.assertEqual(mis[0]["node"]["type"], "Bitmap Heap Scan")
                self.assertEqual(mis[0]["evidence"]["est_rows"], 2)
                self.assertEqual(mis[0]["evidence"]["actual_rows"], 587)
                self.assertLessEqual(mis[0]["evidence"]["est_ratio"], 0.01)
                self.assertIn("propagates to Nested Loop", mis[0]["summary"])
                self.assert_rules(plan, ["pg.hot-node"])
                # Top-N heapsort stays in memory; the inner side runs 587 loops, under the 1,000 threshold.
                self.assert_absent(plan, ["pg.sort-disk", "pg.nested-loop-heavy"])

    def test_pg_text_analyze_matches_json(self):
        self.assertEqual(rules("pg_text_analyze.txt"), rules("pg_nested_loop_misestimate.json"))

    def test_pg_text_limit_guard(self):
        mis = of_rule("pg_text_analyze.txt", "pg.row-misestimate")
        self.assertEqual(len(mis), 1, mis)
        self.assertEqual(mis[0]["node"]["label"], "Bitmap Heap Scan on users u")

    def test_pg_fractional_rows_formatted(self):
        inner = {"Node Type": "Index Scan", "Parent Relationship": "Inner", "Relation Name": "orders",
                 "Alias": "o", "Index Name": "idx_orders_user_id", "Startup Cost": 0.29, "Total Cost": 8.3,
                 "Plan Rows": 12, "Plan Width": 8, "Actual Startup Time": 0.001, "Actual Total Time": 0.002,
                 "Actual Rows": 12.40, "Actual Loops": 5000}
        outer = {"Node Type": "Seq Scan", "Parent Relationship": "Outer", "Relation Name": "users", "Alias": "u",
                 "Startup Cost": 0.0, "Total Cost": 80.0, "Plan Rows": 5000, "Plan Width": 8,
                 "Actual Startup Time": 0.01, "Actual Total Time": 1.0, "Actual Rows": 5000, "Actual Loops": 1}
        loop = {"Node Type": "Nested Loop", "Startup Cost": 0.29, "Total Cost": 41580.0, "Plan Rows": 60000,
                "Plan Width": 16, "Actual Startup Time": 0.02, "Actual Total Time": 15.0, "Actual Rows": 62000,
                "Actual Loops": 1, "Plans": [outer, inner]}
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tmp(tmp, "fractional.json",
                             [{"Plan": loop, "Planning Time": 0.1, "Triggers": [], "Execution Time": 16.0}])
            proc = subprocess.run([sys.executable, "-I", SCRIPT, path, "--top", "100"],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("5,000 loops x 12 rows/loop", proc.stdout)
        self.assertNotIn("12.4", proc.stdout)

    def test_pg_estimates_only(self):
        for plan in ("pg_estimates_only.json", "pg_text_estimates.txt"):
            with self.subTest(plan=plan):
                self.assert_rules(plan, ["pg.no-analyze"])
                self.assert_absent(plan, ["pg.seq-scan-filtered"])
                self.assertIn("Bitmap Heap Scan on events", node_labels(plan))

    def test_pg_sort_disk_hash_batches(self):
        self.assert_rules("pg_sort_disk_hash_batches.json", ["pg.sort-disk", "pg.hash-batches"])
        self.assert_absent("pg_sort_disk_hash_batches.json", ["pg.heap-fetches"])
        hb = of_rule("pg_sort_disk_hash_batches.json", "pg.hash-batches")
        self.assertEqual([f["evidence"]["hash_batches"] for f in hb], [64], hb)

    def test_pg_heap_fetches(self):
        hf = of_rule("pg_heap_fetches.json", "pg.heap-fetches")
        self.assertEqual(len(hf), 1, hf)
        self.assertIn("182,864 heap fetches", hf[0]["summary"])

    def test_prod_delete_seq_scan_estimate(self):
        with open(PROD_DELETE_CONTEXT, encoding="utf-8") as fh:
            block = re.search(r"```json\n(.*?)```", fh.read(), re.S)
        self.assertIsNotNone(block, "no ```json block in context.md")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tmp(tmp, "prod_delete.json", block.group(1))
            found = findings(path)
        got = {f["rule"] for f in found}
        self.assertIn("pg.no-analyze", got)
        seq = [f for f in found if f["rule"] == "pg.seq-scan-filtered"]
        self.assertEqual([f["evidence"]["est_rows"] for f in seq], [11642], seq)
        self.assertIn("est 11,642 rows", seq[0]["summary"])

    def test_pg_psql_wrapped_matches_plain(self):
        plain = run("pg_estimates_only.json")
        wrapped = run("pg_psql_wrapped.txt")
        self.assertEqual(wrapped.returncode, 0, wrapped.stderr)
        self.assertEqual(json.loads(plain.stdout), json.loads(wrapped.stdout))

    def test_pg_text_psql_wrapped_matches_plain(self):
        with open(os.path.join(FIXTURES, "pg_text_analyze.txt"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        width = max(len(ln) for ln in lines)
        wrapped = ["QUERY PLAN".center(width + 2).rstrip(), "-" * (width + 2)] + [" " + ln for ln in lines]
        wrapped.append(f"({len(lines)} rows)")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tmp(tmp, "wrapped.txt", "\n".join(wrapped) + "\n")
            self.assertEqual(findings("pg_text_analyze.txt"), findings(path))

    def test_under_estimate_below_limit_still_fires(self):
        scan = {"Node Type": "Seq Scan", "Parent Relationship": "Outer", "Relation Name": "events", "Alias": "e",
                "Startup Cost": 0.0, "Total Cost": 155.0, "Plan Rows": 10, "Plan Width": 8,
                "Actual Startup Time": 0.01, "Actual Total Time": 4.2, "Actual Rows": 5000, "Actual Loops": 1}
        limit = {"Node Type": "Limit", "Startup Cost": 0.0, "Total Cost": 155.0, "Plan Rows": 10, "Plan Width": 8,
                 "Actual Startup Time": 0.01, "Actual Total Time": 4.9, "Actual Rows": 5000, "Actual Loops": 1,
                 "Plans": [scan]}
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tmp(tmp, "limit_under.json",
                             [{"Plan": limit, "Planning Time": 0.1, "Triggers": [], "Execution Time": 5.0}])
            mis = [f for f in findings(path) if f["rule"] == "pg.row-misestimate"]
        self.assertEqual([f["node"]["label"] for f in mis], ["Seq Scan on events e"], mis)

    def test_stdin_matches_file(self):
        via_file = run("pg_nested_loop_misestimate.json")
        via_stdin = run("pg_nested_loop_misestimate.json", stdin=True)
        self.assertEqual(via_stdin.returncode, 0, via_stdin.stderr)
        self.assertEqual(json.loads(via_file.stdout), json.loads(via_stdin.stdout))

    # ------------------------------------------------------------ MySQL

    def test_mysql_index_scan_filter(self):
        found = findings(MYSQL_EVAL)
        got = {f["rule"] for f in found}
        self.assertIn("my.no-analyze", got)
        self.assertNotIn("my.filesort", got)  # using_filesort is false: the index supplies the order
        isf = [f for f in found if f["rule"] == "my.index-scan-filter"]
        self.assertEqual(len(isf), 1, isf)
        self.assertEqual(isf[0]["severity"], "medium")
        self.assertEqual(isf[0]["node"]["index"], "idx_orders_created")
        self.assertIs(isf[0]["evidence"]["backward_index_scan"], True)
        self.assertEqual(isf[0]["evidence"]["filtered_pct"], 0.5)
        self.assertIn("397 rows/scan", isf[0]["summary"])

    def test_mysql_index_scan_filter_supersedes_full(self):
        with open(MYSQL_EVAL, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["query_block"]["ordering_operation"]["table"]["rows_examined_per_scan"] = 500_000
        with tempfile.TemporaryDirectory() as tmp:
            got = rules(write_tmp(tmp, "big_walk.json", doc))
        self.assertIn("my.index-scan-filter", got)
        self.assertNotIn("my.index-scan-full", got)

    def test_mysql_json_full_scan(self):
        def by_rule(rule):
            return [(f["severity"], f["node"]["relation"]) for f in found if f["rule"] == rule]

        found = findings("mysql_json_full_scan.json")
        self.assertEqual(by_rule("my.full-scan"), [("medium", "c")])  # 48,720 rows, under the 100k HIGH line
        self.assertEqual(by_rule("my.filesort"), [("medium", None)])  # 187,148 rows produced by the join
        self.assertEqual(by_rule("my.not-covering"), [("medium", "o")])
        self.assertEqual(by_rule("my.low-filter"), [("medium", "c")])
        self.assertEqual(by_rule("my.key-unused"), [("medium", "c")])
        self.assertIn("filesort over 187,148 input rows", [f["summary"] for f in found if f["rule"] == "my.filesort"])

    def test_mysql_filesort_not_covering_tiers(self):
        def severity(found, rule):
            return [f["severity"] for f in found if f["rule"] == rule]

        with open(os.path.join(FIXTURES, "mysql_json_full_scan.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        for entry in doc["query_block"]["ordering_operation"]["nested_loop"]:
            entry["table"]["rows_produced_per_join"] = 120
        with tempfile.TemporaryDirectory() as tmp:
            small = findings(write_tmp(tmp, "small.json", doc))
        self.assertEqual(severity(small, "my.filesort"), ["low"])
        self.assertEqual(severity(small, "my.not-covering"), ["low"])

    def test_mysql_tree_analyze(self):
        found = findings("mysql_tree_analyze.txt")
        got = {f["rule"] for f in found}
        self.assertIn("my.hot-node", got)
        self.assertEqual([f["node"]["label"] for f in found if f["rule"] == "my.full-scan"], ["Table scan on c"])
        self.assertEqual([f["severity"] for f in found if f["rule"] == "my.filesort"], ["medium"])  # 30,000 rows
        # Estimates are close in the real plan; the inner lookup runs 750 loops, under 1,000.
        self.assertFalse({"my.row-misestimate", "my.nested-loop-heavy"} & got, sorted(got))

    def test_wrong_format_exits_2(self):
        proc = run("mysql_tree_analyze.txt", "--format", "pg-json")
        self.assertEqual(proc.returncode, 2)

    # ------------------------------------------------------------ QuestDB

    def test_questdb_full_scan(self):
        for plan in ("questdb_full_scan.txt", QDB_EVAL):
            with self.subTest(plan=plan):
                found = findings(plan)
                got = {f["rule"] for f in found}
                self.assertIn("qdb.no-analyze", got)
                self.assertEqual([f["severity"] for f in found if f["rule"] == "qdb.full-frame-scan"], ["high"])
                self.assertNotIn("qdb.sort", got)  # ORDER BY ts DESC is a Frame backward scan, no sort

    def test_questdb_interval_scan(self):
        self.assertEqual(rules("questdb_interval_scan.txt"), {"qdb.no-analyze"})

    def test_questdb_psql_wrapped_matches_plain(self):
        self.assertEqual(findings("questdb_full_scan.txt"), findings("questdb_psql_wrapped.txt"))

    # ------------------------------------------------------------ output and errors

    def test_rule_column_aligned(self):
        proc = subprocess.run([sys.executable, "-I", SCRIPT, os.path.join(FIXTURES, "questdb_full_scan.txt")],
                              capture_output=True, text=True, timeout=30)
        heads = [ln for ln in proc.stdout.splitlines() if ln[:6].strip() in ("HIGH", "MEDIUM", "LOW")]
        starts = {re.match(r"\S+ +\S+ +", ln).end() for ln in heads}
        self.assertEqual(len(heads), 2, proc.stdout)
        self.assertEqual(starts, {7 + len("qdb.single-thread-filter") + 1}, heads)

    def test_garbage_exits_2(self):
        proc = run("garbage.txt")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1, proc.stderr)


if __name__ == "__main__":
    unittest.main()
