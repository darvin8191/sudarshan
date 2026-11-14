"""The journal's account reads, all through the portfolio service (JB3', cross-design Y4).

One reader of the owner's Upstox account serves the whole ring (G2): the journal never reads the
Analytics Token and has no budget of its own. It asks sudarshan-portfolio on 127.0.0.1:5583, under
the portfolio's journal purpose (``PORTFOLIO_JOURNAL_API_INFO``, principal ``sudarshan-journal``),
for exactly these reads, each answered with ``read_at``:

    trades_day                              the day's executions (trade id, order id, exchange time)
    order_history(order_id)                 one order's rows (its type and time, for an order
                                            placed outside OpenAlgo)
    historical_trades(segment, from, to, page)
    pnl_report(segment, fy, part, page)     Upstox's matched pairs and charges
    positions, funds                        the day report's figure and the brief's lines
    positions, holdings                     what was held before the journal's first fill of an
                                            instrument, read once at first sight (review B1)
    benchmark_closes(index, from, to)       NIFTY 50's closes for the week and beta

A seal is ``{"op", "nonce", "principal": "sudarshan-journal", "args": {...}}``, one Fernet token,
60 s, once. Before each post the listener on the port is looked up (``alerts/loopback.py``): the
portfolio runs as this service's own account, and nothing is posted to a port another account
holds. A read that cannot be had raises ``PortfolioUnavailable`` with what to say; it is logged
once in ten minutes per cause, never with an account figure. Nothing is cached here: the journal
keeps what it needs in journal.db, and the portfolio shares one Upstox call among readers within
5 seconds.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts import loopback
from sudarshan.autologin.handoff import seal

log = logging.getLogger("sudarshan.journal.portfolio")

PRINCIPAL = "sudarshan-journal"
CONNECT_TIMEOUT_S = 2.0
#: The portfolio may wait for Upstox (its own timeout is 10 s) and page a report.
READ_TIMEOUT_S = 25.0
LOG_EVERY_S = 600.0
#: The causes of a read that did not happen, in the words the journal's lines and reports use.
WORDS = {
    "down": "The portfolio service is not answering, so Upstox could not be read.",
    "held": "Another program holds the portfolio service's port, so nothing was asked of it.",
    "off": "The portfolio service is switched off, so Upstox could not be read.",
    "refused": "The portfolio service did not accept the journal's request; the two may hold "
    "different keys. Restart both services.",
    "failed": "The portfolio service could not read Upstox just now.",
}


class PortfolioUnavailable(Exception):
    """A read that did not happen; ``kind`` is a key of ``WORDS`` and ``str()`` its words, or the
    portfolio's own sentence when it gave one."""

    def __init__(self, kind: str, message: str | None = None):
        super().__init__(message or WORDS[kind])
        self.kind = kind


class PortfolioLink:
    """Sealed reads from the portfolio service. One HTTP client, closed with ``aclose``."""

    def __init__(
        self,
        url: str,
        fernet: Fernet,
        *,
        client: httpx.AsyncClient | None = None,
        tables=loopback.TCP_TABLES,
        uid: Callable[[], int] = os.getuid,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.url, self.fernet = url.rstrip("/"), fernet
        self._client, self._own = client, client is None
        self.tables, self.uid, self.clock = tables, uid, clock
        self._said: dict[str, float] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S), trust_env=False
            )
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and self._own:
            await client.aclose()

    def _fail(self, kind: str, message: str | None = None) -> PortfolioUnavailable:
        now = self.clock()
        if now - self._said.get(kind, -1e9) >= LOG_EVERY_S:
            self._said[kind] = now
            log.warning("A read from the portfolio service did not happen: %s", WORDS[kind])
        return PortfolioUnavailable(kind, message)

    async def call(self, op: str, **args: Any) -> dict[str, Any]:
        """The data of one read, or PortfolioUnavailable."""
        held = loopback.holder(self.url, self.tables, expected_uid=self.uid())
        if held == loopback.NOBODY:
            raise self._fail("down")
        if held == loopback.OTHER:
            raise self._fail("held")
        body = seal(self.fernet, op, principal=PRINCIPAL, args=args)
        try:
            answer = await self.client.post(
                f"{self.url}/v1/{op}",
                content=body,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise self._fail("down") from None
        try:
            envelope = answer.json()
        except ValueError:
            envelope = None
        if not isinstance(envelope, dict):
            raise self._fail("failed")
        if envelope.get("status") != "success":
            code = envelope.get("code")
            kind = "refused" if code in ("forbidden", "principal") else "failed"
            raise self._fail(kind)
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise self._fail("failed")
        if data.get("enabled") is False:
            raise self._fail("off")
        return data

    # ---- the reads -------------------------------------------------------------------------
    async def trades_day(self) -> dict[str, Any]:
        return await self.call("trades_day")

    async def positions(self) -> dict[str, Any]:
        return await self.call("positions")

    async def holdings(self) -> dict[str, Any]:
        return await self.call("holdings")

    async def funds(self) -> dict[str, Any]:
        return await self.call("funds")

    async def order_history(self, order_id: str) -> dict[str, Any]:
        return await self.call("order_history", order_id=order_id)

    async def historical_trades(self, segment: str, start: int, end: int, page: int = 1):
        return await self.call(
            "historical_trades", segment=segment, page=page, **{"from": start, "to": end}
        )

    async def pnl_report(self, segment: str, fy: int, part: str, page: int = 1,
                         page_size: int | None = None):  # fmt: skip
        args: dict[str, Any] = {"segment": segment, "fy": fy, "part": part, "page": page}
        if page_size is not None:
            args["page_size"] = page_size
        return await self.call("pnl_report", **args)

    async def benchmark_closes(self, index: str, start: int, end: int) -> dict[str, Any]:
        return await self.call("benchmark_closes", index=index, **{"from": start, "to": end})


def link_for(service: Any) -> PortfolioLink:
    """The service's one link, built on first use and closed with the service."""
    return service.part(
        "portfolio",
        lambda svc: PortfolioLink(svc.settings.portfolio_url, svc.portfolio_key),
    )
