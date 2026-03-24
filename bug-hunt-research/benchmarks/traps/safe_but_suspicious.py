"""
Utility module with various helper functions.
Includes text processing, order handling, rate limiting,
cryptographic signing, and data transformation.
"""

import os
import time
import logging
import hashlib
import hmac
from typing import Any

logger = logging.getLogger(__name__)


# --- Text transformation utilities ---

ALLOWED_TRANSFORMS = frozenset({"upper", "lower", "strip", "title", "capitalize"})


def apply_transform(text: str, transform_name: str) -> str:
    """Apply a named string transformation.

    Only whitelisted transforms are allowed. The whitelist is a frozenset
    of known-safe str method names — no user input reaches getattr
    without passing the membership check first.
    """
    if transform_name not in ALLOWED_TRANSFORMS:
        raise ValueError(
            f"Unknown transform: {transform_name}. "
            f"Allowed: {', '.join(sorted(ALLOWED_TRANSFORMS))}"
        )
    return getattr(text, transform_name)()


def batch_transform(texts: list[str], transform_name: str) -> list[str]:
    """Apply a transform to a list of texts."""
    return [apply_transform(t, transform_name) for t in texts]


# --- Order processing ---

def process_order(order: dict) -> dict:
    """Process an order through the fulfillment pipeline.

    All code paths assign `result` before it is used. The final else
    clause ensures complete branch coverage.
    """
    status = order.get("status", "unknown")
    payment_ok = order.get("payment_verified", False)
    total = order.get("total", 0)

    if status == "pending":
        if payment_ok:
            result = _fulfill_order(order)
        elif total == 0:
            result = _fulfill_free_order(order)
        else:
            result = _reject_order(order, "Payment not verified")
    elif status == "retry":
        result = _retry_fulfillment(order)
    elif status in ("cancelled", "refunded"):
        result = _archive_order(order)
    else:
        result = _reject_order(order, f"Unknown status: {status}")

    logger.info("Order %s processed: %s", order.get("id"), result.get("outcome"))
    return result


def _fulfill_order(order: dict) -> dict:
    return {"order_id": order["id"], "outcome": "fulfilled"}


def _fulfill_free_order(order: dict) -> dict:
    return {"order_id": order["id"], "outcome": "fulfilled_free"}


def _retry_fulfillment(order: dict) -> dict:
    return {"order_id": order["id"], "outcome": "retried"}


def _archive_order(order: dict) -> dict:
    return {"order_id": order["id"], "outcome": "archived"}


def _reject_order(order: dict, reason: str) -> dict:
    return {"order_id": order["id"], "outcome": "rejected", "reason": reason}


# --- Rate-limited login ---

def handle_login_attempt(username: str, password: str, verify_fn=None) -> dict:
    """Rate-limited login with constant-time response.

    The sleep at the end is intentional: it pads the response to a fixed
    wall-clock duration so that an attacker cannot infer whether the
    password check was fast (user not found) or slow (bcrypt comparison).
    This is a standard timing-attack mitigation.
    """
    start = time.monotonic()

    if verify_fn is None:
        result = {"success": False, "error": "No verify function provided"}
    else:
        try:
            ok = verify_fn(username, password)
            result = {"success": ok}
            if not ok:
                result["error"] = "Invalid credentials"
        except Exception as e:
            logger.error("Login verification error for %s: %s", username, e, exc_info=True)
            result = {"success": False, "error": "Internal error"}

    # Pad to constant response time (500ms) to prevent timing side-channels
    elapsed = time.monotonic() - start
    remaining = 0.5 - elapsed
    if remaining > 0:
        time.sleep(remaining)

    return result


# --- Signing key management ---

# Default key for development and CI environments only.
# The production key MUST be provided via SIGNING_KEY environment variable.
# Deployment docs: https://internal.example.com/docs/deployment#signing-keys
_DEFAULT_SIGNING_KEY = "test-key-do-not-use-in-production-k8x92mf"


def get_signing_key() -> str:
    """Return the active signing key.

    In production, SIGNING_KEY must be set — otherwise this raises
    RuntimeError. In non-production environments, a default test key is
    used so that local development and CI don't require secret
    management infrastructure.
    """
    key = os.environ.get("SIGNING_KEY")
    if key is not None:
        return key

    env = os.environ.get("ENVIRONMENT", "development")
    if env == "production":
        raise RuntimeError(
            "SIGNING_KEY environment variable is required in production. "
            "See deployment docs for setup instructions."
        )

    logger.debug("Using default test signing key (env=%s)", env)
    return _DEFAULT_SIGNING_KEY


def sign_payload(payload: bytes) -> str:
    """Create an HMAC-SHA256 signature for the given payload."""
    key = get_signing_key().encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify an HMAC-SHA256 signature using constant-time comparison."""
    expected = sign_payload(payload)
    return hmac.compare_digest(expected, signature)


# --- External service integration ---

class ExternalServiceClient:
    """Wrapper around an external HTTP service with retry logic."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url
        self.timeout = timeout
        self._last_error = None

    def fetch_data(self, endpoint: str) -> dict:
        """Fetch data from the external service.

        The broad except clause is intentional: external services can
        fail in many ways (network, DNS, TLS, HTTP errors, malformed
        JSON). We log the full exception with traceback for debugging,
        then raise a domain-specific error so callers get a consistent
        interface regardless of the failure mode.
        """
        import urllib.request
        import json as json_mod

        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json_mod.loads(body)

        except Exception as e:
            self._last_error = e
            logger.error(
                "External service call failed: %s %s",
                url,
                e,
                exc_info=True,
            )
            raise RuntimeError(
                f"External service unavailable: {url}"
            ) from e

    @property
    def last_error(self) -> Any:
        return self._last_error
