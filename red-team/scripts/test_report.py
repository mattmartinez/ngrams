#!/usr/bin/env python3
"""Unit tests for report.py; standard library only."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPORT_PATH = Path(__file__).resolve().with_name("report.py")
REPORT_SPEC = importlib.util.spec_from_file_location("red_team_report", str(REPORT_PATH))
if REPORT_SPEC is None or REPORT_SPEC.loader is None:
    raise ImportError("cannot load report.py")
report = importlib.util.module_from_spec(REPORT_SPEC)
REPORT_SPEC.loader.exec_module(report)


ZONE = "API"


def make_path(identifier="ATTACK-API-1", zone=ZONE, severity="High"):
    return {
        "id": identifier,
        "title": "Cross-tenant object read",
        "severity": severity,
        "persona": "authenticated low-privilege tenant",
        "asset": "another tenant's private object",
        "entry_point": {"file": "api/objects.py", "line": 18},
        "sink": {"file": "data/objects.py", "line": 44},
        "category": "authz/idor",
        "precondition": "The attacker has an ordinary account and controls the object ID.",
        "steps": [
            "Request an object owned by another tenant at api/objects.py:18.",
            "The query reaches the object store without an owner predicate at data/objects.py:44.",
        ],
        "evidence": [
            "api/objects.py:18 accepts object_id from the request.",
            "data/objects.py:44 reads by object_id; credential=<redacted>.",
        ],
        "impact": "Read and disclose another tenant's object.",
        "mitigations": ["Authentication is required at api/auth.py:9."],
        "zone": zone,
        "capabilities_required": ["ordinary tenant session"],
        "capabilities_gained": ["cross-tenant object read"],
        "chain_links": [],
    }


def make_attacker(zone=ZONE, paths=None, skipped=None, notes=None):
    if paths is None:
        paths = [make_path(zone=zone, identifier=f"ATTACK-{zone}-1")]
    return {
        "schema_version": 1,
        "zone": zone,
        "metadata": {
            "shape": "network service",
            "stack": "Python / FastAPI",
            "target": "/assessment/root",
            "files_assessed": 3,
            "files_assessed_sample": ["api/objects.py", "data/objects.py"],
            "entry_points": ["GET /objects/{id}"],
            "crown_jewels": ["tenant objects"],
            "personas": ["authenticated low-privilege tenant"],
            "trust_boundaries": ["tenant A -> tenant B"],
            "coverage": {
                "assigned": [zone],
                "assessed": [zone],
                "skipped": skipped or [],
                "notes": notes or [f"{zone} coverage recorded"],
            },
        },
        "paths": paths,
    }


def marked(stage, payload):
    start, end = report.MARKERS[stage]
    return f"agent narrative\n{start}\n{json.dumps(payload)}\n{end}\n"


def make_blue(attacks, decisions=None):
    decisions = decisions or {}
    evaluations = []
    for path in attacks["paths"]:
        decision = decisions.get(path["id"], "DEFENDED")
        if decision == "EXPLOITABLE":
            assessment = "A demonstrated current bypass reaches the asset despite the control."
        elif decision == "UNDETERMINED":
            assessment = "Reachability cannot be settled from the available code and configuration."
        else:
            assessment = "A current control stops the path; future fragility is not a current bypass."
        evaluations.append(
            {
                "id": path["id"],
                "decision": decision,
                "verification_method": "Read entry, sink, middleware, and the full call path.",
                "precondition_check": "Checked that the named persona can or cannot obtain the precondition.",
                "control_analysis": "Checked tenant scoping at data/objects.py:40-48.",
                "assessment": assessment,
                "evidence": ["data/objects.py:44 contains the object query; values=<redacted>."],
                "residual_concern": "Tenant scoping depends on one shared helper.",
                "confidence": 85,
            }
        )
    return {"schema_version": 1, "evaluations": evaluations}


def make_arbiter(attacks, blue, verdicts=None, fragile_ids=None):
    verdicts = verdicts or {}
    fragile_ids = set(fragile_ids or [])
    paths = []
    for path in attacks["paths"]:
        record = copy.deepcopy(path)
        verdict = verdicts.get(path["id"], "EXPLOITABLE")
        record.update(
            {
                "verdict": verdict,
                "analysis": "Independently traced the complete path and checked the invariant at the sink.",
                "confidence": 90,
                "true_severity": path["severity"],
                "defense": (
                    "Tenant-scoped query at data/objects.py:42"
                    + ("; fragile if this shared helper changes." if path["id"] in fragile_ids else ".")
                    if verdict == "MITIGATED"
                    else ""
                ),
                "remediation": "Require an owner predicate and add a cross-tenant regression test.",
                "remediation_complexity": "Moderate",
                "remediation_risks": "Administrative support queries may need a separate audited path.",
                "validation": "Attempt two same-ID requests as different tenants and assert isolation.",
            }
        )
        paths.append(record)
    coverage = attacks["metadata"]["coverage"]
    required = []
    for field in ("assigned", "assessed", "skipped"):
        for zone in coverage[field]:
            if zone not in required:
                required.append(zone)
    skipped = coverage["skipped"]
    stats = {
        "total": len(paths),
        "exploitable": sum(path["verdict"] == "EXPLOITABLE" for path in paths),
        "mitigated": sum(path["verdict"] == "MITIGATED" for path in paths),
        "not_a_risk": sum(path["verdict"] == "NOT_A_RISK" for path in paths),
        "undetermined": sum(path["verdict"] == "UNDETERMINED" for path in paths),
        "severity_counts": {
            severity: sum(path["true_severity"] == severity for path in paths)
            for severity in report.SEVERITIES
        },
    }
    return {
        "schema_version": 1,
        "coverage": {
            "assessed": [
                {"zone": zone, "files": list(attacks["metadata"]["files_assessed_sample"])}
                for zone in required
                if zone not in skipped
            ],
            "skipped": [{"zone": zone, "files": []} for zone in skipped],
            "limitations": ["Static review only; deployed configuration was not exercised."],
        },
        "paths": paths,
        "stats": stats,
    }


class ReportTests(unittest.TestCase):
    def test_malformed_and_truncated_marked_payloads(self):
        start, end = report.MARKERS["attacker"]
        with self.assertRaisesRegex(report.ReportError, "exactly one"):
            report.extract_marked(start + "\n{}", "attacker", "truncated.raw")
        with self.assertRaisesRegex(report.ReportError, "invalid or truncated JSON"):
            report.extract_marked(start + '\n{"schema_version": 1,\n' + end, "attacker")
        with self.assertRaisesRegex(report.ReportError, "exactly one"):
            report.extract_marked(marked("attacker", make_attacker()) + end, "attacker")

    def test_validation_reports_precise_schema_errors(self):
        payload = make_attacker()
        payload["paths"][0]["evidence"] = []
        with self.assertRaisesRegex(report.ReportError, r"attacker.paths\[0\].evidence"):
            report.validate_attacker(payload)
        payload = make_attacker()
        payload["paths"][0]["unexpected"] = "field"
        with self.assertRaisesRegex(report.ReportError, "unknown field"):
            report.validate_attacker(payload)
        payload = make_attacker()
        payload["paths"][0]["id"] = "ATTACK-01"
        with self.assertRaisesRegex(report.ReportError, "arbitrary zone"):
            report.validate_attacker(payload)

    def test_merge_mixes_zero_and_nonzero_attackers_and_unions_coverage(self):
        empty = make_attacker(zone="EMPTY", paths=[], notes=["No viable path in this zone"])
        nonempty = make_attacker(zone="PUBLIC_API")
        merged = report.merge_attackers([empty, nonempty])
        self.assertEqual([path["id"] for path in merged["paths"]], ["ATTACK-PUBLIC_API-1"])
        self.assertEqual(merged["metadata"]["coverage"]["assessed"], ["EMPTY", "PUBLIC_API"])
        self.assertIn("No viable path in this zone", merged["metadata"]["coverage"]["notes"])
        report.validate_attacker(merged)

    def test_merge_supports_four_plus_arbitrary_length_zones(self):
        zones = [
            "PUBLIC_API",
            "AUTH",
            "DATA",
            "INFRA",
            "CI_CD",
            "WORKFLOW_WITH_A_VERY_LONG_ZONE_NAME",
        ]
        payloads = []
        for index, zone in enumerate(zones, 1):
            path = make_path(identifier=f"ATTACK-{zone}-{index * 100000}", zone=zone)
            payloads.append(make_attacker(zone=zone, paths=[path]))
        merged = report.merge_attackers(payloads)
        self.assertEqual(len(merged["paths"]), 6)
        self.assertEqual(
            {path["zone"] for path in merged["paths"]},
            set(zones),
        )
        self.assertIn("ATTACK-WORKFLOW_WITH_A_VERY_LONG_ZONE_NAME-600000", {p["id"] for p in merged["paths"]})

    def test_merge_rejects_duplicate_ids(self):
        payload = make_attacker()
        with self.assertRaisesRegex(report.ReportError, "duplicate ID ATTACK-API-1"):
            report.merge_attackers([payload, copy.deepcopy(payload)])

    def test_merge_validates_cross_zone_chain_references(self):
        first = make_path("ATTACK-API-1", zone="API")
        second = make_path("ATTACK-AUTH-1", zone="AUTH")
        first["chain_links"] = [
            {
                "file": "api/auth.py",
                "line": 12,
                "role": "pivot into the authenticated workflow",
                "id": "ATTACK-AUTH-9",
            }
        ]
        with self.assertRaisesRegex(report.ReportError, "dangling cross-zone reference ATTACK-AUTH-9"):
            report.merge_attackers(
                [
                    make_attacker(zone="API", paths=[first]),
                    make_attacker(zone="AUTH", paths=[second]),
                ]
            )
        first["chain_links"][0]["id"] = "ATTACK-AUTH-1"
        merged = report.merge_attackers(
            [
                make_attacker(zone="API", paths=[first]),
                make_attacker(zone="AUTH", paths=[second]),
            ]
        )
        self.assertEqual({path["zone"] for path in merged["paths"]}, {"API", "AUTH"})

        first["chain_links"][0]["id"] = "ATTACK-API-1"
        with self.assertRaisesRegex(report.ReportError, "self chain reference"):
            report.validate_attacker(make_attacker(zone="API", paths=[first]))

    def test_benchmark_style_code_chain_links_render(self):
        path = make_path()
        path["chain_links"] = [
            {"file": "api/objects.py", "line": 18, "role": "entry"},
            {"file": "data/objects.py", "line": 44, "role": "disclosure sink"},
        ]
        attacks = make_attacker(paths=[path])
        blue = make_blue(attacks)
        arbiter = make_arbiter(attacks, blue)
        report.validate_arbiter(arbiter, attacks, blue["evaluations"])
        full = report.render(arbiter, "full")
        self.assertIn("api/objects.py:18 · entry", full)
        self.assertIn("data/objects.py:44 · disclosure sink", full)

    def test_blue_rejects_missing_extra_and_duplicate_ids(self):
        attacks = make_attacker()
        blue = make_blue(attacks)
        blue["evaluations"] = []
        with self.assertRaisesRegex(report.ReportError, "missing ATTACK-API-1"):
            report.validate_blue(blue, attacks)

        blue = make_blue(attacks)
        extra = copy.deepcopy(blue["evaluations"][0])
        extra["id"] = "ATTACK-OTHER-99"
        blue["evaluations"].append(extra)
        with self.assertRaisesRegex(report.ReportError, "unexpected ATTACK-OTHER-99"):
            report.validate_blue(blue, attacks)

        blue = make_blue(attacks)
        blue["evaluations"].append(copy.deepcopy(blue["evaluations"][0]))
        with self.assertRaisesRegex(report.ReportError, "duplicate ID"):
            report.validate_blue(blue, attacks)

    def test_blue_bounded_batch_has_exact_selected_id_set(self):
        paths = [make_path("ATTACK-API-1"), make_path("ATTACK-API-2")]
        attacks = make_attacker(paths=paths)
        first_only = make_blue(attacks)
        first_only["evaluations"] = first_only["evaluations"][:1]
        report.validate_blue(first_only, attacks, ["ATTACK-API-1"])
        with self.assertRaisesRegex(report.ReportError, "missing ATTACK-API-2"):
            report.validate_blue(first_only, attacks)
        with self.assertRaisesRegex(report.ReportError, "absent from Attacker"):
            report.validate_blue(first_only, attacks, ["ATTACK-NOPE-1"])

    def test_arbiter_rejects_missing_extra_and_duplicate_ids(self):
        two = make_attacker(paths=[make_path("ATTACK-API-1"), make_path("ATTACK-API-2")])
        two_blue = make_blue(two)
        one = make_attacker()
        one_arbiter = make_arbiter(one, make_blue(one))
        with self.assertRaisesRegex(report.ReportError, "missing ATTACK-API-2"):
            report.validate_arbiter(one_arbiter, two, two_blue["evaluations"])

        extra_record = copy.deepcopy(one_arbiter["paths"][0])
        extra_record["id"] = "ATTACK-EXTRA-7"
        one_arbiter["paths"].append(extra_record)
        one_arbiter["stats"] = make_arbiter(two, two_blue)["stats"]
        with self.assertRaisesRegex(report.ReportError, "unexpected ATTACK-EXTRA-7"):
            report.validate_arbiter(one_arbiter, one, make_blue(one)["evaluations"])

        duplicate = make_arbiter(one, make_blue(one))
        duplicate["paths"].append(copy.deepcopy(duplicate["paths"][0]))
        with self.assertRaisesRegex(report.ReportError, "duplicate ID"):
            report.validate_arbiter(duplicate, one, make_blue(one)["evaluations"])

    def test_arbiter_rejects_bad_verdict_and_severity_counts(self):
        attacks = make_attacker()
        blue = make_blue(attacks)
        payload = make_arbiter(attacks, blue)
        payload["stats"]["exploitable"] = 0
        with self.assertRaisesRegex(report.ReportError, r"arbiter.stats.exploitable: expected 1"):
            report.validate_arbiter(payload, attacks, blue["evaluations"])

        payload = make_arbiter(attacks, blue)
        payload["stats"]["severity_counts"]["High"] = 0
        with self.assertRaisesRegex(report.ReportError, r"arbiter.stats.severity_counts.High: expected 1"):
            report.validate_arbiter(payload, attacks, blue["evaluations"])

    def test_arbiter_requires_retained_records_and_coverage(self):
        attacks = make_attacker()
        blue = make_blue(attacks)
        payload = make_arbiter(attacks, blue)
        payload["paths"][0]["title"] = "Rewritten title"
        with self.assertRaisesRegex(report.ReportError, "must retain the Attacker value"):
            report.validate_arbiter(payload, attacks, blue["evaluations"])

        payload = make_arbiter(attacks, blue)
        payload["coverage"]["assessed"] = []
        with self.assertRaisesRegex(report.ReportError, "zone\(s\) disappeared"):
            report.validate_arbiter(payload, attacks, blue["evaluations"])

    def test_current_bypass_and_undetermined_records(self):
        attacks = make_attacker(paths=[make_path("ATTACK-API-1"), make_path("ATTACK-API-2")])
        blue = make_blue(
            attacks,
            {"ATTACK-API-1": "EXPLOITABLE", "ATTACK-API-2": "UNDETERMINED"},
        )
        report.validate_blue(blue, attacks)
        self.assertIn("current bypass", blue["evaluations"][0]["assessment"])
        arbiter = make_arbiter(
            attacks,
            blue,
            {"ATTACK-API-1": "EXPLOITABLE", "ATTACK-API-2": "UNDETERMINED"},
        )
        report.validate_arbiter(arbiter, attacks, blue["evaluations"])
        summary = report.render(arbiter, "summary")
        self.assertIn("Undetermined: 1", summary)
        self.assertIn("Undetermined paths requiring more evidence: 1", summary)
        self.assertIn("UNDETERMINED", report.render(arbiter, "path", "ATTACK-API-2"))
        self.assertIn("ATTACK-API-2", report.render(arbiter, "stopped"))

    def test_renderability_at_every_severity_and_verdict(self):
        verdicts = ("EXPLOITABLE", "MITIGATED", "NOT_A_RISK", "UNDETERMINED")
        paths = [
            make_path(f"ATTACK-ALL-{index}", zone="ALL", severity=severity)
            for index, severity in enumerate(report.SEVERITIES, 1)
        ]
        paths[-1]["entry_point"]["line"] = None
        attacks = make_attacker(zone="ALL", paths=paths)
        blue = make_blue(attacks)
        verdict_by_id = {path["id"]: verdicts[index] for index, path in enumerate(paths)}
        arbiter = make_arbiter(attacks, blue, verdict_by_id, fragile_ids=["ATTACK-ALL-2"])
        report.validate_arbiter(arbiter, attacks, blue["evaluations"])
        summary = report.render(arbiter, "summary")
        full = report.render(arbiter, "full")
        stopped = report.render(arbiter, "stopped")
        for index, severity in enumerate(report.SEVERITIES, 1):
            with self.subTest(severity=severity):
                self.assertIn(f"### {severity}", full)
                card = report.render(arbiter, "path", f"ATTACK-ALL-{index}")
                self.assertIn(f"True severity: {severity}", card)
                self.assertIn(f"ATTACK-ALL-{index}", full)
        self.assertIn("ATTACK-ALL-2", stopped)
        self.assertIn("ATTACK-ALL-3", stopped)
        self.assertIn("ATTACK-ALL-4", stopped)
        self.assertIn("Fragile controls:\n- ATTACK-ALL-2", summary)
        self.assertIn("api/objects.py:18 ·", summary)
        self.assertEqual(report.render(arbiter, "path", "1"), report.render(arbiter, "path", "ATTACK-ALL-1"))
        with self.assertRaisesRegex(report.ReportError, "out of range"):
            report.render(arbiter, "path", "2")

    def test_zero_path_clean_payload_renders_with_coverage(self):
        attacks = make_attacker(zone="QUIET", paths=[], skipped=["DEFERRED_INFRA"])
        blue = make_blue(attacks)
        arbiter = make_arbiter(attacks, blue)
        report.validate_arbiter(arbiter, attacks, [])
        summary = report.render(arbiter, "summary")
        self.assertIn("Paths: 0", summary)
        self.assertIn("No paths were confirmed exploitable.", summary)
        self.assertIn("QUIET", summary)
        self.assertIn("DEFERRED_INFRA", summary)
        self.assertIn("Static review only", summary)
        self.assertIn("FULL PATH RECORDS", report.render(arbiter, "full"))
        self.assertIn("None.", report.render(arbiter, "stopped"))

    def test_clean_summary_lists_every_attempt_and_stop_reason(self):
        attacks = make_attacker(paths=[make_path("ATTACK-API-1"), make_path("ATTACK-API-2")])
        blue = make_blue(attacks)
        arbiter = make_arbiter(
            attacks,
            blue,
            {"ATTACK-API-1": "MITIGATED", "ATTACK-API-2": "NOT_A_RISK"},
        )
        summary = report.render(arbiter, "summary")
        self.assertIn("No paths were confirmed exploitable.", summary)
        self.assertIn("Attempted paths and their current outcome:", summary)
        self.assertIn("ATTACK-API-1", summary)
        self.assertIn("Tenant-scoped query at data/objects.py:42", summary)
        self.assertIn("ATTACK-API-2", summary)
        self.assertIn("Independently traced the complete path", summary)

    def test_arbiter_batches_validate_and_combine(self):
        paths = [make_path("ATTACK-API-1"), make_path("ATTACK-API-2", severity="Critical")]
        attacks = make_attacker(paths=paths)
        blue = make_blue(attacks)
        batches = []
        for index, identifier in enumerate(("ATTACK-API-1", "ATTACK-API-2")):
            selected_attacks = copy.deepcopy(attacks)
            selected_attacks["paths"] = [paths[index]]
            selected_blue = {"schema_version": 1, "evaluations": [blue["evaluations"][index]]}
            batch = make_arbiter(selected_attacks, selected_blue)
            report.validate_arbiter(batch, attacks, selected_blue["evaluations"], [identifier])
            batches.append(batch)

        batches[0]["coverage"]["assessed"] = [{"zone": "API", "files": ["api/objects.py"]}]
        batches[1]["coverage"]["assessed"] = [
            {"zone": "API", "files": ["data/objects.py"]},
            {"zone": "SYNTHESIS", "files": ["app/workflow.py"]},
        ]
        batches[1]["coverage"]["skipped"] = [
            {"zone": "API", "files": ["stale/skip.py"]},
            {"zone": "DEFERRED", "files": []},
        ]
        batches[1]["coverage"]["limitations"] = ["Second batch limitation"]
        with self.assertRaisesRegex(report.ReportError, "missing ATTACK-API-2"):
            report.merge_arbiters([batches[0]], attacks, blue["evaluations"])

        combined = report.merge_arbiters(batches, attacks, blue["evaluations"])
        self.assertEqual(combined["stats"]["total"], 2)
        self.assertEqual(combined["stats"]["exploitable"], 2)
        self.assertEqual(combined["stats"]["severity_counts"]["Critical"], 1)
        assessed = {entry["zone"]: entry["files"] for entry in combined["coverage"]["assessed"]}
        self.assertEqual(assessed["API"], ["api/objects.py", "data/objects.py"])
        self.assertEqual(assessed["SYNTHESIS"], ["app/workflow.py"])
        skipped = {entry["zone"]: entry["files"] for entry in combined["coverage"]["skipped"]}
        self.assertEqual(list(skipped), ["API", "DEFERRED"])
        self.assertEqual(skipped["API"], ["stale/skip.py"])
        self.assertEqual(
            combined["coverage"]["limitations"],
            ["Static review only; deployed configuration was not exercised.", "Second batch limitation"],
        )
        report.validate_arbiter(combined, attacks, blue["evaluations"])

        with self.assertRaisesRegex(report.ReportError, "duplicate ID ATTACK-API-1"):
            report.merge_arbiters([batches[0], *batches], attacks, blue["evaluations"])

    def test_actual_cli_validate_merge_and_render(self):
        attacks = make_attacker()
        blue = make_blue(attacks, {"ATTACK-API-1": "EXPLOITABLE"})
        arbiter = make_arbiter(attacks, blue)
        script = str(Path(__file__).with_name("report.py"))
        with tempfile.TemporaryDirectory() as directory:
            raw_attacker = os.path.join(directory, "attacker.raw")
            raw_blue = os.path.join(directory, "blue.raw")
            raw_arbiter = os.path.join(directory, "arbiter.raw")
            Path(raw_attacker).write_text(marked("attacker", attacks), encoding="utf-8")
            Path(raw_blue).write_text(marked("blue", blue), encoding="utf-8")
            Path(raw_arbiter).write_text(marked("arbiter", arbiter), encoding="utf-8")
            attacks_json = os.path.join(directory, "attacks.json")
            merged_json = os.path.join(directory, "merged.json")
            blue_json = os.path.join(directory, "blue.json")
            arbiter_json = os.path.join(directory, "arbiter.json")
            arbiter_batch_json = os.path.join(directory, "arbiter-batch.json")
            recombined_json = os.path.join(directory, "arbiter-recombined.json")

            def run(*args):
                completed = subprocess.run(
                    [sys.executable, script, *args],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("[report]", completed.stderr)
                return completed.stdout

            Path(attacks_json).write_text(run("validate", "attacker", raw_attacker), encoding="utf-8")
            Path(merged_json).write_text(run("merge", attacks_json), encoding="utf-8")
            Path(blue_json).write_text(
                run("validate", "blue", raw_blue, "--attacks", merged_json), encoding="utf-8"
            )
            Path(arbiter_json).write_text(
                run(
                    "validate",
                    "arbiter",
                    raw_arbiter,
                    "--attacks",
                    merged_json,
                    "--blue",
                    blue_json,
                ),
                encoding="utf-8",
            )
            self.assertIn("ATTACK-API-1", run("render", "summary", arbiter_json))
            self.assertIn("Attack steps:", run("render", "path", arbiter_json, "1"))

            Path(arbiter_batch_json).write_text(
                run(
                    "validate", "arbiter", raw_arbiter,
                    "--attacks", merged_json,
                    "--blue", blue_json,
                    "--id", "ATTACK-API-1",
                ),
                encoding="utf-8",
            )
            Path(recombined_json).write_text(
                run(
                    "merge-arbiters", arbiter_batch_json,
                    "--attacks", merged_json,
                    "--blue", blue_json,
                ),
                encoding="utf-8",
            )
            self.assertIn("ATTACK-API-1", run("render", "summary", recombined_json))

            failed = subprocess.run(
                [sys.executable, script, "validate", "blue", raw_blue],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("requires --attacks", failed.stderr)


if __name__ == "__main__":
    unittest.main()
