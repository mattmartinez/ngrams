#!/usr/bin/env python3
"""
Evaluate bug-hunt results against the benchmark manifest.

Usage:
    python evaluate.py <referee_output_file> <manifest_path> [--description TEXT] [--commit SHA]

Reads the referee's structured output, matches confirmed bugs against
the manifest of planted bugs and traps, then computes:
  - Recall:            planted bugs correctly found / total planted
  - Precision:         true positives / (true positives + false positives)
  - F1:                harmonic mean of precision and recall
  - Severity accuracy: correctly-rated bugs / total confirmed true bugs
  - Trap resistance:   1 - (traps falsely reported as bugs / total traps)
  - Composite:         F1 × (0.5 + 0.5·severity_accuracy) × trap_resistance

Outputs a JSON object with all scores plus detailed match info on stdout,
and a trace on stderr. Appends a one-line summary to results.tsv (if it
exists) only when --description is given.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

LINE_TOLERANCE = 10  # lines of fuzz when matching reported → planted
RESULTS_TSV = Path(__file__).resolve().parent / "results.tsv"

log = logging.getLogger("evaluate")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

REPORT_START = "===REFEREE_REPORT_START==="
REPORT_END = "===REFEREE_REPORT_END==="

FILE_PATTERN = re.compile(
    r"\*\*File:\*\*\s*`?([^\s`*:]+)`?(?::(\d+)(?:-\d+)?)?`?"
)
# Fallback: bare backtick path like `path/to/file.py:42`
FILE_PATTERN_BARE = re.compile(
    r"`([^\s`]+\.(?:py|js|ts|go|rs|java|rb|c|cpp|h))"
    r"(?::(\d+)(?:-\d+)?)?`"
)
HEADING_PATTERN = re.compile(r"^###[ \t]*(.*?)[ \t]*$", re.MULTILINE)
SEVERITY_HEADING = re.compile(r"(Critical|Medium|Low)\s*:?", re.IGNORECASE)
BUG_HEADING = re.compile(r"BUG-(\d+)\s*[—–-]")
VERDICT_HEADER = re.compile(r"\*\*BUG-(\d+)\*\*")
REAL_BUG = re.compile(r"VERDICT:\s*REAL\s*BUG", re.IGNORECASE)
TRUE_SEVERITY = re.compile(
    r"\*\*True severity:\*?\*?\s*(Critical|Medium|Low)", re.IGNORECASE
)


# One `N` or `N-M` token, with optional spaces/backticks around it.
LINE_TOKEN = re.compile(r"[ \t`]*(\d+)(?:[ \t]*[-–][ \t]*\d+)?[ \t`]*")


def _file_and_lines(m) -> tuple[str, list[int]]:
    """Path from a File: match plus the start of every range cited after it.

    Reads the rest of the same line, e.g. `path:13, 120-127` -> [13, 120].
    """
    if not m:
        return "", []
    rest = m.string[m.end(1):].split("\n", 1)[0].lstrip(" \t`")
    starts: list[int] = []
    if rest.startswith(":"):
        pos = 1
        while (tm := LINE_TOKEN.match(rest, pos)):
            starts.append(int(tm.group(1)))
            pos = tm.end()
            if not rest.startswith(",", pos):
                break
            pos += 1
    return m.group(1), starts


def _parse_final_report(text: str) -> list[dict]:
    """Confirmed bugs from the final report's Critical/Medium/Low sections."""
    start, end = text.find(REPORT_START), text.find(REPORT_END)
    if start != -1 and end > start:
        report = text[start + len(REPORT_START):end]
        log.info("final report: using text between report markers (%d chars)", len(report))
    else:
        report = text
        log.info("final report: markers not found, scanning whole text")

    headings = list(HEADING_PATTERN.finditer(report))
    confirmed: list[dict] = []
    severity = None  # None = not inside a confirmed-bug section
    for i, h in enumerate(headings):
        title = h.group(1)
        if SEVERITY_HEADING.fullmatch(title):
            severity = SEVERITY_HEADING.fullmatch(title).group(1).capitalize()
            log.info("final report: section %s", severity)
            continue
        bm = BUG_HEADING.match(title)
        if not bm:
            if severity is not None:
                log.info("final report: heading %r ends the confirmed sections", title)
            severity = None
            continue
        bug_id = f"BUG-{bm.group(1)}"
        if severity is None:
            log.info("final report: %s skipped, not under Critical/Medium/Low", bug_id)
            continue
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(report)
        block = report[h.end():block_end]
        filepath, starts = _file_and_lines(FILE_PATTERN.search(block))
        if not filepath:
            log.info("final report: %s has no File: line", bug_id)
        log.info("final report: %s confirmed severity=%s file=%r line_starts=%s",
                 bug_id, severity, filepath, starts)
        confirmed.append({
            "id": bug_id,
            "file": filepath,
            "line_start": starts[0] if starts else 0,
            "line_starts": starts,
            "severity": severity,
        })
    return confirmed


def _parse_verdict_blocks(text: str) -> list[dict]:
    """Confirmed bugs from per-bug verdict blocks, one block at a time."""
    headers = list(VERDICT_HEADER.finditer(text))
    confirmed: list[dict] = []
    for i, h in enumerate(headers):
        bug_id = f"BUG-{h.group(1)}"
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[h.end():block_end]
        if not REAL_BUG.search(block):
            log.info("verdict blocks: %s not confirmed (no VERDICT: REAL BUG in its block)", bug_id)
            continue
        sm = TRUE_SEVERITY.search(block)
        if not sm:
            log.info("verdict blocks: %s skipped, no True severity in its block", bug_id)
            continue
        severity = sm.group(1).capitalize()
        fm = FILE_PATTERN.search(block) or FILE_PATTERN_BARE.search(block)
        filepath, starts = _file_and_lines(fm)
        log.info("verdict blocks: %s confirmed severity=%s file=%r line_starts=%s",
                 bug_id, severity, filepath, starts)
        confirmed.append({
            "id": bug_id,
            "file": filepath,
            "line_start": starts[0] if starts else 0,
            "line_starts": starts,
            "severity": severity,
        })
    return confirmed


def parse_referee_output(text: str) -> list[dict]:
    """Extract confirmed bugs from referee output.

    Tries, in order, and returns the first that yields entries:
      1. Final report entries (### BUG-NNN — title) under ### Critical /
         ### Medium / ### Low, with the file from each entry's File: line
      2. Per-bug verdict blocks (VERDICT: REAL BUG), one block at a time
      3. Compact table rows

    Returns a list of dicts with keys: id, file, line_start, line_starts,
    severity. line_starts lists the start of every cited range, in order;
    line_start is the first of them (0 when none).
    """
    confirmed = _parse_final_report(text)
    if confirmed:
        log.info("parse path: final report, %d confirmed", len(_dedupe(confirmed)))
        return _dedupe(confirmed)

    confirmed = _parse_verdict_blocks(text)
    if confirmed:
        log.info("parse path: verdict blocks, %d confirmed", len(_dedupe(confirmed)))
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
            "line_starts": [int(m.group(3))],
            "severity": "Low",
        })

    log.info("parse path: table rows, %d confirmed", len(_dedupe(confirmed)))
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
    if not reported.get("file", "").strip():
        return False  # no file named — cannot match anything
    rp = _normalize_path(reported["file"])
    pp = _normalize_path(planted["file"])

    if rp != pp and not rp.endswith(pp) and not pp.endswith(rp):
        return False

    starts = _reported_starts(reported)
    pl_start = planted["line_start"]
    pl_end = planted.get("line_end", pl_start)

    if not starts:
        return True  # file matched but no line info — generous match

    return any(pl_start - LINE_TOLERANCE <= rl <= pl_end + LINE_TOLERANCE for rl in starts)


def _reported_starts(reported: dict) -> list[int]:
    """Every cited start line: line_starts if non-empty, else line_start."""
    starts = reported.get("line_starts") or []
    if starts:
        return list(starts)
    rl = reported.get("line_start", 0)
    return [rl] if rl else []


def _no_match_reason(reported: dict, entries: list[dict]) -> str:
    """Explain why a reported bug matched no manifest entry (for the trace)."""
    if not reported.get("file", "").strip():
        return "no file named"
    rp = _normalize_path(reported["file"])
    same_file = [
        e for e in entries
        if (pp := _normalize_path(e["file"])) == rp or rp.endswith(pp) or pp.endswith(rp)
    ]
    if not same_file:
        return f"no manifest entry for file {reported['file']!r}"
    return (
        f"line starts {_reported_starts(reported)} are all outside ±{LINE_TOLERANCE} of every "
        f"{len(same_file)} entry(ies) in {reported['file']!r} (or they were already matched)"
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_scores(manifest: dict, confirmed: list[dict]) -> dict:
    planted = manifest["bugs"]
    traps = manifest["traps"]
    neutral = manifest.get("neutral", [])

    matched_planted: set[int] = set()
    severity_correct = 0
    matched_trap_indices: set[int] = set()
    matched_neutral_indices: set[int] = set()
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
                log.info("match: %s line_starts=%s -> planted[%d] %s:%s-%s (severity %s vs %s)",
                         reported["id"], _reported_starts(reported), i, pb["file"],
                         pb["line_start"], pb.get("line_end", pb["line_start"]),
                         reported.get("severity", ""), pb["severity"])
                break

        if found_planted:
            continue

        # Check if it matched a trap
        found_trap = False
        for j, trap in enumerate(traps):
            if match_bug(reported, trap):
                matched_trap_indices.add(j)
                found_trap = True
                log.info("match: %s line_starts=%s -> trap[%d] %s:%s-%s (counts against precision and trap_resistance)",
                         reported["id"], _reported_starts(reported), j, trap["file"],
                         trap["line_start"], trap.get("line_end", trap["line_start"]))
                break

        if found_trap:
            continue

        # Check if it matched a known-but-unplanted (neutral) issue — a real bug
        # the benchmark simply doesn't track. Neither rewarded nor penalized, so
        # it does not count against precision.
        found_neutral = False
        for k, nb in enumerate(neutral):
            if match_bug(reported, nb):
                matched_neutral_indices.add(k)
                found_neutral = True
                log.info("match: %s line_starts=%s -> neutral[%d] %s:%s-%s (not scored)",
                         reported["id"], _reported_starts(reported), k, nb["file"],
                         nb["line_start"], nb.get("line_end", nb["line_start"]))
                break

        if not found_neutral:
            false_positives += 1
            log.info("no match: %s file=%r line_starts=%s -> false positive: %s",
                     reported["id"], reported.get("file", ""), _reported_starts(reported),
                     _no_match_reason(reported, planted + traps + neutral))

    tp = len(matched_planted)
    traps_triggered = len(matched_trap_indices)

    recall = tp / len(planted) if planted else 0.0
    precision = tp / (tp + false_positives + traps_triggered) if (tp + false_positives + traps_triggered) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    sev_acc = severity_correct / tp if tp > 0 else 0.0
    trap_res = 1.0 - (traps_triggered / len(traps)) if traps else 1.0
    # Severity is bounded to a 0.5–1.0 multiplier so mislabeling severities can
    # dampen — but not zero out — a run that actually found the bugs.
    composite = f1 * (0.5 + 0.5 * sev_acc) * trap_res

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
            "neutral_total": len(neutral),
            "neutrals_matched": len(matched_neutral_indices),
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


def category_breakdown(manifest: dict, matched_indices: set[int]) -> dict:
    """Return recall per bug category."""
    cats: dict[str, dict] = {}
    for i, bug in enumerate(manifest["bugs"]):
        c = bug.get("category", "unknown")
        cats.setdefault(c, {"total": 0, "found": 0})
        cats[c]["total"] += 1
        if i in matched_indices:
            cats[c]["found"] += 1
    for c in cats.values():
        c["recall"] = round(c["found"] / c["total"], 4) if c["total"] else 0.0
    return cats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate bug-hunt results against the benchmark manifest."
    )
    parser.add_argument("referee_output", help="Path to referee output file")
    parser.add_argument("manifest", help="Path to benchmark manifest JSON")
    parser.add_argument(
        "--description",
        default="",
        help="Short description of this experiment run; when given, a row is appended to results.tsv",
    )
    parser.add_argument(
        "--commit",
        default="",
        help="Commit whose prompts produced this reply, for the results.tsv row "
             "(default: the current HEAD). Use it when re-scoring a saved reply.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="evaluate: %(message)s")
    log.info("start: referee_output=%s manifest=%s description=%r commit=%r",
             args.referee_output, args.manifest, args.description, args.commit)

    output_path = Path(args.referee_output)
    manifest_path = Path(args.manifest)

    text = output_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    confirmed = parse_referee_output(text)
    log.info("parsed %d confirmed bug(s): %s", len(confirmed),
             ", ".join(b["id"] for b in confirmed) or "none")

    expected_total = len(manifest["bugs"])
    if expected_total > 0 and len(confirmed) < expected_total / 3:
        print(
            f"WARNING: Parser matched only {len(confirmed)} of {expected_total} "
            f"expected bugs; output may be malformed. Check referee output format.",
            file=sys.stderr,
        )

    scores = compute_scores(manifest, confirmed)
    matched_indices = set(scores["details"]["matched_planted_indices"])
    scores["difficulty_breakdown"] = difficulty_breakdown(manifest, matched_indices)
    scores["category_breakdown"] = category_breakdown(manifest, matched_indices)

    print(json.dumps(scores, indent=2))

    # Log to results.tsv next to this script (independent of cwd), but only
    # for a described experiment run — ad-hoc runs must not add rows.
    tsv = RESULTS_TSV
    if not args.description.strip():
        log.info("results row not written: no --description given")
    elif not tsv.exists():
        log.info("results row not written: %s does not exist", tsv)
    else:
        append_result_row(tsv, scores, args.description, args.commit)


def append_result_row(tsv: Path, scores: dict, description: str, commit: str = "") -> None:
    """Append one 8-column row for this run to the results tsv.

    commit names the commit whose prompts produced the reply; when empty,
    the current HEAD of the repo holding the tsv is used.
    """
    commit = commit.strip() or subprocess.run(
        ["git", "-C", str(tsv.parent), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip() or "n/a"
    line = (
        f"{commit}\t{scores['composite']:.6f}\t{scores['f1']:.4f}\t"
        f"{scores['recall']:.4f}\t{scores['precision']:.4f}\t"
        f"{scores['severity_accuracy']:.4f}\t{scores['trap_resistance']:.4f}\t{description}\n"
    )
    with open(tsv, "a") as f:
        f.write(line)
    log.info("results row written to %s: %s", tsv, line.rstrip("\n"))


if __name__ == "__main__":
    main()
