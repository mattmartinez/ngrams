"""Optional per-repo settings for the query scanner, read from `.queryquant.json`.

Format (every key optional):
    {"ignore": ["glob", ...],
     "accept": [{"rule": "qq.offset-paging", "path": "glob", "reason": "..."}],
     "leading_column": {"column": "tenant_id", "severity": "medium"},
     "small_tables": ["glob", ...]}

Globs are matched against "/" + the path relative to the scanned root, so "**/bin/**" also matches a
top-level "bin/". `*` crosses "/" (fnmatch semantics); that is intended. `small_tables` globs match
lower-cased table names; findings on only such tables drop to low severity (see rank.py).
"""
import fnmatch
import json
import logging
import os

log = logging.getLogger("qq_scan.config")

CONFIG_NAME = ".queryquant.json"
SEVERITIES = ("high", "medium", "low")


class Config:
    """Parsed config. An empty Config means "no extra rules, no ignores, nothing accepted"."""

    def __init__(self, ignore=None, accept=None, leading_column=None, source=None, small_tables=None):
        self.small_tables = list(small_tables or [])
        self.ignore = list(ignore or [])
        self.accept = list(accept or [])
        self.leading_column = leading_column
        self.source = source

    def is_ignored(self, rel_path):
        """True if a path (relative to the scan root) matches any ignore glob."""
        probe = "/" + rel_path.replace(os.sep, "/").lstrip("/")
        for pattern in self.ignore:
            if fnmatch.fnmatchcase(probe, pattern) or fnmatch.fnmatchcase(probe + "/", pattern):
                log.debug("ignored by config: %s (glob %s)", rel_path, pattern)
                return True
        return False

    def accepted_by(self, finding):
        """Return the accept entry that covers this finding, or None."""
        probe = "/" + (finding.get("file") or "").lstrip("/")
        for entry in self.accept:
            if entry.get("rule") not in (None, finding["rule"]):
                continue
            pattern = entry.get("path") or "*"
            if fnmatch.fnmatchcase(probe, pattern) or fnmatch.fnmatchcase(probe.lstrip("/"), pattern):
                log.debug("accepted by config: %s %s:%s (%s)", finding["rule"], finding.get("file"),
                          finding.get("line"), entry.get("reason"))
                return entry
        return None


def load(root, path=None):
    """Load `path`, or `<root>/.queryquant.json` when it exists, else an empty Config.

    Raises ValueError with a plain message when the file exists but is malformed.
    """
    log.debug("config.load enter root=%s path=%s", root, path)
    if path is None:
        candidate = os.path.join(root, CONFIG_NAME)
        if not os.path.isfile(candidate):
            log.debug("no %s at %s; using defaults", CONFIG_NAME, root)
            return Config()
        path = candidate
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except OSError as exc:
        log.error("cannot read config %s: %s", path, exc)
        raise ValueError("cannot read config %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        log.error("config %s is not valid JSON: %s", path, exc)
        raise ValueError("config %s is not valid JSON: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ValueError("config %s must be a JSON object" % path)
    ignore = raw.get("ignore") or []
    accept = raw.get("accept") or []
    if not isinstance(ignore, list) or not all(isinstance(g, str) for g in ignore):
        raise ValueError("config %s: 'ignore' must be a list of glob strings" % path)
    if not isinstance(accept, list) or not all(isinstance(a, dict) and a.get("rule") for a in accept):
        raise ValueError("config %s: 'accept' entries need at least a 'rule'" % path)
    leading = raw.get("leading_column")
    if leading is not None:
        if not isinstance(leading, dict) or not leading.get("column"):
            raise ValueError("config %s: 'leading_column' needs a 'column'" % path)
        severity = leading.get("severity", "medium")
        if severity not in SEVERITIES:
            raise ValueError("config %s: leading_column severity must be one of %s" % (path, SEVERITIES))
        leading = {"column": leading["column"].lower(), "severity": severity}
    small = raw.get("small_tables") or []
    if not isinstance(small, list) or not all(isinstance(g, str) for g in small):
        raise ValueError("config %s: 'small_tables' must be a list of glob strings" % path)
    cfg = Config(ignore, accept, leading, source=os.path.abspath(path), small_tables=small)
    log.debug("config.load exit: %d ignore, %d accept, leading_column=%s, small_tables=%s", len(cfg.ignore),
              len(cfg.accept), cfg.leading_column, cfg.small_tables)
    return cfg
