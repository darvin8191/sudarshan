"""The read-only Upstox option chain client (M11.1, O1). The chain bridge is its only caller.

One call: ``GET <Upstox host>/v2/option/chain?instrument_key=&expiry_date=`` with OpenAlgo's daily
access token for the owner's session (never the Analytics Token). It goes through the shared httpx
client with an explicit timeout (connect 1 s, read 8 s), after the Upstox plugin's standard rate
limiter has given its turn, and is never retried: a caller that gets ``token`` or ``wait`` back
must not call again for it. The token is only ever placed in the request's header; it is not
logged here, not in a message and not kept. Request-driven; no thread, timer or loop.

Upstox's page says the chain is not offered for MCX; the bridge never asks for one.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import httpx

from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)

HOST = "https://api.upstox.com"
PATH = "/v2/option/chain"
TIMEOUT = httpx.Timeout(8.0, connect=1.0, pool=2.0)
#: Upstox's refusal of a token that is wrong, lapsed or revoked.
TOKEN_REFUSED = (401, 403)


class Outcome(NamedTuple):
    """``kind``: ``ok`` (``answer`` is the parsed body), ``token`` (Upstox refused the token),
    ``wait`` (Upstox, or the Upstox plugin's limiter, asks us to slow down) or ``failed`` (no
    usable answer)."""

    kind: str
    answer: Any = None


def fetch(token: str, instrument_key: str, expiry: str) -> Outcome:
    """Read one expiry's chain once. Nothing is raised: every failure is an ``Outcome``."""
    from broker.upstox.api.rate_limiter import apply_rate_limit, is_rate_limited
    from utils.broker_backpressure import BrokerBusyError

    try:
        apply_rate_limit("standard")
    except BrokerBusyError:
        return Outcome("wait")
    try:
        answer = get_httpx_client().get(
            HOST + PATH,
            params={"instrument_key": instrument_key, "expiry_date": expiry},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        logger.warning("Options: Upstox did not answer for a chain (%s)", type(exc).__name__)
        return Outcome("failed")
    status = answer.status_code
    if status in TOKEN_REFUSED:
        return Outcome("token")
    try:
        body = answer.json()
    except ValueError:
        body = None
    if is_rate_limited(status, body):
        return Outcome("wait")
    if status != 200 or not isinstance(body, dict) or body.get("status") == "error":
        logger.warning("Options: Upstox refused a chain read (status %s)", status)
        return Outcome("failed")
    return Outcome("ok", body)
