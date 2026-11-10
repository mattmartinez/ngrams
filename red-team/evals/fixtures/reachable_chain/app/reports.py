"""Report storage backed by tenant-specific files."""

from pathlib import Path

REPORT_ROOT = Path("/srv/reports")


def read_report(tenant, report_name):
    path = REPORT_ROOT / tenant / report_name
    return path.read_text(encoding="utf-8")
