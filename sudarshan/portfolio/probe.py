"""The start-up probe (cross-design Y41): can the Analytics Token read the account's orders?

Two designs (the family view, the journal) depend on one fact nobody has verified: whether the
read-only Analytics Token may call ``retrieve-all``, ``get-trades-for-day`` and the order history.
At start, once, this logs the HTTP status each answered and nothing from the body, so the owner
reads the line with ``journalctl -u sudarshan-portfolio``. It spends one read of each API's own
budget as the owner, sends the token the way the reader does, and changes no state of the
reader (a 401 here does not pause the reads of the Upstox tab).

The order history is asked for an order id that cannot exist: an answer that is not 401 or 403
tells that the token may read the endpoint at all; the status is what is logged, never a body.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .upstox import API_OF

log = logging.getLogger("sudarshan.portfolio.probe")

#: A moment after start, so the feed link and the store are up before the first read.
DELAY_S = 20.0
PROBES = (
    ("orders", "/v2/order/retrieve-all", {}),
    ("trades", "/v2/order/trades/get-trades-for-day", {}),
    ("order_history", "/v2/order/history", {"order_id": "0"}),
)


async def probe(upstox: Any) -> dict[str, int | str]:
    """The status each endpoint answered, or ``"no token"``, ``"no answer"`` or ``"budget"``.
    Logs one line; the body of an answer is never read."""
    found: dict[str, int | str] = {}
    token = upstox._token()
    if token is None:
        found = {name: "no token" for name, _, _ in PROBES}
    else:
        for name, path, params in PROBES:
            if not upstox.budget.take(API_OF.get(name, name)):
                found[name] = "budget"
                continue
            try:
                answer = await upstox.client.get(
                    upstox.base_url + path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.HTTPError:
                found[name] = "no answer"
                continue
            found[name] = answer.status_code
    del token
    log.info(
        "Analytics Token probe: retrieve-all %s, get-trades-for-day %s, order history %s",
        found["orders"], found["trades"], found["order_history"],
    )  # fmt: skip
    return found


def register_job() -> None:
    from .service import job

    @job("probe")
    async def probe_job(service) -> None:
        await asyncio.sleep(DELAY_S)
        try:
            await probe(service.ensure_upstox())
        except Exception:
            log.exception("the Analytics Token probe could not run")
