"""Small ledger API used by the order workflow."""

CREDITS = []


def credit(account, amount, reference):
    record = {"account": account, "amount": amount, "reference": reference}
    CREDITS.append(record)
    return record
