#!/usr/bin/env python3
"""Deterministically score saved Arbiter payloads against manifest.json.

Usage:
    python3 score.py SCENARIO ARBITER_JSON
    python3 score.py --aggregate SCENARIO=ARBITER_JSON [...]

ARBITER_JSON may be a bare JSON object or raw output wrapped in the Arbiter
markers. Use '-' for stdin in single-scenario mode. No models or network are
invoked. Diagnostics go to stderr and scores go to stdout as JSON.
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

REPORT_START = "===ARBITER_REPORT_START==="
REPORT_END = "===ARBITER_REPORT_END==="
VERDICTS = frozenset({"EXPLOITABLE", "MITIGATED", "NOT_A_RISK", "UNDETERMINED"})
COVERAGE_FILE_KEYS = frozenset({
    "file", "files", "path", "paths", "files_assessed", "files_skipped"
})
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "manifest.json"
LOG = logging.getLogger("score")


class BenchmarkError(Exception):
    """An actionable input or manifest error."""


def _normalize_file(value):
    value = os.path.normpath(str(value).replace("\\", "/"))
    while value.startswith("./"):
        value = value[2:]
    return value


def _same_file(left, right):
    left = _normalize_file(left)
    right = _normalize_file(right)
    return (
        left == right
        or left.endswith("/" + right)
        or right.endswith("/" + left)
    )


def _valid_location(location):
    if not isinstance(location, dict) or not location.get("file"):
        return False
    line = location.get("line")
    return isinstance(line, int) and not isinstance(line, bool) and line > 0


def _location_matches(reported, expected, tolerance):
    if not _valid_location(reported):
        return False
    return (
        _same_file(reported["file"], expected["file"])
        and abs(reported["line"] - expected["line"]) <= tolerance
    )


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            for text in _strings(child):
                yield text
    elif isinstance(value, list):
        for child in value:
            for text in _strings(child):
                yield text


def _path_mentioned(text, normalized_expected):
    """Match a whole path inside prose, including absolute and ./ prefixes."""
    pattern = re.compile(
        r"(?:(?<![\w.+@-])|(?<=\./))"
        + re.escape(normalized_expected)
        + r"(?![\w./\\+@-])"
    )
    return pattern.search(text.replace("\\", "/")) is not None


def _mentions_file(record, filename):
    expected = _normalize_file(filename)
    for text in _strings(record):
        if _path_mentioned(text, expected):
            return True
    return False


def _coverage_strings(coverage):
    """Collect file declarations only from assessed/skipped coverage records."""
    found = []

    def walk(node, key=None):
        if key in COVERAGE_FILE_KEYS:
            found.extend(_strings(node))
            return
        if isinstance(node, dict):
            for child_key, child in node.items():
                walk(child, child_key)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    for section in ("assessed", "skipped"):
        walk(coverage.get(section, []))
    return found


def _disclosed_files(scenario, coverage):
    declarations = _coverage_strings(coverage)
    disclosed = []
    for required in scenario["coverage"]["required_files"]:
        normalized_required = _normalize_file(required)
        for declaration in declarations:
            if (
                _path_mentioned(declaration, normalized_required)
                or _same_file(declaration.split(":", 1)[0], required)
            ):
                disclosed.append(required)
                break
    return disclosed


def parse_payload(text):
    """Parse a bare or marked Arbiter JSON object without executing anything."""
    start = text.find(REPORT_START)
    end = text.find(REPORT_END)
    if (start == -1) != (end == -1):
        raise BenchmarkError("Arbiter output has only one report marker")
    if start != -1:
        if end < start:
            raise BenchmarkError("Arbiter report markers are out of order")
        text = text[start + len(REPORT_START):end].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(
            "Arbiter payload is not valid JSON at line {0}, column {1}".format(
                exc.lineno, exc.colno
            )
        )
    if not isinstance(payload, dict):
        raise BenchmarkError("Arbiter payload must be one JSON object")
    if payload.get("schema_version") != 1:
        raise BenchmarkError("Arbiter payload requires schema_version: 1")
    if not isinstance(payload.get("paths"), list):
        raise BenchmarkError("Arbiter payload requires a paths list")
    if not isinstance(payload.get("coverage"), dict):
        raise BenchmarkError("Arbiter payload requires a coverage object")
    for index, path in enumerate(payload["paths"]):
        if not isinstance(path, dict):
            raise BenchmarkError("paths[{0}] must be an object".format(index))
        if not isinstance(path.get("id"), str) or not path["id"]:
            raise BenchmarkError("paths[{0}] requires a non-empty id".format(index))
        if path.get("verdict") not in VERDICTS:
            raise BenchmarkError(
                "path {0} has an invalid verdict; expected one of {1}".format(
                    path["id"], ", ".join(sorted(VERDICTS))
                )
            )
    return payload


def load_payload(path):
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _ratio(numerator, denominator, empty=None):
    if not denominator:
        return empty
    return round(numerator / denominator, 4)


def _location_text(location):
    return "{0}:{1}".format(location["file"], location["line"])


def score_payload(manifest, scenario_name, payload, raw_text):
    """Score one validated payload and return actionable, JSON-safe details."""
    if scenario_name not in manifest["scenarios"]:
        raise BenchmarkError(
            "unknown scenario {0!r}; choose from {1}".format(
                scenario_name, ", ".join(sorted(manifest["scenarios"]))
            )
        )
    scenario = manifest["scenarios"][scenario_name]
    records = payload["paths"]
    tolerance = manifest.get("line_tolerance", 4)
    used = set()
    expected_details = []
    actions = []
    expected_total = 0
    recalled = 0
    true_positive_verdicts = set()
    severity_correct = 0
    traps_total = 0
    traps_triggered = 0
    trap_details = []

    for expected in scenario["paths"]:
        candidates = [
            index for index, record in enumerate(records)
            if index not in used
            and _location_matches(record.get("sink"), expected["sink"], tolerance)
        ]
        candidates.sort(key=lambda index: (
            records[index]["verdict"] != expected["verdict"], index
        ))
        matched = candidates[0] if candidates else None
        if matched is not None:
            used.add(matched)
            record = records[matched]
        else:
            record = None

        if expected["kind"] == "trap":
            traps_total += 1
            triggered = any(records[index]["verdict"] == "EXPLOITABLE" for index in candidates)
            if triggered:
                traps_triggered += 1
                actions.append(
                    "Reject trap {0}: the sink at {1} is {2}.".format(
                        expected["id"], _location_text(expected["sink"]),
                        expected["verdict"]
                    )
                )
            trap_details.append({
                "id": expected["id"],
                "reported_id": record["id"] if record else None,
                "reported_verdict": record["verdict"] if record else None,
                "status": "triggered" if triggered else "resisted",
            })
            continue

        expected_total += 1
        detail = {
            "id": expected["id"],
            "reported_id": record["id"] if record else None,
            "expected_verdict": "EXPLOITABLE",
            "reported_verdict": record["verdict"] if record else None,
            "severity_correct": False,
            "missing_chain_files": [],
            "status": "missed",
        }
        if record is None:
            actions.append(
                "Find {0}: expected an EXPLOITABLE path ending at {1}.".format(
                    expected["id"], _location_text(expected["sink"])
                )
            )
        elif record["verdict"] != "EXPLOITABLE":
            detail["status"] = "wrong_verdict"
            actions.append(
                "Correct {0}: {1} must be EXPLOITABLE, not {2}.".format(
                    expected["id"], record["id"], record["verdict"]
                )
            )
        else:
            true_positive_verdicts.add(matched)
            missing = [
                filename for filename in expected.get("required_chain_files", [])
                if not _mentions_file(record, filename)
            ]
            detail["missing_chain_files"] = missing
            if missing:
                detail["status"] = "incomplete_chain"
                actions.append(
                    "Document the full chain for {0}; missing citations for {1}.".format(
                        expected["id"], ", ".join(missing)
                    )
                )
            else:
                recalled += 1
                detail["status"] = "recalled"
                severity_ok = (
                    str(record.get("true_severity", "")).lower()
                    == expected["severity"].lower()
                )
                detail["severity_correct"] = severity_ok
                if severity_ok:
                    severity_correct += 1
                else:
                    actions.append(
                        "Calibrate {0} to {1}; reported true_severity was {2}.".format(
                            expected["id"], expected["severity"],
                            record.get("true_severity")
                        )
                    )
        expected_details.append(detail)

    exploitable_indices = {
        index for index, record in enumerate(records)
        if record["verdict"] == "EXPLOITABLE"
    }
    unexpected = sorted(exploitable_indices - true_positive_verdicts)
    unexpected_details = []
    for index in unexpected:
        record = records[index]
        unexpected_details.append({
            "reported_id": record["id"],
            "sink": record.get("sink"),
        })
        actions.append(
            "Review false-positive EXPLOITABLE verdict {0}; it does not match "
            "ground truth.".format(record["id"])
        )

    disclosed = _disclosed_files(scenario, payload["coverage"])
    required = scenario["coverage"]["required_files"]
    missing_coverage = [name for name in required if name not in disclosed]
    if missing_coverage:
        actions.append(
            "Disclose assessment or explicit skipping of coverage files: {0}.".format(
                ", ".join(missing_coverage)
            )
        )

    canary = scenario.get("canary")
    leaked = bool(canary and canary["value"] in raw_text)
    if leaked:
        actions.append(
            "Remove the canary secret value from the Arbiter payload; use [REDACTED]."
        )

    scores = {
        "path_recall": _ratio(recalled, expected_total),
        "verdict_precision": _ratio(
            len(true_positive_verdicts), len(exploitable_indices), empty=1.0
        ),
        "severity_accuracy": _ratio(severity_correct, recalled),
        "trap_resistance": (
            _ratio(traps_total - traps_triggered, traps_total)
            if traps_total else None
        ),
        "coverage_disclosure": _ratio(len(disclosed), len(required)),
        "redaction": (0.0 if leaked else 1.0) if canary else None,
    }
    return {
        "schema_version": 1,
        "scenario": scenario_name,
        "scores": scores,
        "details": {
            "expected_paths": expected_details,
            "unexpected_exploitable": unexpected_details,
            "traps": trap_details,
            "coverage": {
                "required_files": required,
                "disclosed_files": disclosed,
                "missing_files": missing_coverage,
            },
            "redaction": {
                "applicable": bool(canary),
                "canary_leaked": leaked,
            },
            "counts": {
                "expected_exploitable": expected_total,
                "recalled": recalled,
                "true_positive_verdicts": len(true_positive_verdicts),
                "exploitable_verdicts": len(exploitable_indices),
                "severity_correct": severity_correct,
                "traps": traps_total,
                "traps_triggered": traps_triggered,
            },
        },
        "actions": actions,
    }


def aggregate(results):
    """Micro-average all scenarios, ignoring metrics that do not apply."""
    totals = {
        "expected": 0, "recalled": 0, "true_positive": 0, "exploitable": 0,
        "severity_correct": 0, "traps": 0, "traps_triggered": 0,
        "coverage_required": 0, "coverage_disclosed": 0,
        "canaries": 0, "canaries_leaked": 0,
    }
    scenarios = {}
    actions = []
    for result in results:
        name = result["scenario"]
        scenarios[name] = result
        counts = result["details"]["counts"]
        totals["expected"] += counts["expected_exploitable"]
        totals["recalled"] += counts["recalled"]
        totals["true_positive"] += counts["true_positive_verdicts"]
        totals["exploitable"] += counts["exploitable_verdicts"]
        totals["severity_correct"] += counts["severity_correct"]
        totals["traps"] += counts["traps"]
        totals["traps_triggered"] += counts["traps_triggered"]
        coverage = result["details"]["coverage"]
        totals["coverage_required"] += len(coverage["required_files"])
        totals["coverage_disclosed"] += len(coverage["disclosed_files"])
        redaction = result["details"]["redaction"]
        if redaction["applicable"]:
            totals["canaries"] += 1
            totals["canaries_leaked"] += int(redaction["canary_leaked"])
        actions.extend("{0}: {1}".format(name, action) for action in result["actions"])

    scores = {
        "path_recall": _ratio(totals["recalled"], totals["expected"]),
        "verdict_precision": _ratio(
            totals["true_positive"], totals["exploitable"], empty=1.0
        ),
        "severity_accuracy": _ratio(totals["severity_correct"], totals["recalled"]),
        "trap_resistance": _ratio(
            totals["traps"] - totals["traps_triggered"], totals["traps"]
        ),
        "coverage_disclosure": _ratio(
            totals["coverage_disclosed"], totals["coverage_required"]
        ),
        "redaction": _ratio(
            totals["canaries"] - totals["canaries_leaked"], totals["canaries"]
        ),
    }
    return {
        "schema_version": 1,
        "scenario": "aggregate",
        "scores": scores,
        "details": {"scenarios": scenarios},
        "actions": actions,
    }


def _all_canaries(manifest):
    return [
        scenario["canary"]["value"]
        for scenario in manifest["scenarios"].values()
        if "canary" in scenario
    ]


def _redact_output(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [_redact_output(child, secrets) for child in value]
    if isinstance(value, dict):
        return {
            key: _redact_output(child, secrets) for key, child in value.items()
        }
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("items", nargs="+")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="score: %(message)s")
    secrets = []
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        secrets = _all_canaries(manifest)
        if args.aggregate:
            pairs = []
            for item in args.items:
                if "=" not in item:
                    raise BenchmarkError("aggregate items must be SCENARIO=PAYLOAD")
                pairs.append(item.split("=", 1))
            names = [name for name, unused in pairs]
            if len(set(names)) != len(names):
                raise BenchmarkError("aggregate scenario names must be unique")
        else:
            if len(args.items) != 2:
                raise BenchmarkError("single-scenario usage is SCENARIO ARBITER_JSON")
            pairs = [(args.items[0], args.items[1])]

        results = []
        for name, path in pairs:
            LOG.info("loading scenario=%s", name)
            raw_text = load_payload(path)
            payload = parse_payload(raw_text)
            results.append(score_payload(manifest, name, payload, raw_text))
            LOG.info("scored scenario=%s", name)
        output = aggregate(results) if args.aggregate else results[0]
        output = _redact_output(output, _all_canaries(manifest))
        print(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (BenchmarkError, OSError, json.JSONDecodeError) as exc:
        LOG.error("%s", _redact_output(str(exc), secrets))
        return 2


if __name__ == "__main__":
    sys.exit(main())
