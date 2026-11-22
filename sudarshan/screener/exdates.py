"""Ex-dates for the "days to ex-date" line (M9.13, gap audit GA8.3), from the portfolio.

* **One source, no Upstox call.** The portfolio reads Upstox's corporate actions each night inside
  its own budget, for what the owner holds, has traded or keeps in a tab and the shares on his
  watchlists (``sudarshan/portfolio/corpactions.py``). The screener asks the portfolio, never
  Upstox, for any share.
* **Sealed, under a purpose of its own.** ``POST <portfolio>/v1/ex_dates``: one Fernet token under
  ``PORTFOLIO_SCREENER_API_INFO``, principal ``sudarshan-screener``. The portfolio opens no other op
  under that purpose and refuses this one under any other. Before each post the listener on the
  port is looked up (``alerts/loopback.py``): the portfolio runs as this service's own account, and
  nothing is posted to a port another account holds.
* **Once a day, never on a run.** At start, then each day after 08:05 (the portfolio's nightly read
  and its catch-up are over by 08:00), and every 10 minutes while it cannot be had. An ex-date is
  set weeks ahead; the nightly read is the only thing that changes one. A job of its own does it,
  so a slow answer never holds the feed's frames.
* **Not known is not "none".** A share the portfolio has not read cleanly in two weeks (one it does
  not read, being neither held nor watched), every share while nothing has been read, and every
  share three days after the last read worked, has no ex-dates here: the line is "Not checked" for
  it, never a miss. A share read with nothing listed has an empty list.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts import loopback
from sudarshan.autologin.handoff import seal
from sudarshan.autologin.schedule import IST

from .catalog import CA_KINDS

log = logging.getLogger("sudarshan.screener")

OP = "ex_dates"
PRINCIPAL = "sudarshan-screener"
TIMEOUT_S, CONNECT_S = 10.0, 1.0
#: The day's read is due from 08:05 IST; one that failed is tried again 10 minutes later.
READ_AT_MIN = 8 * 60 + 5
RETRY_MS = 10 * 60_000
#: The last read is used for three days at most.
KEEP_MS = 3 * 24 * 3_600_000
TICK_S = 60.0
#: Why a read did not happen, for /health and the journal (never shown to the owner).
WORDS = {
    "down": "the portfolio service is not answering",
    "held": "another program holds the portfolio service's port",
    "off": "the portfolio is switched off",
    "not_ready": "the portfolio's corporate actions are switched off (SUDARSHAN_PORTFOLIO_INSIGHTS)",
    "refused": "the portfolio did not accept the screener's key; restart both services",
    "failed": "the portfolio's answer could not be read",
}


class Unread(Exception):
    """A read that did not happen; ``kind`` is a key of WORDS."""

    def __init__(self, kind: str):
        super().__init__(WORDS[kind])
        self.kind = kind


@dataclass(frozen=True)
class Facts:
    """Each share's events by ISIN, as (kind, ex-date YYYYMMDD) soonest first; when last read."""

    by_isin: Mapping[str, tuple[tuple[str, int], ...]] = field(default_factory=dict)
    as_of: int | None = None


def parse(data: Any) -> Facts:
    """The portfolio's answer; a share with an event that cannot be read is left out (not known),
    never kept with the rest of its events."""
    shares = data.get("shares") if isinstance(data, Mapping) else None
    if not isinstance(shares, list):
        raise Unread("failed")
    by_isin: dict[str, tuple[tuple[str, int], ...]] = {}
    for share in shares:
        isin = share.get("isin") if isinstance(share, Mapping) else None
        listed = share.get("events") if isinstance(share, Mapping) else None
        if not isinstance(isin, str) or not isinstance(listed, list):
            continue
        events = [tuple(e) for e in listed if isinstance(e, list) and len(e) == 2]
        if len(events) == len(listed) and all(
            kind in CA_KINDS and type(day) is int and 19000101 <= day <= 29991231
            for kind, day in events
        ):
            by_isin[isin] = tuple(sorted(events, key=lambda e: (e[1], e[0])))
    as_of = data.get("as_of")
    return Facts(by_isin, as_of if type(as_of) is int else None)


class ExDatesLink:
    """The day's ex-dates, read from the portfolio and kept in memory (``service.parts``)."""

    def __init__(
        self,
        url: str,
        fernet: Fernet,
        *,
        client: httpx.AsyncClient | None = None,
        tables=loopback.TCP_TABLES,
        uid: Callable[[], int] = os.getuid,
    ):
        self.url, self.fernet, self._client = url.rstrip("/"), fernet, client
        self.tables, self.uid = tables, uid
        self.facts: Facts | None = None
        #: When the last read that worked was made (epoch ms), and the day its 08:05 read was made.
        self.read_at: int | None = None
        self.read_day: int | None = None
        self.retry_at = 0
        self.state = "never"

    def of_key(self, key: str, now: int) -> tuple[tuple[str, int], ...] | None:
        """A universe member's events (its key holds the ISIN: ``NSE_EQ|INE...``), or None."""
        facts, read_at = self.facts, self.read_at
        if facts is None or read_at is None or now - read_at > KEEP_MS:
            return None
        return facts.by_isin.get(key.rpartition("|")[2])

    def status(self) -> dict[str, Any]:
        facts = self.facts
        return {
            "state": self.state,
            "as_of": facts.as_of if facts else None,
            "shares": len(facts.by_isin) if facts else 0,
        }

    async def refresh(self, now: int) -> None:
        """Read when due (see the module's words); a read that fails keeps the last facts."""
        moment = datetime.fromtimestamp(now / 1000, IST)
        day, after = int(moment.strftime("%Y%m%d")), moment.hour * 60 + moment.minute >= READ_AT_MIN
        due = self.facts is None or (after and self.read_day != day)
        if not due or now < self.retry_at:
            return
        try:
            facts = await self.read()
        except Unread as exc:
            self.retry_at = now + RETRY_MS
            self._say(exc.kind)
            return
        self.facts, self.read_at, self.retry_at = facts, now, 0
        self.read_day = day if after else None
        self._say("ok")

    async def read(self) -> Facts:
        held = loopback.holder(self.url, self.tables, expected_uid=self.uid())
        if held in (loopback.NOBODY, loopback.OTHER):
            raise Unread("down" if held == loopback.NOBODY else "held")
        body = seal(self.fernet, OP, principal=PRINCIPAL, args={})
        try:
            if self._client is not None:
                answer = await self._post(self._client, body)
            else:
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as own:
                    answer = await self._post(own, body)
            envelope = answer.json()
        except httpx.HTTPError:
            raise Unread("down") from None
        except ValueError:
            raise Unread("failed") from None
        if not isinstance(envelope, dict):
            raise Unread("failed")
        if envelope.get("status") != "success":
            code = envelope.get("code")
            kind = "refused" if code in ("forbidden", "principal") else "failed"
            raise Unread("not_ready" if code == "not_ready" else kind)
        data = envelope.get("data")
        if isinstance(data, dict) and data.get("enabled") is False:
            raise Unread("off")
        return parse(data)

    async def _post(self, client: httpx.AsyncClient, body: bytes) -> httpx.Response:
        return await client.post(
            f"{self.url}/v1/{OP}", content=body,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_S),
        )  # fmt: skip

    def _say(self, state: str) -> None:
        """A change of state, once: a warning for a fault, info for a switch or a read."""
        if state == self.state:
            return
        self.state = state
        if state == "ok":
            log.info(
                "the ex-dates were read from the portfolio: %s shares", self.status()["shares"]
            )
        elif state in ("off", "not_ready"):
            log.info("no ex-dates from the portfolio: %s", WORDS[state])
        else:
            log.warning("the ex-dates could not be read from the portfolio: %s", WORDS[state])


async def run_ex_dates(service, *, tick_s: float = TICK_S) -> None:
    """The job: the day's ex-dates (``ExDatesLink.refresh``), looked at once a minute; nothing
    while the service holds no key for the portfolio (a test without a pepper)."""
    link = service.parts.get("exdates")
    if link is None:
        return
    while True:
        await link.refresh(service.clock())
        await asyncio.sleep(tick_s)


__all__ = ["ExDatesLink", "Facts", "Unread", "parse", "run_ex_dates"]
