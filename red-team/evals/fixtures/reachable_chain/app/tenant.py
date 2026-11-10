"""Tenant selection for report requests."""


def requested_tenant(request):
    # The authenticated session tenant is never compared with this argument.
    return request["args"]["tenant"]
