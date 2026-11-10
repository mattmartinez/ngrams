#!/usr/bin/env python3
"""Validate, merge, and render structured red-team assessment reports."""

import argparse
import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PREFIX = "[report]"
SCHEMA_VERSION = 1
SEVERITIES = ("Critical", "High", "Medium", "Low")
VERDICTS = ("EXPLOITABLE", "MITIGATED", "NOT_A_RISK", "UNDETERMINED")
DECISIONS = ("DEFENDED", "EXPLOITABLE", "UNDETERMINED")
ID_RE = re.compile(r"^ATTACK-(?:[A-Za-z0-9][A-Za-z0-9_-]*-)?[1-9][0-9]*$")

MARKERS = {
    "attacker": ("===ATTACKER_FINDINGS_START===", "===ATTACKER_FINDINGS_END==="),
    "blue": ("===BLUE_TEAM_REPORT_START===", "===BLUE_TEAM_REPORT_END==="),
    "arbiter": ("===ARBITER_REPORT_START===", "===ARBITER_REPORT_END==="),
}

ATTACKER_PATH_FIELDS = (
    "id",
    "title",
    "severity",
    "persona",
    "asset",
    "entry_point",
    "sink",
    "category",
    "precondition",
    "steps",
    "evidence",
    "impact",
    "mitigations",
    "zone",
    "capabilities_required",
    "capabilities_gained",
    "chain_links",
)
ARBITER_EXTRA_FIELDS = (
    "verdict",
    "analysis",
    "confidence",
    "true_severity",
    "defense",
    "remediation",
    "remediation_complexity",
    "remediation_risks",
    "validation",
)


class ReportError(Exception):
    """A user-facing validation or command error."""


def log(message: str) -> None:
    print(f"{PREFIX} {message}", file=sys.stderr, flush=True)


def _fail(where: str, message: str) -> None:
    raise ReportError(f"{where}: {message}")


def _object(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        _fail(where, "expected a JSON object")
    return value


def _array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        _fail(where, "expected a JSON array")
    return value


def _keys(value: Dict[str, Any], fields: Iterable[str], where: str) -> None:
    expected = set(fields)
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        _fail(where, "missing field(s): " + ", ".join(missing))
    if extra:
        _fail(where, "unknown field(s): " + ", ".join(extra) + "; use the documented schema")


def _string(value: Any, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(where, "expected a string")
    if not allow_empty and not value.strip():
        _fail(where, "must not be empty")
    return value


def _integer(value: Any, where: str, minimum: int, maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(where, "expected an integer")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        _fail(where, f"expected {bound}, got {value}")
    return value


def _string_list(value: Any, where: str, allow_empty_items: bool = False) -> List[str]:
    items = _array(value, where)
    for index, item in enumerate(items):
        _string(item, f"{where}[{index}]", allow_empty=allow_empty_items)
    return items


def _attack_id(value: Any, where: str) -> str:
    text = _string(value, where)
    if not ID_RE.fullmatch(text):
        _fail(where, "expected ATTACK-1 or an arbitrary zone form such as ATTACK-PUBLIC_API-25")
    return text


def _location(value: Any, where: str) -> None:
    item = _object(value, where)
    _keys(item, ("file", "line"), where)
    _string(item["file"], where + ".file")
    line = item["line"]
    if line is not None:
        _integer(line, where + ".line", 1)


def _coverage(value: Any, where: str, final: bool = False) -> None:
    item = _object(value, where)
    fields = ("assessed", "skipped", "limitations") if final else ("assigned", "assessed", "skipped", "notes")
    _keys(item, fields, where)
    if final:
        for field in ("assessed", "skipped"):
            entries = _array(item[field], where + "." + field)
            seen = set()
            for index, entry in enumerate(entries):
                entry_where = f"{where}.{field}[{index}]"
                record = _object(entry, entry_where)
                _keys(record, ("zone", "files"), entry_where)
                zone = _string(record["zone"], entry_where + ".zone")
                if zone in seen:
                    _fail(entry_where + ".zone", f"duplicate coverage zone {zone}")
                seen.add(zone)
                _string_list(record["files"], entry_where + ".files")
        _string_list(item["limitations"], where + ".limitations")
    else:
        for field in fields:
            _string_list(item[field], where + "." + field)


def _metadata(value: Any, where: str) -> None:
    item = _object(value, where)
    fields = (
        "shape",
        "stack",
        "target",
        "files_assessed",
        "files_assessed_sample",
        "entry_points",
        "crown_jewels",
        "personas",
        "trust_boundaries",
        "coverage",
    )
    _keys(item, fields, where)
    for field in ("shape", "stack", "target"):
        _string(item[field], where + "." + field)
    _integer(item["files_assessed"], where + ".files_assessed", 0)
    for field in (
        "files_assessed_sample",
        "entry_points",
        "crown_jewels",
        "personas",
        "trust_boundaries",
    ):
        _string_list(item[field], where + "." + field)
    _coverage(item["coverage"], where + ".coverage")


def _attacker_path(value: Any, where: str, arbiter: bool = False) -> None:
    item = _object(value, where)
    fields = ATTACKER_PATH_FIELDS + (ARBITER_EXTRA_FIELDS if arbiter else ())
    _keys(item, fields, where)
    _attack_id(item["id"], where + ".id")
    for field in ("title", "persona", "asset", "category", "precondition", "impact", "zone"):
        _string(item[field], where + "." + field)
    if item["severity"] not in SEVERITIES:
        _fail(where + ".severity", "expected one of " + ", ".join(SEVERITIES))
    _location(item["entry_point"], where + ".entry_point")
    _location(item["sink"], where + ".sink")
    steps = _string_list(item["steps"], where + ".steps")
    evidence = _string_list(item["evidence"], where + ".evidence")
    if not steps:
        _fail(where + ".steps", "must contain at least one step")
    if not evidence:
        _fail(where + ".evidence", "must contain at least one redacted evidence item")
    _string_list(item["mitigations"], where + ".mitigations")
    _string_list(item["capabilities_required"], where + ".capabilities_required")
    _string_list(item["capabilities_gained"], where + ".capabilities_gained")
    links = _array(item["chain_links"], where + ".chain_links")
    for index, link in enumerate(links):
        link_where = f"{where}.chain_links[{index}]"
        record = _object(link, link_where)
        fields = ("file", "line", "role", "id") if "id" in record else ("file", "line", "role")
        _keys(record, fields, link_where)
        _location({"file": record["file"], "line": record["line"]}, link_where)
        _string(record["role"], link_where + ".role")
        if "id" in record:
            _attack_id(record["id"], link_where + ".id")
            if record["id"] == item["id"]:
                _fail(link_where + ".id", f"self chain reference is not allowed: {item['id']}")


def _unique_ids(items: List[Dict[str, Any]], field: str, where: str) -> List[str]:
    seen = set()
    result = []
    for index, item in enumerate(items):
        identifier = item[field]
        if identifier in seen:
            _fail(f"{where}[{index}].{field}", f"duplicate ID {identifier}")
        seen.add(identifier)
        result.append(identifier)
    return result


def validate_attacker(value: Any) -> Dict[str, Any]:
    item = _object(value, "attacker")
    _keys(item, ("schema_version", "zone", "metadata", "paths"), "attacker")
    _integer(item["schema_version"], "attacker.schema_version", 1, 1)
    _string(item["zone"], "attacker.zone")
    _metadata(item["metadata"], "attacker.metadata")
    paths = _array(item["paths"], "attacker.paths")
    for index, path in enumerate(paths):
        _attacker_path(path, f"attacker.paths[{index}]")
        if item["zone"] != "MERGED" and path["zone"] != item["zone"]:
            _fail(f"attacker.paths[{index}].zone", "must exactly match attacker.zone")
    _unique_ids(paths, "id", "attacker.paths")
    return item


def validate_blue(value: Any, attacks: Optional[Dict[str, Any]] = None, selected_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    item = _object(value, "blue")
    _keys(item, ("schema_version", "evaluations"), "blue")
    if item["schema_version"] != SCHEMA_VERSION or isinstance(item["schema_version"], bool):
        _fail("blue.schema_version", "expected 1")
    evaluations = _array(item["evaluations"], "blue.evaluations")
    fields = (
        "id",
        "decision",
        "verification_method",
        "precondition_check",
        "control_analysis",
        "assessment",
        "evidence",
        "residual_concern",
        "confidence",
    )
    for index, evaluation in enumerate(evaluations):
        where = f"blue.evaluations[{index}]"
        entry = _object(evaluation, where)
        _keys(entry, fields, where)
        _attack_id(entry["id"], where + ".id")
        if entry["decision"] not in DECISIONS:
            _fail(where + ".decision", "expected one of " + ", ".join(DECISIONS))
        for field in ("verification_method", "precondition_check", "control_analysis", "assessment"):
            _string(entry[field], where + "." + field)
        evidence = _string_list(entry["evidence"], where + ".evidence")
        if not evidence:
            _fail(where + ".evidence", "must contain at least one redacted evidence item")
        _string(entry["residual_concern"], where + ".residual_concern", allow_empty=True)
        _integer(entry["confidence"], where + ".confidence", 0, 100)
    actual = set(_unique_ids(evaluations, "id", "blue.evaluations"))
    if attacks is not None:
        incoming = {path["id"] for path in attacks["paths"]}
        expected = set(selected_ids) if selected_ids is not None else incoming
        unknown = expected - incoming
        if unknown:
            _fail("--id", "ID(s) absent from Attacker payload: " + ", ".join(sorted(unknown)))
        _reconcile_ids(actual, expected, "blue.evaluations")
    return item


def _reconcile_ids(actual: set, expected: set, where: str) -> None:
    missing = expected - actual
    extra = actual - expected
    problems = []
    if missing:
        problems.append("missing " + ", ".join(sorted(missing)))
    if extra:
        problems.append("unexpected " + ", ".join(sorted(extra)))
    if problems:
        _fail(where, "ID set does not match input (" + "; ".join(problems) + ")")


def _calculate_stats(paths: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "total": len(paths),
        "exploitable": sum(path["verdict"] == "EXPLOITABLE" for path in paths),
        "mitigated": sum(path["verdict"] == "MITIGATED" for path in paths),
        "not_a_risk": sum(path["verdict"] == "NOT_A_RISK" for path in paths),
        "undetermined": sum(path["verdict"] == "UNDETERMINED" for path in paths),
        "severity_counts": {
            severity: sum(path["true_severity"] == severity for path in paths)
            for severity in SEVERITIES
        },
    }


def _stats(value: Any, paths: List[Dict[str, Any]], where: str = "arbiter.stats") -> None:
    item = _object(value, where)
    fields = ("total", "exploitable", "mitigated", "not_a_risk", "undetermined", "severity_counts")
    _keys(item, fields, where)
    for field in fields[:-1]:
        _integer(item[field], where + "." + field, 0)
    severity = _object(item["severity_counts"], where + ".severity_counts")
    _keys(severity, SEVERITIES, where + ".severity_counts")
    for field in SEVERITIES:
        _integer(severity[field], where + ".severity_counts." + field, 0)

    expected = _calculate_stats(paths)
    for field in fields[:-1]:
        if item[field] != expected[field]:
            _fail(where + "." + field, f"expected {expected[field]} from path records, got {item[field]}")
    for severity_name in SEVERITIES:
        count = expected["severity_counts"][severity_name]
        if severity[severity_name] != count:
            _fail(
                where + ".severity_counts." + severity_name,
                f"expected {count} across all path records, got {severity[severity_name]}",
            )


def validate_arbiter(
    value: Any,
    attacks: Optional[Dict[str, Any]] = None,
    blue_evaluations: Optional[List[Dict[str, Any]]] = None,
    selected_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    item = _object(value, "arbiter")
    _keys(item, ("schema_version", "coverage", "paths", "stats"), "arbiter")
    if item["schema_version"] != SCHEMA_VERSION or isinstance(item["schema_version"], bool):
        _fail("arbiter.schema_version", "expected 1")
    _coverage(item["coverage"], "arbiter.coverage", final=True)
    paths = _array(item["paths"], "arbiter.paths")
    for index, path in enumerate(paths):
        where = f"arbiter.paths[{index}]"
        _attacker_path(path, where, arbiter=True)
        if path["verdict"] not in VERDICTS:
            _fail(where + ".verdict", "expected one of " + ", ".join(VERDICTS))
        _string(path["analysis"], where + ".analysis")
        _integer(path["confidence"], where + ".confidence", 0, 100)
        if path["true_severity"] not in SEVERITIES:
            _fail(where + ".true_severity", "expected one of " + ", ".join(SEVERITIES))
        _string(path["defense"], where + ".defense", allow_empty=True)
        _string(path["remediation"], where + ".remediation", allow_empty=True)
        _string(path["remediation_complexity"], where + ".remediation_complexity")
        _string(path["remediation_risks"], where + ".remediation_risks", allow_empty=True)
        _string(path["validation"], where + ".validation", allow_empty=True)
    actual = set(_unique_ids(paths, "id", "arbiter.paths"))
    _stats(item["stats"], paths)

    if attacks is not None:
        incoming = {path["id"]: path for path in attacks["paths"]}
        expected_ids = set(incoming) if selected_ids is None else set(selected_ids)
        unknown_ids = expected_ids - set(incoming)
        if unknown_ids:
            _fail("arbiter selected IDs", "ID(s) absent from Attacker payload: " + ", ".join(sorted(unknown_ids)))
        _reconcile_ids(actual, expected_ids, "arbiter.paths")
        for index, path in enumerate(paths):
            source = incoming.get(path["id"])
            if source is None:
                continue
            for field in ATTACKER_PATH_FIELDS:
                if path[field] != source[field]:
                    _fail(
                        f"arbiter.paths[{index}].{field}",
                        f"must retain the Attacker value for {path['id']}",
                    )
        if selected_ids is None:
            final_zones = {
                entry["zone"]
                for field in ("assessed", "skipped")
                for entry in item["coverage"][field]
            }
            required_zones = set()
            for field in ("assigned", "assessed", "skipped"):
                required_zones.update(attacks["metadata"]["coverage"][field])
            missing_zones = required_zones - final_zones
            if missing_zones:
                _fail(
                    "arbiter.coverage",
                    "zone(s) disappeared from assessed/skipped coverage: " + ", ".join(sorted(missing_zones)),
                )
    if blue_evaluations is not None:
        blue_ids = {evaluation["id"] for evaluation in blue_evaluations}
        _reconcile_ids(blue_ids, actual, "combined Blue Team evaluations")
    return item


def _read_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    try:
        with open(source, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise ReportError(f"cannot read {source}: {exc}")


def _json(text: str, source: str) -> Dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportError(f"{source}: invalid or truncated JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    return _object(value, source)


def extract_marked(text: str, stage: str, source: str = "<input>") -> Dict[str, Any]:
    start, end = MARKERS[stage]
    if text.count(start) != 1 or text.count(end) != 1:
        _fail(source, f"expected exactly one {start} and one {end} marker")
    start_at = text.index(start) + len(start)
    end_at = text.index(end)
    if end_at < start_at:
        _fail(source, f"{end} appears before {start}")
    return _json(text[start_at:end_at].strip(), source + " marked payload")


def _load_validated(source: str) -> Dict[str, Any]:
    return _json(_read_text(source), source)


def _union(groups: Iterable[Iterable[str]]) -> List[str]:
    result = []
    seen = set()
    for group in groups:
        for value in group:
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


def merge_attackers(payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not payloads:
        raise ReportError("merge requires at least one validated Attacker payload")
    for payload in payloads:
        validate_attacker(payload)
    paths = [path for payload in payloads for path in payload["paths"]]
    identifiers = set(_unique_ids(paths, "id", "merged.paths"))
    for path in paths:
        for link in path["chain_links"]:
            reference = link.get("id")
            if reference is not None and reference not in identifiers:
                _fail(
                    f"merged.paths.{path['id']}.chain_links",
                    f"dangling cross-zone reference {reference}",
                )
    metadata = [payload["metadata"] for payload in payloads]

    def joined(field: str) -> str:
        return " | ".join(_union(item[field] for item in metadata))

    merged = {
        "schema_version": SCHEMA_VERSION,
        "zone": "MERGED",
        "metadata": {
            "shape": joined("shape"),
            "stack": joined("stack"),
            "target": joined("target"),
            "files_assessed": sum(item["files_assessed"] for item in metadata),
            "files_assessed_sample": _union(item["files_assessed_sample"] for item in metadata),
            "entry_points": _union(item["entry_points"] for item in metadata),
            "crown_jewels": _union(item["crown_jewels"] for item in metadata),
            "personas": _union(item["personas"] for item in metadata),
            "trust_boundaries": _union(item["trust_boundaries"] for item in metadata),
            "coverage": {
                field: _union(item["coverage"][field] for item in metadata)
                for field in ("assigned", "assessed", "skipped", "notes")
            },
        },
        "paths": paths,
    }
    return validate_attacker(merged)


def merge_arbiters(
    payloads: List[Dict[str, Any]],
    attacks: Dict[str, Any],
    blue_evaluations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Combine disjoint Arbiter batches and validate the complete assessment."""
    if not payloads:
        raise ReportError("merge-arbiters requires at least one validated Arbiter payload")
    validate_attacker(attacks)
    validate_blue({"schema_version": SCHEMA_VERSION, "evaluations": blue_evaluations}, attacks)
    for payload in payloads:
        validate_arbiter(payload)
    paths = [path for payload in payloads for path in payload["paths"]]
    _unique_ids(paths, "id", "merged-arbiters.paths")

    def coverage_entries(field: str) -> List[Dict[str, Any]]:
        result = []
        by_zone = {}
        for payload in payloads:
            for entry in payload["coverage"][field]:
                zone = entry["zone"]
                if zone not in by_zone:
                    record = {"zone": zone, "files": []}
                    by_zone[zone] = record
                    result.append(record)
                files = by_zone[zone]["files"]
                for filename in entry["files"]:
                    if filename not in files:
                        files.append(filename)
        return result

    merged = {
        "schema_version": SCHEMA_VERSION,
        "coverage": {
            "assessed": coverage_entries("assessed"),
            "skipped": coverage_entries("skipped"),
            "limitations": _union(payload["coverage"]["limitations"] for payload in payloads),
        },
        "paths": paths,
        "stats": _calculate_stats(paths),
    }
    return validate_arbiter(merged, attacks, blue_evaluations)


def _location_text(location: Dict[str, Any]) -> str:
    return location["file"] + (f":{location['line']}" if location["line"] is not None else "")


def _worst_first(paths: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rank = {severity: index for index, severity in enumerate(SEVERITIES)}
    return sorted(paths, key=lambda path: rank[path["true_severity"]])


def _inline(items: List[str]) -> str:
    return "; ".join(items) if items else "None recorded."


def _coverage_inline(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return "None recorded."
    return "; ".join(
        entry["zone"] + (" (files: " + ", ".join(entry["files"]) + ")" if entry["files"] else "")
        for entry in entries
    )


def _chain_inline(links: List[Dict[str, Any]]) -> str:
    if not links:
        return "None recorded."
    return "; ".join(
        f"{_location_text(link)} · {link['role']}" + (f" · {link['id']}" if "id" in link else "")
        for link in links
    )


def _fragile(path: Dict[str, Any]) -> bool:
    return path["verdict"] == "MITIGATED" and re.search(r"\bfragile\b", path["defense"], re.IGNORECASE) is not None


def _summary(payload: Dict[str, Any]) -> str:
    stats = payload["stats"]
    exploitable = _worst_first([path for path in payload["paths"] if path["verdict"] == "EXPLOITABLE"])
    lines = [
        "RED-TEAM ASSESSMENT",
        (
            f"Paths: {stats['total']} · Exploitable: {stats['exploitable']} · "
            f"Mitigated: {stats['mitigated']} · Not a risk: {stats['not_a_risk']} · "
            f"Undetermined: {stats['undetermined']}"
        ),
        "Exploitable severity: " + " · ".join(
            f"{severity} {sum(path['true_severity'] == severity for path in exploitable)}"
            for severity in SEVERITIES
        ),
        "Coverage:",
        "  Assessed: " + _coverage_inline(payload["coverage"]["assessed"]),
        "  Skipped/exclusions: " + _coverage_inline(payload["coverage"]["skipped"]),
        "  Limitations: " + _inline(payload["coverage"]["limitations"]),
        "",
        "Worst exploitable paths:",
    ]
    if not exploitable:
        lines.append("No paths were confirmed exploitable.")
        if payload["paths"]:
            lines.append("Attempted paths and their current outcome:")
            for path in payload["paths"]:
                detail = path["defense"] if path["verdict"] == "MITIGATED" else path["analysis"]
                lines.append(
                    f"- {path['id']} · {path['title']} · {path['verdict']} · "
                    f"{detail or 'No detail recorded.'}"
                )
    for index, path in enumerate(exploitable[:10], 1):
        lines.append(
            f"{index}. {_location_text(path['entry_point'])} · {path['title']} · "
            f"{path['true_severity']} · {path['id']}"
        )
    remainder = exploitable[10:]
    if remainder:
        lines.append(
            "Additional exploitable paths: "
            + " · ".join(
                f"{severity} {sum(path['true_severity'] == severity for path in remainder)}"
                for severity in SEVERITIES
                if any(path["true_severity"] == severity for path in remainder)
            )
        )
    fragile = [path for path in payload["paths"] if _fragile(path)]
    lines.extend(["", "Fragile controls:"])
    if fragile:
        for path in fragile:
            lines.append(f"- {path['id']} · {path['title']} · {path['defense'] or 'Fragile control recorded.'}")
    else:
        lines.append("None recorded.")
    lines.extend(
        [
            f"Undetermined paths requiring more evidence: {stats['undetermined']}",
            "Say 'look at N' for a summary-listed path, 'look at ATTACK-…-ID' for any path, "
            "'what stopped the rest' for stopped/dismissed paths, or 'full report' for everything.",
        ]
    )
    return "\n".join(lines)


def _record_lines(path: Dict[str, Any]) -> List[str]:
    lines = [
        f"#### {path['id']} — {path['title']}",
        f"- Verdict: {path['verdict']} · True severity: {path['true_severity']} · Confidence: {path['confidence']}/100",
        f"- Persona: {path['persona']}",
        f"- Asset: {path['asset']}",
        f"- Entry point: {_location_text(path['entry_point'])}",
        f"- Sink: {_location_text(path['sink'])}",
        f"- Category: {path['category']} · Zone: {path['zone']}",
        f"- Precondition: {path['precondition']}",
        "- Attack steps:",
    ]
    lines.extend(f"  {index}. {step}" for index, step in enumerate(path["steps"], 1))
    lines.append("- Evidence (secret values redacted):")
    lines.extend(f"  - {item}" for item in path["evidence"])
    lines.extend(
        [
            f"- Impact: {path['impact']}",
            "- Existing mitigations: " + _inline(path["mitigations"]),
            "- Capabilities required: " + _inline(path["capabilities_required"]),
            "- Capabilities gained: " + _inline(path["capabilities_gained"]),
            "- Chain links: " + _chain_inline(path["chain_links"]),
            f"- Arbiter analysis: {path['analysis']}",
            "- Defense: " + (path["defense"] or "None recorded."),
            f"- Fragile control: {'yes' if _fragile(path) else 'no'}",
            "- Remediation: " + (path["remediation"] or "None recorded."),
            f"- Remediation complexity: {path['remediation_complexity']}",
            "- Remediation risks: " + (path["remediation_risks"] or "None recorded."),
            "- Validation: " + (path["validation"] or "None recorded."),
        ]
    )
    return lines


def _full(payload: Dict[str, Any]) -> str:
    lines = [_summary(payload), "", "FULL PATH RECORDS"]
    for severity in SEVERITIES:
        lines.extend(["", f"### {severity}"])
        paths = [path for path in payload["paths"] if path["true_severity"] == severity]
        if not paths:
            lines.append("None.")
        for path in paths:
            lines.extend(["", *_record_lines(path)])
    return "\n".join(lines)


def _stopped(payload: Dict[str, Any]) -> str:
    lines = ["STOPPED, DISMISSED, AND UNDETERMINED PATHS"]
    groups = (
        ("MITIGATED", "Mitigated — currently stopped", "defense"),
        ("NOT_A_RISK", "Not a risk — dismissed", "analysis"),
        ("UNDETERMINED", "Undetermined — more evidence required", "analysis"),
    )
    for verdict, heading, field in groups:
        lines.extend(["", heading + ":"])
        paths = [path for path in payload["paths"] if path["verdict"] == verdict]
        if not paths:
            lines.append("None.")
        for path in paths:
            lines.append(
                f"- {path['id']} · {path['title']} · {path['true_severity']} · "
                f"{path[field] or 'No detail recorded.'}"
            )
    return "\n".join(lines)


def _path(payload: Dict[str, Any], id_or_index: str) -> str:
    path = next((item for item in payload["paths"] if item["id"] == id_or_index), None)
    if path is None and id_or_index.isdigit():
        index = int(id_or_index)
        exploitable = _worst_first([item for item in payload["paths"] if item["verdict"] == "EXPLOITABLE"])
        if 1 <= index <= len(exploitable):
            path = exploitable[index - 1]
        else:
            raise ReportError(f"summary index {index} is out of range 1..{len(exploitable)}")
    if path is None:
        raise ReportError(f"no Arbiter path has ID or summary index {id_or_index!r}")
    return "\n".join(_record_lines(path))


def render(payload: Dict[str, Any], view: str, id_or_index: Optional[str] = None) -> str:
    validate_arbiter(payload)
    if view == "summary":
        return _summary(payload)
    if view == "full":
        return _full(payload)
    if view == "stopped":
        return _stopped(payload)
    if view == "path":
        if id_or_index is None:
            raise ReportError("render path requires an Attacker ID or summary index")
        return _path(payload, id_or_index)
    raise ReportError(f"unknown render view {view}")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ReportError(f"bad arguments: {message}")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="report.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate marked Attacker, Blue Team, or Arbiter output")
    validate.add_argument("stage", choices=tuple(MARKERS))
    validate.add_argument("input", nargs="?", default="-", help="raw marked output file, or - for stdin")
    validate.add_argument("--attacks", help="validated Attacker JSON (required for blue and arbiter)")
    validate.add_argument(
        "--blue",
        action="append",
        default=[],
        help="validated Blue Team JSON; repeat for disjoint batches (required for arbiter)",
    )
    validate.add_argument(
        "--id",
        action="append",
        dest="selected_ids",
        help="restrict Blue Team or Arbiter evaluation to this incoming ID; repeat for a bounded batch",
    )

    merge = commands.add_parser("merge", help="merge validated Attacker JSON payloads")
    merge.add_argument("inputs", nargs="+", help="validated Attacker JSON files")

    combine = commands.add_parser("merge-arbiters", help="combine validated disjoint Arbiter batches")
    combine.add_argument("inputs", nargs="+", help="validated Arbiter batch JSON files")
    combine.add_argument("--attacks", required=True, help="validated merged Attacker JSON")
    combine.add_argument(
        "--blue",
        action="append",
        required=True,
        help="validated Blue Team JSON; repeat for every disjoint batch",
    )

    render_parser = commands.add_parser("render", help="render a validated Arbiter payload")
    render_parser.add_argument("view", choices=("summary", "full", "stopped", "path"))
    render_parser.add_argument("input", help="validated Arbiter JSON file")
    render_parser.add_argument("id_or_index", nargs="?", help="path ID or worst-first summary index")
    return parser


def _selected_ids(values: Optional[List[str]]) -> Optional[List[str]]:
    if values is None:
        return None
    seen = set()
    for index, value in enumerate(values):
        _attack_id(value, f"--id[{index}]")
        if value in seen:
            _fail("--id", f"duplicate selected ID {value}")
        seen.add(value)
    return values


def _load_blue_batch(source: str) -> Dict[str, Any]:
    return validate_blue(_load_validated(source))


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "validate":
            if args.stage == "attacker" and (args.attacks or args.blue or args.selected_ids is not None):
                raise ReportError("validate attacker does not accept --attacks, --blue, or --id")
            if args.stage == "blue" and not args.attacks:
                raise ReportError("validate blue requires --attacks; add --id for each ID in a bounded batch")
            if args.stage == "blue" and args.blue:
                raise ReportError("validate blue does not accept --blue")
            if args.stage == "arbiter" and (not args.attacks or not args.blue):
                raise ReportError("validate arbiter requires --attacks and at least one --blue")

            log(f"validating {args.stage} payload from {args.input}")
            raw = extract_marked(_read_text(args.input), args.stage, args.input)
            if args.stage == "attacker":
                normalized = validate_attacker(raw)
            elif args.stage == "blue":
                attacks = validate_attacker(_load_validated(args.attacks))
                normalized = validate_blue(raw, attacks, _selected_ids(args.selected_ids))
            else:
                attacks = validate_attacker(_load_validated(args.attacks))
                batches = [_load_blue_batch(source) for source in args.blue]
                evaluations = []
                seen = set()
                for source, batch in zip(args.blue, batches):
                    for evaluation in batch["evaluations"]:
                        if evaluation["id"] in seen:
                            raise ReportError(f"{source}: duplicate Blue Team evaluation across batches: {evaluation['id']}")
                        seen.add(evaluation["id"])
                        evaluations.append(evaluation)
                selected_ids = _selected_ids(args.selected_ids)
                if selected_ids is not None:
                    allowed_ids = set(selected_ids)
                    evaluations = [entry for entry in evaluations if entry["id"] in allowed_ids]
                validate_blue(
                    {"schema_version": SCHEMA_VERSION, "evaluations": evaluations},
                    attacks,
                    selected_ids,
                )
                normalized = validate_arbiter(raw, attacks, evaluations, selected_ids)
            json.dump(normalized, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
            sys.stdout.write("\n")
            log(f"validated {args.stage} payload")
            return 0

        if args.command == "merge":
            log(f"merging {len(args.inputs)} validated Attacker payloads")
            merged = merge_attackers([_load_validated(source) for source in args.inputs])
            json.dump(merged, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
            sys.stdout.write("\n")
            log(f"merged {len(merged['paths'])} attack paths")
            return 0

        if args.command == "merge-arbiters":
            log(f"combining {len(args.inputs)} validated Arbiter batches")
            attacks = validate_attacker(_load_validated(args.attacks))
            batches = [_load_blue_batch(source) for source in args.blue]
            evaluations = []
            seen = set()
            for source, batch in zip(args.blue, batches):
                for evaluation in batch["evaluations"]:
                    if evaluation["id"] in seen:
                        raise ReportError(f"{source}: duplicate Blue Team evaluation across batches: {evaluation['id']}")
                    seen.add(evaluation["id"])
                    evaluations.append(evaluation)
            merged = merge_arbiters(
                [_load_validated(source) for source in args.inputs],
                attacks,
                evaluations,
            )
            json.dump(merged, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
            sys.stdout.write("\n")
            log(f"combined {len(merged['paths'])} Arbiter path records")
            return 0

        if args.view != "path" and args.id_or_index is not None:
            raise ReportError(f"render {args.view} does not accept an ID or index")
        log(f"rendering {args.view} from {args.input}")
        sys.stdout.write(render(_load_validated(args.input), args.view, args.id_or_index) + "\n")
        return 0
    except ReportError as exc:
        print(f"{PREFIX} error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
