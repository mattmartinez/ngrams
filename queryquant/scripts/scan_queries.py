#!/usr/bin/env python3
"""Static scan of the SQL in a Java (JDBI v3 + Liquibase) codebase. Never connects to a database.

Usage:
    python3 -I scan_queries.py PATH [--service NAME] [--json OUT] [--top N] [--config FILE] [--verbose]

PATH is a repo root holding many services, or one service. Prints a ranked list, one finding per line:
    <n>. <service> · <file:line> · <DAO.method or call> · <summary> · from code
then "… and N more (M not analysed, K accepted by config)". --json OUT writes every service (tables,
indexes), statement and finding; see qq_scan/__init__.py for the ranking and qq_scan/rules.py for the
rules. Config: PATH/.queryquant.json or --config FILE (see qq_scan/config.py).
Exit codes: 0 scanned, 2 bad arguments or config.
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qq_scan import config as config_mod  # noqa: E402
from qq_scan import scan, text_report  # noqa: E402

log = logging.getLogger("qq_scan.cli")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Static scan of SQL in a JDBI/Liquibase codebase.")
    ap.add_argument("path")
    ap.add_argument("--service", help="only this service (name as printed, or its folder name)")
    ap.add_argument("--json", dest="json_out", metavar="OUT", help="write the full facts file here")
    ap.add_argument("--top", type=int, default=10, help="findings to print (default 10)")
    ap.add_argument("--config", help="config file (default PATH/.queryquant.json if present)")
    ap.add_argument("--verbose", action="store_true", help="log every file, statement and rule decision")
    args = ap.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    log.debug("args: %s", vars(args))
    if not os.path.isdir(args.path):
        log.error("not a directory: %s", args.path)
        print("scan_queries: not a directory: %s" % args.path, file=sys.stderr)
        return 2
    try:
        cfg = config_mod.load(args.path, args.config)
    except ValueError as exc:
        print("scan_queries: %s" % exc, file=sys.stderr)
        return 2
    result, extras = scan(args.path, cfg, args.service)
    if args.service and not result["services"]:
        print("scan_queries: no service named %s under %s" % (args.service, args.path), file=sys.stderr)
        return 2
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
            fh.write("\n")
        log.debug("wrote %s", args.json_out)
    print(text_report(result, extras, max(0, args.top)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
