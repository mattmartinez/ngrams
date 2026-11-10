#!/usr/bin/env python3
"""Unit tests for evaluate.py. Standard library only.

Run from the repo root or from this folder:
    python3 -I bug-hunt-research/test_evaluate.py
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evaluate  # noqa: E402


# A small answer key: two planted bugs, one trap, one neutral entry.
MANIFEST = {
    "bugs": [
        {"file": "app/auth.py", "line_start": 40, "line_end": 45, "severity": "Critical"},
        {"file": "app/cache.py", "line_start": 100, "severity": "Medium"},
    ],
    "traps": [
        {"file": "app/util.py", "line_start": 10},
    ],
    "neutral": [
        {"file": "app/extra.py", "line_start": 5},
    ],
}

WELL_FORMED = """
---
**BUG-1**
- **Hunter's claim:** token check is skipped
- **VERDICT: REAL BUG**
- **Confidence:** High
- **True severity:** Critical
---
**BUG-2**
- **Hunter's claim:** cache never expires
- **VERDICT: REAL BUG**
- **Confidence:** High
- **True severity:** Medium
---
**BUG-3**
- **Hunter's claim:** helper looks odd
- **VERDICT: NOT A BUG**
- **Confidence:** High
---

===REFEREE_REPORT_START===

**VERIFIED BUG REPORT**

### Critical

---
### BUG-1 — token check skipped
- **File:** `app/auth.py:42-44`
- **Category:** auth
---

### Medium

### BUG-2 – cache never expires
- **File:** app/cache.py:103
- **Category:** logic

### Low

### Low-confidence items (flagged for manual review)

### BUG-4 - maybe a race
- **File:** app/cache.py:100

### Dismissed

### BUG-3 - helper looks odd
- **File:** app/util.py:10

===REFEREE_REPORT_END===
"""

NO_FILE_LINE = """
===REFEREE_REPORT_START===
### Critical
### BUG-7 — something bad
- **Category:** auth
- **Fix:** see `app/auth.py:41`
===REFEREE_REPORT_END===
"""

# The reproduction: a dismissed block, then a confirmed block, no final report.
REPRO = """
---
**BUG-1**
- **VERDICT: NOT A BUG**
- **Confidence:** High
---
**BUG-2**
- **VERDICT: REAL BUG**
- **Confidence:** High
- **True severity:** Critical
---
"""


def bug(file, line, severity="Critical", bug_id="BUG-1", starts=None):
    if starts is None:
        starts = [line] if line else []
    return {"id": bug_id, "file": file, "line_start": line,
            "line_starts": starts, "severity": severity}


class ParseTests(unittest.TestCase):
    def test_well_formed_reply(self):
        bugs = evaluate.parse_referee_output(WELL_FORMED)
        self.assertEqual(bugs, [
            bug("app/auth.py", 42, "Critical", "BUG-1"),
            bug("app/cache.py", 103, "Medium", "BUG-2"),
        ])

    def test_dismissed_and_low_confidence_not_confirmed(self):
        ids = {b["id"] for b in evaluate.parse_referee_output(WELL_FORMED)}
        self.assertNotIn("BUG-3", ids)  # under ### Dismissed
        self.assertNotIn("BUG-4", ids)  # under ### Low-confidence items

    def test_report_entry_without_file_line(self):
        bugs = evaluate.parse_referee_output(NO_FILE_LINE)
        self.assertEqual(bugs, [bug("", 0, "Critical", "BUG-7")])
        scores = evaluate.compute_scores(MANIFEST, bugs)
        self.assertEqual(scores["details"]["planted_found"], 0)
        self.assertEqual(scores["recall"], 0.0)

    def test_reproduction_dismissed_then_confirmed(self):
        bugs = evaluate.parse_referee_output(REPRO)
        self.assertEqual([b["id"] for b in bugs], ["BUG-2"])
        self.assertEqual(bugs[0]["file"], "")
        self.assertEqual(bugs[0]["severity"], "Critical")
        scores = evaluate.compute_scores(MANIFEST, bugs)
        self.assertEqual(scores["details"]["planted_found"], 0)
        self.assertEqual(scores["recall"], 0.0)

    def test_file_line_without_backticks_around_line(self):
        text = "### Low\n### BUG-9 — x\n- **File:** `app/cache.py`:12-20\n"
        self.assertEqual(evaluate.parse_referee_output(text),
                         [bug("app/cache.py", 12, "Low", "BUG-9")])


class RealReplyFixtureTests(unittest.TestCase):
    """Parser checks against a trimmed excerpt of a real Referee reply."""

    fixture = HERE / "fixtures" / "referee_reply_excerpt.md"

    def setUp(self):
        self.text = self.fixture.read_text(encoding="utf-8")

    def test_final_report_entries(self):
        with self.assertLogs("evaluate", level="INFO") as logs:
            bugs = evaluate.parse_referee_output(self.text)
        self.assertIn("parse path: final report", "\n".join(logs.output))
        server = "benchmarks/easy/vuln_api_server.py"
        self.assertEqual(bugs, [
            bug(server, 97, "Critical", "BUG-1"),
            bug(server, 76, "Critical", "BUG-2", [76, 171]),
            bug(server, 51, "Medium", "BUG-7", [51, 156]),
            bug(server, 51, "Medium", "BUG-8", [51, 65, 164]),
            bug(server, 146, "Low", "BUG-29"),
            bug(server, 174, "Low", "BUG-30"),
        ])
        for b in bugs:
            self.assertTrue(b["file"].startswith("benchmarks/"), b)
            self.assertTrue(b["file"].endswith(".py"), b)

    def test_low_confidence_and_dismissed_not_returned(self):
        ids = {b["id"] for b in evaluate.parse_referee_output(self.text)}
        for listed in ("BUG-13", "BUG-17", "BUG-34", "BUG-35"):
            self.assertIn(listed, self.text)
            self.assertNotIn(listed, ids)

    def test_fallback_without_report_returns_real_bug_blocks_only(self):
        head = self.text[:self.text.index(evaluate.REPORT_START)]
        with self.assertLogs("evaluate", level="INFO") as logs:
            bugs = evaluate.parse_referee_output(head)
        self.assertIn("parse path: verdict blocks", "\n".join(logs.output))
        self.assertEqual([(b["id"], b["severity"]) for b in bugs],
                         [("BUG-1", "Critical"), ("BUG-2", "Critical"), ("BUG-3", "Critical")])


class LineRangeTests(unittest.TestCase):
    """Every comma-separated range on a File: line is kept."""

    def parse_one(self, file_line):
        text = f"### Medium\n### BUG-1 — x\n- **File:** {file_line}\n- **Category:** logic\n"
        bugs = evaluate.parse_referee_output(text)
        self.assertEqual(len(bugs), 1)
        return bugs[0]

    def test_two_ranges(self):
        b = self.parse_one("benchmarks/hard/auth_session.py:13, 120-127")
        self.assertEqual(b["file"], "benchmarks/hard/auth_session.py")
        self.assertEqual(b["line_start"], 13)
        self.assertEqual(b["line_starts"], [13, 120])

    def test_any_range_matches(self):
        b = self.parse_one("benchmarks/hard/auth_session.py:13, 120-127")
        self.assertTrue(evaluate.match_bug(
            b, {"file": "hard/auth_session.py", "line_start": 120, "line_end": 121}))
        self.assertFalse(evaluate.match_bug(
            b, {"file": "hard/auth_session.py", "line_start": 300, "line_end": 301}))

    def test_six_ranges(self):
        b = self.parse_one("`app/x.py:10-12, 15, 20, 30-31, 40 , 50-55`")
        self.assertEqual(b["line_starts"], [10, 15, 20, 30, 40, 50])
        self.assertEqual(b["line_start"], 10)

    def test_single_range(self):
        b = self.parse_one("app/x.py:45-52")
        self.assertEqual((b["line_start"], b["line_starts"]), (45, [45]))

    def test_no_numbers(self):
        b = self.parse_one("app/x.py")
        self.assertEqual((b["file"], b["line_start"], b["line_starts"]), ("app/x.py", 0, []))

    def test_stops_at_end_of_line(self):
        b = self.parse_one("app/x.py:7\n12, 14")
        self.assertEqual(b["line_starts"], [7])

    def test_table_rows_get_one_start(self):
        bugs = evaluate.parse_referee_output("| 3 | app/x.py | 42 | Low |")
        self.assertEqual(bugs, [bug("app/x.py", 42, "Low", "BUG-3", [42])])


class MatchTests(unittest.TestCase):
    planted = MANIFEST["bugs"][0]  # app/auth.py 40-45

    def test_empty_file_matches_nothing(self):
        self.assertFalse(evaluate.match_bug(bug("", 0), self.planted))
        self.assertFalse(evaluate.match_bug(bug("", 42), self.planted))

    def test_line_inside_tolerance_matches(self):
        tol = evaluate.LINE_TOLERANCE
        self.assertTrue(evaluate.match_bug(bug("app/auth.py", 40 - tol), self.planted))
        self.assertTrue(evaluate.match_bug(bug("app/auth.py", 45 + tol), self.planted))

    def test_line_outside_tolerance_does_not_match(self):
        tol = evaluate.LINE_TOLERANCE
        self.assertFalse(evaluate.match_bug(bug("app/auth.py", 40 - tol - 1), self.planted))
        self.assertFalse(evaluate.match_bug(bug("app/auth.py", 45 + tol + 1), self.planted))

    def test_wrong_file_does_not_match(self):
        self.assertFalse(evaluate.match_bug(bug("app/cache.py", 42), self.planted))


class ScoreTests(unittest.TestCase):
    def test_trap_hit_lowers_trap_resistance(self):
        scores = evaluate.compute_scores(MANIFEST, [bug("app/util.py", 11, "Low", "BUG-5")])
        self.assertLess(scores["trap_resistance"], 1.0)
        self.assertEqual(scores["details"]["traps_triggered"], 1)

    def test_well_formed_reply_scores_full_recall(self):
        scores = evaluate.compute_scores(MANIFEST, evaluate.parse_referee_output(WELL_FORMED))
        self.assertEqual(scores["recall"], 1.0)
        self.assertEqual(scores["trap_resistance"], 1.0)


class ResultsRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tsv = self.dir / "results.tsv"
        self.header = "\t".join([
            "commit", "composite", "f1", "recall", "precision",
            "severity_accuracy", "trap_resistance", "description",
        ]) + "\n"
        self.tsv.write_text(self.header)
        self.reply = self.dir / "reply.md"
        self.reply.write_text(WELL_FORMED)
        self.manifest = self.dir / "manifest.json"
        self.manifest.write_text(json.dumps(MANIFEST))

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *extra):
        argv = ["evaluate.py", str(self.reply), str(self.manifest), *extra]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(evaluate, "RESULTS_TSV", self.tsv), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            evaluate.main()
        return out.getvalue()

    def test_row_writer_appends_one_8_column_row(self):
        scores = evaluate.compute_scores(MANIFEST, [])
        evaluate.append_result_row(self.tsv, scores, "unit test")
        lines = self.tsv.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        cells = lines[1].split("\t")
        self.assertEqual(len(cells), 8)
        self.assertEqual(cells[-1], "unit test")

    def test_row_writer_uses_the_given_commit(self):
        scores = evaluate.compute_scores(MANIFEST, [])
        evaluate.append_result_row(self.tsv, scores, "unit test", "abc1234")
        self.assertEqual(self.tsv.read_text().splitlines()[1].split("\t")[0], "abc1234")

    def test_main_passes_commit_to_the_row(self):
        self.run_main("--description", "experiment x", "--commit", "abc1234")
        self.assertEqual(self.tsv.read_text().splitlines()[1].split("\t")[0], "abc1234")

    def test_main_without_description_writes_nothing(self):
        out = self.run_main()
        self.assertEqual(self.tsv.read_text(), self.header)
        self.assertIn("composite", json.loads(out))  # stdout is still pure JSON

    def test_main_with_empty_description_writes_nothing(self):
        self.run_main("--description", "  ")
        self.assertEqual(self.tsv.read_text(), self.header)

    def test_main_with_description_writes_one_row(self):
        self.run_main("--description", "experiment x")
        lines = self.tsv.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(lines[1].split("\t")), 8)


if __name__ == "__main__":
    unittest.main()
