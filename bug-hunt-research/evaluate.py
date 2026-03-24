#!/usr/bin/env python3
"""
Evaluate bug-hunt results against the benchmark manifest.

Usage:
    python evaluate.py <referee_output_file> <manifest_path>

Reads the referee's structured output, matches confirmed bugs against
the manifest of planted bugs and traps, then computes:
  - Recall:            planted bugs correctly found / total planted
  - Precision:         true positives / (true positives + false positives)
  - F1:                harmonic mean of precision and recall
  - Severity accuracy: correctly-rated bugs / total confirmed true bugs
  - Trap resistance:   1 - (traps falsely reported as bugs / total traps)
  - Composite:         F1 × severity_accuracy × trap_resistance

Outputs a JSON object with all scores plus detailed match info, and
appends a one-line summary to results.tsv if it exists.
"""

import json
import os
import re
import sys
from pathlib import Path

LINE_TOLERANCE = 10  # lines of fuzz when matching reported → planted


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_referee_output(text: str) -> list[dict]:
    """Extract confirmed bugs from referee output.

    Handles two output sections:
      1. Per-bug VERDICT blocks (VERDICT: REAL BUG)
      2. Final report bug entries (### BUG-NNN — title)

    Returns a list of dicts with keys: id, file, line_start, severity.
    """
    confirmed: list[dict] = []

    # --- Strategy A: parse per-bug verdict blocks ---
    # Match VERDICT: REAL BUG + True severity (value may be inside or outside bold)
    verdict_pattern = re.compile(
        r"\*\*BUG-(\d+)\*\*.*?"
        r"\*\*VERDICT:\s*REAL\s*BUG\*\*.*?"
        r"\*\*True severity:\*?\*?\s*(Critical|Medium|Low)",
        re.DOTALL | re.IGNORECASE,
    )
    file_pattern = re.compile(
        r"\*\*File:\*\*\s*`?([^\s`*:]+)(?::(\d+)(?:-\d+)?)?`?"
    )
    # Fallback: bare backtick path like `path/to/file.py:42`
    file_pattern_bare = re.compile(
        r"`([^\s`]+\.(?:py|js|ts|go|rs|java|rb|c|cpp|h))"
        r"(?::(\d+)(?:-\d+)?)?`"
    )

    for m in verdict_pattern.finditer(text):
        bug_id = m.group(1)
        severity = m.group(2).capitalize()
        # Search for the file ONLY in the text near this bug block
        # Look backward to the bug header, forward through the verdict
        bug_header_pos = text.rfind(f"**BUG-{bug_id}**", max(0, m.start() - 50), m.start() + 20)
        if bug_header_pos == -1:
            bug_header_pos = m.start()
        block = text[bug_header_pos: m.end() + 500]
        fm = file_pattern.search(block)
        if not fm:
            fm = file_pattern_bare.search(block)
        filepath = fm.group(1) if fm else ""
        line = int(fm.group(2)) if fm and fm.group(2) else 0

        confirmed.append({
            "id": f"BUG-{bug_id}",
            "file": filepath,
            "line_start": line,
            "severity": severity,
        })

    if confirmed:
        return _dedupe(confirmed)

    # --- Strategy B: parse final report sections ---
    report_bug_pattern = re.compile(
        r"###\s*BUG-(\d+)\s*[—–-]\s*(.*?)\n(.*?)(?=###|\Z)",
        re.DOTALL,
    )
    severity_pattern = re.compile(r"severity.*?(Critical|Medium|Low)", re.IGNORECASE)

    for m in report_bug_pattern.finditer(text):
        bug_id = m.group(1)
        block = m.group(3)
        fm = file_pattern.search(block)
        if not fm:
            fm = file_pattern_bare.search(block)
        filepath = fm.group(1) if fm else ""
        line = int(fm.group(2)) if fm and fm.group(2) else 0
        sm = severity_pattern.search(block)
        severity = sm.group(1).capitalize() if sm else "Medium"

        confirmed.append({
            "id": f"BUG-{bug_id}",
            "file": filepath,
            "line_start": line,
            "severity": severity,
        })

    if confirmed:
        return _dedupe(confirmed)

    # --- Strategy C: parse compact table rows ---
    row_pattern = re.compile(
        r"\|\s*(\d+)\s*\|\s*`?([^\s|`]+)`?\s*\|\s*(\d+)",
    )
    for m in row_pattern.finditer(text):
        confirmed.append({
            "id": f"BUG-{m.group(1)}",
            "file": m.group(2),
            "line_start": int(m.group(3)),
            "severity": "Low",
        })

    return _dedupe(confirmed)


def _dedupe(bugs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for b in bugs:
        if b["id"] not in seen:
            seen.add(b["id"])
            out.append(b)
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _normalize_path(p: str) -> str:
    return os.path.normpath(p).lstrip("./")


def match_bug(reported: dict, planted: dict) -> bool:
    """True if a reported bug matches a planted bug within tolerance."""
    rp = _normalize_path(reported["file"])
    pp = _normalize_path(planted["file"])

    if rp != pp and not rp.endswith(pp) and not pp.endswith(rp):
        return False

    rl = reported.get("line_start", 0)
    pl_start = planted["line_start"]
    pl_end = planted.get("line_end", pl_start)

    if rl == 0:
        return True  # file matched but no line info — generous match

    if pl_start - LINE_TOLERANCE <= rl <= pl_end + LINE_TOLERANCE:
        return True

    return False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_scores(manifest: dict, confirmed: list[dict]) -> dict:
    planted = manifest["bugs"]
    traps = manifest["traps"]

    matched_planted: set[int] = set()
    severity_correct = 0
    matched_trap_indices: set[int] = set()
    false_positives = 0

    for reported in confirmed:
        # Try matching to a planted bug
        found_planted = False
        for i, pb in enumerate(planted):
            if i in matched_planted:
                continue
            if match_bug(reported, pb):
                matched_planted.add(i)
                found_planted = True
                if reported.get("severity", "").lower() == pb["severity"].lower():
                    severity_correct += 1
                break

        if found_planted:
            continue

        # Check if it matched a trap
        found_trap = False
        for j, trap in enumerate(traps):
            if match_bug(reported, trap):
                matched_trap_indices.add(j)
                found_trap = True
                break

        if not found_trap:
            false_positives += 1

    tp = len(matched_planted)
    traps_triggered = len(matched_trap_indices)

    recall = tp / len(planted) if planted else 0.0
    precision = tp / (tp + false_positives + traps_triggered) if (tp + false_positives + traps_triggered) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    sev_acc = severity_correct / tp if tp > 0 else 0.0
    trap_res = 1.0 - (traps_triggered / len(traps)) if traps else 1.0
    composite = f1 * sev_acc * trap_res

    return {
        "composite": round(composite, 6),
        "f1": round(f1, 4),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "severity_accuracy": round(sev_acc, 4),
        "trap_resistance": round(trap_res, 4),
        "details": {
            "planted_total": len(planted),
            "planted_found": tp,
            "severity_correct": severity_correct,
            "false_positives": false_positives,
            "traps_total": len(traps),
            "traps_triggered": traps_triggered,
            "matched_planted_indices": sorted(matched_planted),
            "matched_trap_indices": sorted(matched_trap_indices),
        },
    }


# ---------------------------------------------------------------------------
# Difficulty breakdown
# ---------------------------------------------------------------------------

def difficulty_breakdown(manifest: dict, matched_indices: set[int]) -> dict:
    """Return recall per difficulty tier."""
    tiers: dict[str, dict] = {}
    for i, bug in enumerate(manifest["bugs"]):
        d = bug.get("difficulty", "unknown")
        tiers.setdefault(d, {"total": 0, "found": 0})
        tiers[d]["total"] += 1
        if i in matched_indices:
            tiers[d]["found"] += 1
    for t in tiers.values():
        t["recall"] = round(t["found"] / t["total"], 4) if t["total"] else 0.0
    return tiers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: python evaluate.py <referee_output> <manifest.json>")
        sys.exit(1)

    output_path = Path(sys.argv[1])
    manifest_path = Path(sys.argv[2])

    text = output_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    confirmed = parse_referee_output(text)
    scores = compute_scores(manifest, confirmed)
    breakdown = difficulty_breakdown(
        manifest, set(scores["details"]["matched_planted_indices"])
    )
    scores["difficulty_breakdown"] = breakdown

    print(json.dumps(scores, indent=2))

    # Append to results.tsv if it exists in cwd
    tsv = Path("results.tsv")
    if tsv.exists():
        commit = os.popen("git rev-parse --short HEAD 2>/dev/null").read().strip() or "n/a"
        line = f"{commit}\t{scores['composite']:.6f}\t{scores['f1']:.4f}\t{scores['recall']:.4f}\t{scores['precision']:.4f}\t{scores['trap_resistance']:.4f}\n"
        with open(tsv, "a") as f:
            f.write(line)


if __name__ == "__main__":
    main()
