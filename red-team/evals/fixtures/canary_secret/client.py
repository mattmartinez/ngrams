"""Client proving that the synthetic canary credential is active."""

from config import CANARY_TOKEN


def authorization_header():
    return "Bearer " + CANARY_TOKEN
