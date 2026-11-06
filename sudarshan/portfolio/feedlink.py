"""The portfolio's link to sudarshan-feed (M18.1, design 5.2, cross-design X10).

The same client the market-alerts service uses (``sudarshan/alerts/market/feedlink.py``): one id
at the feed, ``PUT /claims/<id>`` with ``{"keys": {key: mode}}`` to replace and renew the set,
``GET /snapshot`` for the last tick of a key, ``GET /health`` for ``started_at``. That module also
opens a ZeroMQ socket the portfolio never needs (it reads ``/snapshot`` when it wants a price), so
the three calls are repeated here, with the same ids, paths, bodies and answers, rather than
importing a class that builds a socket.

* Client id ``sudarshan-portfolio``, mode ``ltpc``, an own cap of 200 keys, and the two index
  keys the benchmarks follow.
* ``PUT`` after any change of the set, every 10 minutes (a claim expires after 7 days unrenewed),
  and at once when the feed's ``started_at`` changes (it restarted and forgot the claims).
* A 409 (over the feed's cap or quota) or a 404 (an older feed with no claims) is logged once and
  the portfolio prices from closing records and quotes. Never a fallback to ``/subscribe``, which
  moves other clients' keys.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx

log = logging.getLogger("sudarshan.portfolio.feed")

CLIENT = "sudarshan-portfolio"
MODE = "ltpc"
CLAIM_CAP = 200
RENEW_MS = 10 * 60 * 1000
HTTP_TIMEOUT_S = 5.0
CHECK_EVERY_S = 30.0
#: A read that waits for the feed gives each call this long and the whole warming this long, so a
#: feed that accepts a connection and never answers cannot push a read past the bridge's 3 s.
READ_CALL_S = 0.6
READ_BUDGET_S = 1.5
#: The benchmark indexes (design 5.5); both are claimed so their ticks are there at the close.
INDEX_KEYS = ("NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty 500")


class FeedClient:
    """HTTP to the feed on this server. A feed that does not answer is said once, and again only
    after it has answered in between."""

    def __init__(self, feed_url: str, client: httpx.AsyncClient | None = None):
        self.url = feed_url.rstrip("/")
        self._client, self._own = client, client is None
        self.down = False
        self.call_timeout: float | None = None  # set while a read waits for the feed
        self._told: set[str] = set()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and self._own:
            await client.aclose()

    def say_once(self, name: str, text: str, *args: Any) -> None:
        if name not in self._told:
            self._told.add(name)
            log.warning(text, *args)

    async def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response | None:
        try:
            if self.call_timeout is not None:
                kwargs.setdefault("timeout", self.call_timeout)
            answer = await self.client.request(method, self.url + path, **kwargs)
        except httpx.HTTPError:
            if not self.down:
                log.warning(
                    "The price feed is not answering; prices come from the last closing "
                    "record until it does."
                )
            self.down = True
            return None
        if self.down:
            log.info("The price feed answers again.")
            self.down = False
        return answer


def json_of(answer: httpx.Response | None) -> dict:
    if answer is None:
        return {}
    try:
        found = answer.json()
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


class Claims:
    """This service's set of keys at the feed."""

    def __init__(self, feed: FeedClient, cap: int = CLAIM_CAP):
        self.feed, self.cap = feed, max(1, min(cap, CLAIM_CAP))
        self.claimed: frozenset[str] = frozenset()
        self.claimed_at = 0
        self.started: str | None = None

    async def sync(self, keys: Iterable[str], now: int) -> bool:
        """Make the feed's set for this client ``keys`` (at most the cap) when it needs it.
        Returns True when the feed holds the set after the call."""
        ordered = list(dict.fromkeys(k for k in keys if k))
        want = frozenset(ordered[: self.cap])
        if len(ordered) > self.cap:
            self.feed.say_once(
                "cap", "More than %d holdings to follow: the rest are priced by quote.", self.cap
            )
        health = await self.feed.call("GET", "/health")
        if health is None:
            return False
        started = json_of(health).get("started_at")
        restarted = started is not None and started != self.started
        if not want:
            return False
        if want == self.claimed and not restarted and now - self.claimed_at < RENEW_MS:
            return True
        answer = await self.feed.call(
            "PUT", f"/claims/{CLIENT}", json={"keys": dict.fromkeys(sorted(want), MODE)}
        )
        if answer is None:
            return False
        if answer.status_code == 200:
            self.claimed, self.claimed_at, self.started = want, now, started
            return True
        if answer.status_code == 404:
            self.feed.say_once(
                "noclaims",
                "The feed does not take claims yet; the portfolio prices from closing "
                "records and quotes.",
            )
        elif answer.status_code == 409:
            self.feed.say_once(
                "claimcap",
                "The feed has no room for the portfolio's keys; they are priced from closing "
                "records and quotes.",
            )
        else:
            self.feed.say_once(
                f"claim{answer.status_code}", "The feed did not take the portfolio's keys."
            )
        return False

    async def drop(self) -> None:
        """Release the set (a stopped service has no use for ticks)."""
        if self.claimed:
            await self.feed.call("DELETE", f"/claims/{CLIENT}")
            self.claimed = frozenset()


#: Waits between tries at releasing the claims while the portfolio is off; then it gives up (the
#: feed lets a claim lapse by itself after 7 days).
RELEASE_DELAYS_S = (0.0, 5.0, 15.0, 30.0, 60.0, 60.0, 60.0, 60.0)


async def release(
    feed_url: str,
    delays: Iterable[float] = RELEASE_DELAYS_S,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> bool:
    """Drop this service's claims at the feed, trying again while the feed does not answer.
    True when the feed has no set for this client any more. Used when the portfolio is switched
    off; a plain stop or restart keeps its claims."""
    feed = FeedClient(feed_url)
    try:
        for wait in delays:
            if wait:
                await sleep(wait)
            answer = await feed.call("DELETE", f"/claims/{CLIENT}")
            if answer is not None and answer.status_code in (200, 204, 404):
                return True
        log.warning("The portfolio is off but the feed still holds its price subscriptions.")
        return False
    finally:
        await feed.aclose()


def register_job() -> None:
    """The job "claims": keep the set at the feed current while the service runs, so ticks are
    there at the close. The entry point calls this once."""
    from .service import job

    @job("claims")
    async def claims_job(service) -> None:
        prices = service.ensure_prices()
        while True:
            try:
                await prices.follow(service.store, service.clock(), service.upstox)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the portfolio's keys could not be claimed at the feed")
            await asyncio.sleep(CHECK_EVERY_S)
