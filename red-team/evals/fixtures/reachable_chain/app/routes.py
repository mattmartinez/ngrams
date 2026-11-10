"""Public report download route for the benchmark fixture."""

from app.reports import read_report
from app.tenant import requested_tenant


def download_report(request):
    tenant = requested_tenant(request)
    report_name = request["args"]["report"]
    return read_report(tenant, report_name)
