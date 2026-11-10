"""Order cancellation workflow."""

from ledger import credit


def cancel_order(order):
    # Every replay performs another credit; prior state is never checked.
    order["status"] = "CANCELLED"
    credit(order["account"], order["total"], order["id"])
    return order
