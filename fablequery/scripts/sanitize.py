#!/usr/bin/env python3
"""Replace known secret shapes in text with [REDACTED:<kind>] markers.

Usage:
    python3 -I sanitize.py [FILE ...] > sanitized.txt
    some-command | python3 -I sanitize.py > sanitized.txt

Reads the files in order (or stdin when none are given) and writes the
sanitized text to stdout. Logs each step and a count per kind to stderr with a
[sanitize] prefix. Secret values are never logged.

Exit codes: 0 done, 2 a file could not be read.

This catches patterns only. It cannot know that a hostname, a customer name or
a project name is private: a person or agent must still read the output.
It over-redacts on purpose: every value assigned to a key whose name contains
password, secret, token, key or credential is removed.
"""

import re
import sys
import traceback
from typing import Callable, Dict, List, Pattern, Tuple, Union

PREFIX = "[sanitize]"

Replacement = Union[str, Callable[["re.Match[str]"], str]]


def log(msg: str) -> None:
    print(f"{PREFIX} {msg}", file=sys.stderr, flush=True)


def mark(kind: str) -> str:
    return f"[REDACTED:{kind}]"


def _assignment(match: "re.Match[str]") -> str:
    """Keep the key, the separator and the quotes; drop the value."""
    value = match.group("value")
    quote = value[0] if value[:1] in ("'", '"') else ""
    return f"{match.group('key')}{match.group('sep')}{quote}{mark('assignment')}{quote}"


SECRET_KEY_WORDS = (
    r"password|passwd|passphrase|pwd|secret|token|credential|"
    r"api[_-]?key|access[_-]?key|private[_-]?key|auth[_-]?key|session[_-]?id|cookie"
)

# Order matters: whole blocks and specific shapes first, the generic
# key=value rule after them, e-mail addresses last.
RULES: List[Tuple[str, Pattern[str], Replacement]] = [
    (
        "private-key-block",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        mark("private-key-block"),
    ),
    (
        "url-credentials",
        re.compile(r"(?P<scheme>\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^@\s/]+@"),
        lambda m: f"{m.group('scheme')}{mark('url-credentials')}@",
    ),
    (
        "authorization-header",
        re.compile(
            r"(?i)(?P<head>\b(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?(?:[A-Za-z][A-Za-z0-9_-]*\s+)?)"
            r"(?!\[REDACTED)[A-Za-z0-9._~+/=-]{8,}"
        ),
        lambda m: f"{m.group('head')}{mark('authorization-header')}",
    ),
    (
        "bearer-token",
        re.compile(r"(?i)(?P<head>\bbearer\s+)(?!\[REDACTED)[A-Za-z0-9._~+/=-]{16,}"),
        lambda m: f"{m.group('head')}{mark('bearer-token')}",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"),
        mark("jwt"),
    ),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), mark("aws-access-key-id")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
        mark("github-token"),
    ),
    ("slack-token", re.compile(r"\bxox[abeprs]-[A-Za-z0-9-]{10,}"), mark("slack-token")),
    ("sk-api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), mark("sk-api-key")),
    (
        "assignment",
        re.compile(
            r"(?i)(?P<key>\b[A-Za-z0-9_.-]*(?:" + SECRET_KEY_WORDS + r")[A-Za-z0-9_.-]*)"
            r"(?P<sep>[\"']?\s*[:=]\s*)"
            r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|(?!\[REDACTED)[^\s\"',;]+)"
        ),
        _assignment,
    ),
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
        mark("email"),
    ),
]


def sanitize(text: str) -> Tuple[str, Dict[str, int]]:
    """Apply every rule in order. Return the new text and a count per kind."""
    counts: Dict[str, int] = {}
    for kind, pattern, replacement in RULES:
        text, n = pattern.subn(replacement, text)
        counts[kind] = n
        log(f"rule {kind}: {n} replaced")
    return text, counts


def read_inputs(paths: List[str]) -> str:
    if not paths:
        log("reading stdin")
        data = sys.stdin.buffer.read()
        log(f"stdin: {len(data)} bytes")
        return data.decode("utf-8", "replace")
    parts = []
    for path in paths:
        log(f"reading {path}")
        with open(path, "rb") as handle:
            data = handle.read()
        log(f"{path}: {len(data)} bytes")
        parts.append(data.decode("utf-8", "replace"))
    return "".join(parts)


def main(argv: List[str]) -> int:
    paths = argv[1:]
    if paths and paths[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    log(f"start: {len(paths)} file(s)")
    try:
        text = read_inputs(paths)
    except OSError as exc:
        log(traceback.format_exc().rstrip())
        print(f"{PREFIX} error: cannot read input: {exc}", file=sys.stderr)
        return 2
    cleaned, counts = sanitize(text)
    sys.stdout.write(cleaned)
    sys.stdout.flush()
    total = sum(counts.values())
    log(f"done: {total} replaced in {len(text)} characters")
    log("patterns only: read the output for private names before you send it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
