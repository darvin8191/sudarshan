"""SUDARSHAN's REST governor (M18.9): the ring services' Upstox market-data GETs, on one share.

    GET http://127.0.0.1:5580/upstox/<Upstox path>?<query>
        X-Sudarshan-Client:   sudarshan-screener | sudarshan-portfolio | sudarshan-alerts
        X-Sudarshan-Priority: repair | interactive | bulk

The feed holds the Analytics Token and re-reads it when its file changes; the other services
reach Upstox's market data only through here, so the buckets live in one place. Cross-design
rules G1-G8 (27 Oct 2026):

- G2: only the market-data GETs in ROUTES pass. Each path is matched segment by segment, its
  instrument keys checked, and the call goes to Upstox in one canonical spelling. Anything else is
  403: account reads stay in the portfolio service, out of reach of any other local process.
- G3: per Upstox API (Upstox allows 50/s, 500/min and 2,000 per 30 min per API per user),
  SUDARSHAN takes at most 40% (TOTAL) and each client at most its own ceiling (CEILINGS); the
  other 60% stays for OpenAlgo's daily token and TRINETRA. The windows slide: no second, minute
  or half hour ever holds more than the share.
- G4: repair first, then interactive, then bulk, first come first served within a class. Bulk
  (warm starts, backfills, the 16:10 option chains) is refused at once from 08:30 to 16:00 IST on
  a trading day, and around a special day's own window with the same margins. A request waits at
  most 30 s, at most 64 wait and 4 are in flight to Upstox; a request that cannot go within its
  30 s is told so at once, with Retry-After. When 64 wait, the last of them in that order is the
  one told it is busy, which is the newcomer unless it outranks it.
- G5: a 429 or UDAPI10005 stops that API for every client for 60 s, then halves its rates for 10
  minutes; a 401 stops every API until the token file changes. Nothing is retried here. Upstox's
  status and body come back as bytes, never parsed (with its Content-Type and Content-Encoding);
  a body over 8 MiB is 502.
- G6: every answer carries X-Sudarshan-Waited-Ms. The governor's own answers are JSON
  {"error": sentence} with X-Sudarshan-Reason and, for a 503, Retry-After; the sentences are
  written for the owner, who may read them on a screen, so a caller's mistake reads as a fault in
  SUDARSHAN and its detail goes to the DEBUG log (L10). /health shows calls, waits, refusals and
  429s per API and client (instants in epoch ms, cross-design X3), never a URL, a query or the
  token; $STATE_DIR/rest_counts.json keeps
  them across restarts, so a week of 429s can be read (whether TRINETRA's own calls meet ours).
- M2 (review, 28 Oct): the same file keeps every send of the last 30 minutes and any stop or half
  rate, so a restart (a deploy, or Restart=always after a crash) never grants a share twice.

It shares the feed's event loop with the tick path, so it must never hold it: per request it
does a few comparisons, and Upstox's bodies are copied, never decoded or decompressed.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import math
import re
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from itertools import chain
from typing import NamedTuple
from urllib.parse import SplitResult, parse_qsl, quote, unquote

import httpx

from .store import atomic_write_json
from .subscriptions import KEY_RE
from .window import IST, MarketWindow, iso

log = logging.getLogger("sudarshan.feed.rest")

PREFIX = "/upstox"
CLIENT_HEADER, PRIORITY_HEADER = "x-sudarshan-client", "x-sudarshan-priority"
WINDOWS_S = (1.0, 60.0, 1800.0)  # Upstox counts per second, per minute and per 30 minutes
TOTAL = (20, 200, 800)  # G3: 40% of Upstox's 50 / 500 / 2,000 for each API
CEILINGS = {
    "sudarshan-screener": (15, 150, 600),  # 30%
    "sudarshan-portfolio": (2, 25, 100),  # 5%
    "sudarshan-alerts": (2, 25, 100),  # 5%
}
PRIORITIES = ("repair", "interactive", "bulk")  # G4: served in this order
BULK = PRIORITIES.index("bulk")
MAX_WAITING, MAX_IN_FLIGHT, WAIT_CAP_S = 64, 4, 30.0
FREEZE_S, HALF_RATE_S = 60.0, 600.0  # G5: after a 429 or UDAPI10005
MARKET_HOURS = (8 * 60 + 30, 16 * 60)  # G4: no bulk 08:30-16:00 IST on a trading day
MAX_BODY = 8 << 20
UPSTREAM_TIMEOUT = httpx.Timeout(25.0, connect=5.0)  # each phase of a call to Upstox
UPSTREAM_TOTAL_S = 30.0  # the whole call, body included
RETRY_S = 60  # Retry-After when nothing better is known
COUNTS_FILE, STATE_VERSION = "rest_counts.json", 2  # G6, M2: what outlives a restart
SAVE_AFTER_S = 1.0  # a change is on disk within a second (a stop at once)
MAX_KEYS = 500  # Upstox's quote calls take at most 500 instrument keys
RATE_LIMITED = re.compile(rb"UDAPI10005(?![0-9])")  # never UDAPI100050, the token error
COUNTS = ("calls", "waits", "refusals", "429s")

BULK_WAITS = "History downloads wait for the market to close (after {end})."
BUSY = "SUDARSHAN's share of Upstox requests is in use; try again in a minute."
SLOWED = "Upstox asked SUDARSHAN to slow down; try again in a minute."
TOKEN_REFUSED = (
    "Upstox refused SUDARSHAN's Analytics Token. Put the current token in the server's token "
    "file; requests start again as soon as it changes."
)
NO_TOKEN = (
    "SUDARSHAN has no usable Analytics Token on the server. Put the current token in the "
    "server's token file; requests start again as soon as it is there."
)
SWITCHED_OFF = "Upstox requests through the price feed are switched off on the server."
RESTARTING = "SUDARSHAN's price feed is restarting; try again in a minute."
TOO_LARGE = (
    "Upstox's answer was larger than SUDARSHAN takes at once; ask for a shorter period or "
    "fewer instruments."
)
NO_ANSWER = "Upstox could not be reached or did not answer in time; try again in a minute."
# L10: a caller's mistake is SUDARSHAN's own; the owner has nothing to change. What was wrong
# goes to the DEBUG log (Refused.detail), never into the sentence.
FAULT = "This is a fault in SUDARSHAN itself; nothing in your settings needs changing."
NOT_ALLOWED = (
    f"The price feed passes only market-data requests to Upstox, and this was not one. {FAULT}"
)
UNKNOWN_CLIENT = f"The price feed did not recognise which part of SUDARSHAN was asking. {FAULT}"
BAD_REQUEST = f"The price feed could not read this request for Upstox. {FAULT}"


class Refused(Exception):
    """The governor's own answer: HTTP status, X-Sudarshan-Reason, sentence, Retry-After, and
    for a caller's mistake what was wrong (the DEBUG log's, never the answer's)."""

    def __init__(
        self, status: int, reason: str, sentence: str, retry_after: float | None = None,
        detail: str | None = None,
    ):  # fmt: skip
        super().__init__(sentence)
        self.status, self.reason, self.retry_after = status, reason, retry_after
        self.detail = detail


# ---- G2: what passes, in one canonical spelling ------------------------------------------------
def _keys(most: int) -> Callable[[str], bool]:
    def check(value: str) -> bool:
        keys = value.split(",")
        return len(keys) <= most and all(KEY_RE.fullmatch(key) for key in keys)

    return check


class Route(NamedTuple):
    """One Upstox API (Upstox limits each on its own): a path pattern and its query, every
    parameter required and nothing else accepted."""

    api: str
    path: str
    params: tuple[tuple[str, Callable[[str], object]], ...] = ()


_KEY, _N, _DAY = "(?P<key>[^/]+)", "[1-9][0-9]{0,2}", "[0-9]{4}-[0-9]{2}-[0-9]{2}"
_ANY_KEYS, _ONE_KEY = _keys(MAX_KEYS), _keys(1)
_INTERVAL = re.compile(r"1d|I1|I30").fullmatch
# Upstox's option-chain page: "Accepts either a date in YYYY-MM-DD format or a relative expiry
# keyword" (current_week, next_week, far_week, current_month, next_month, far_month).
_EXPIRY = re.compile(rf"{_DAY}|(?:current|next|far)_(?:week|month)").fullmatch
#: G2's allow-list, each line one page under upstox.com/developer/api-documentation/:
#: v3/get-intra-day-candle-data, v3/get-historical-candle-data, get-full-market-quote-v3,
#: ltp-v3, get-market-quote-ohlc-v3 and get-pc-option-chain.
ROUTES = (
    Route("intraday-candle", rf"/v3/historical-candle/intraday/{_KEY}/(?:minutes|hours|days)/{_N}"),
    Route("historical-candle", rf"/v3/historical-candle/{_KEY}/(?:minutes|hours|days|weeks|months)"
                               rf"/{_N}/{_DAY}(?:/{_DAY})?"),
    Route("full-quote", "/v3/market-quote/quotes", (("instrument_key", _ANY_KEYS),)),
    Route("ltp", "/v3/market-quote/ltp", (("instrument_key", _ANY_KEYS),)),
    Route("ohlc", "/v3/market-quote/ohlc", (("instrument_key", _ANY_KEYS),
                                            ("interval", _INTERVAL))),
    Route("option-chain", "/v2/option/chain", (("instrument_key", _ONE_KEY),
                                               ("expiry_date", _EXPIRY))),
)  # fmt: skip


def resolve(path: str, query: str) -> tuple[str, str]:
    """(API, Upstox path and query as sent) for an allowed call; Refused for anything else."""
    for route in ROUTES:
        if match := re.fullmatch(route.path, path):
            break
    else:
        raise Refused(403, "not-allowed", NOT_ALLOWED, detail="not a market-data read on the list")
    if "key" in match.re.groupindex:  # one segment, sent percent-encoded whatever came in
        key = unquote(match["key"])
        if not KEY_RE.fullmatch(key) or "/" in key or "\\" in key:
            raise Refused(400, "bad-request", BAD_REQUEST, detail="the path's key is no key")
        path = path[: match.start("key")] + quote(key, safe="") + path[match.end("key") :]
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True) if query else []
    except ValueError:
        raise Refused(400, "bad-request", BAD_REQUEST, detail="query not well formed") from None
    given, names = dict(pairs), [name for name, _ in route.params]
    if len(given) != len(pairs) or sorted(given) != sorted(names):
        wanted = ", ".join(names) or "no query"
        raise Refused(
            400, "bad-request", BAD_REQUEST, detail=f"{route.api} takes exactly: {wanted}"
        )
    for name, valid in route.params:
        if not valid(given[name]):
            raise Refused(400, "bad-request", BAD_REQUEST, detail=f"{route.api}: {name} not valid")
    if names:
        path += "?" + "&".join(f"{name}={quote(given[name], safe='')}" for name in names)
    return route.api, path


# ---- G3: the windows ----------------------------------------------------------------------------
def delay(sent: deque[float], limits: tuple[int, ...], now: float, factor: float = 1.0) -> float:
    """Seconds until one more call fits every window (<= 0: it fits now).

    ``sent`` holds the monotonic send times, oldest first; times no window can see any more are
    dropped here, so it never holds more than the longest window's limit.
    """
    while sent and sent[0] <= now - WINDOWS_S[-1]:
        sent.popleft()
    wait = 0.0
    for window, limit in zip(WINDOWS_S, limits, strict=True):
        allowed = max(1, int(limit * factor))
        if len(sent) >= allowed:
            wait = max(wait, sent[-allowed] + window - now)
    return wait


class MarketHours(MarketWindow):
    """G4's bulk hours: 08:30-16:00 IST on a trading day, moved onto a special day's own window
    with the same margins (13:35-16:00 for Muhurat 14:05-15:40) and added to the usual hours on a
    weekday that trades anyway. It is the feed's own day plan (window.py) with other margins, on
    the feed's calendar object, so each calendar file is still read once."""

    def __init__(self, calendar):
        self.calendar, (self.start, self.end) = calendar, MARKET_HOURS

    def end_of(self, now: float) -> float | None:
        """When the market hours ``now`` falls in end (epoch seconds); None outside them."""
        at = datetime.fromtimestamp(now, IST)
        minute = at.hour * 60 + at.minute
        for start, end in self.plan(at.date()).spans:
            if start <= minute < end:
                return datetime.combine(at.date(), dtime(0), IST).timestamp() + end * 60
        return None


class _Api:
    """One Upstox API: its sends (all clients, and each client), freeze and counters."""

    def __init__(self):
        self.sent: deque[float] = deque()  # monotonic send times, oldest first
        self.by_client = {client: deque() for client in CEILINGS}
        self.frozen_until = self.half_until = 0.0  # monotonic
        self.counts = {client: dict.fromkeys(COUNTS, 0) for client in CEILINGS}


@dataclass(eq=False)
class _Ticket:
    order: tuple[int, int]  # (priority, arrival): G4's order of service
    api: str
    client: str
    bulk: bool
    deadline: float  # monotonic: the 30 s cap
    future: asyncio.Future  # result None: go now; a Refused: the answer
    waited: bool = False
    slot: bool = False  # holds one of the places in flight


class _TooLarge(Exception):
    pass


async def _body(resp: httpx.Response) -> bytes:
    """Upstox's body as it came (never decoded or decompressed), at most MAX_BODY bytes."""
    declared = resp.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > MAX_BODY:
        raise _TooLarge
    chunks, size = [], 0
    async for chunk in resp.aiter_raw():
        size += len(chunk)
        if size > MAX_BODY:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def _bulk_refusal(end: float | None, now: float) -> Refused | None:
    """G4: bulk inside market hours is answered at once, never held; Retry-After their end."""
    if end is None:
        return None
    at = datetime.fromtimestamp(end, IST).strftime("%H:%M")
    return Refused(503, "bulk-hours", BULK_WAITS.format(end=at), end - now)


class RestGovernor:
    """Lives in the FeedService and uses its settings, calendar, HTTP client, token file reader,
    scrubber, wall clock and log throttle; the service passes itself."""

    def __init__(self, service):
        self.service = service
        self.enabled = service.cfg.rest
        self.hours = MarketHours(service.window.calendar)
        self.apis = {route.api: _Api() for route in ROUTES}
        self._waiting: list[_Ticket] = []  # in G4's order
        self._arrivals = self._in_flight = self._not_allowed = 0
        self._timer: asyncio.TimerHandle | None = None
        self._closing = False
        self._token: tuple[int | None, str | None] = (-1, None)  # (file stamp, token) last read
        self._refused_stamp: int | None = None  # the token file's stamp Upstox answered 401 for
        self._refused_since: float | None = None
        self._file = service.cfg.state_dir / COUNTS_FILE
        self._save: asyncio.TimerHandle | None = None
        # L5: the disk work of a save runs here, one save at a time, never on the loop.
        self._saver = ThreadPoolExecutor(1, thread_name_prefix="sudarshan-rest-save")
        self._writing: Future | None = None
        self._keep = False  # something to write at the stop
        self.since = self._load()  # epoch s; counting, the windows and a stop outlive a restart

    # ---- one request ---------------------------------------------------------------------------
    async def serve(self, method: str, url: SplitResult, headers: dict[str, str], respond) -> None:
        """Answer one request: ``await respond(status, body bytes or the governor's JSON payload,
        headers)`` hands the answer to its caller. M3: a place in flight is held until respond
        returns (the caller has the answer, or the HTTP API's 30 s to hand it over ran out), so
        at most MAX_IN_FLIGHT answers of at most MAX_BODY wait in the feed at once."""
        start, ticket, api, waited = time.monotonic(), None, None, None
        client = headers.get(CLIENT_HEADER, "").strip()
        try:
            try:
                api, target, rank = self._check(method, url, client, headers)
                self._refuse_at_once()
                ticket = self._enter(api, client, rank, start)  # bulk in market hours: refused
                await self._turn(ticket)
                waited = _ms(time.monotonic() - start)
                answer = await self._call(ticket, target, waited)
            except Refused as refused:
                answer = self._refusal(refused, api, client, _ms(time.monotonic() - start), waited)
            await respond(*answer)
        finally:
            if ticket is not None:
                self._leave(ticket)

    def _check(self, method: str, url: SplitResult, client: str, headers) -> tuple[str, str, int]:
        if client not in CEILINGS:
            detail = "X-Sudarshan-Client is none of " + ", ".join(CEILINGS)
            raise Refused(403, "client", UNKNOWN_CLIENT, detail=detail)
        if method != "GET":
            raise Refused(403, "not-allowed", NOT_ALLOWED, detail="a method other than GET")
        priority = headers.get(PRIORITY_HEADER, "").strip().lower()
        if priority not in PRIORITIES:
            detail = "X-Sudarshan-Priority is none of " + ", ".join(PRIORITIES)
            raise Refused(400, "bad-request", BAD_REQUEST, detail=detail)
        api, target = resolve(url.path[len(PREFIX) :], url.query)
        return api, target, PRIORITIES.index(priority)

    def _refuse_at_once(self) -> None:
        if not self.enabled:
            raise Refused(503, "off", SWITCHED_OFF, RETRY_S)
        if self._closing or self.service._client is None:
            raise Refused(503, "restarting", RESTARTING, RETRY_S)
        self._token_now()

    def _token_now(self) -> tuple[str, int]:
        """The token to send and its file's stamp; Refused while Upstox refuses it (G5)."""
        stamp = self.service._token_mtime()
        if self._refused_stamp is not None:
            if stamp == self._refused_stamp:
                raise Refused(503, "token", TOKEN_REFUSED, RETRY_S)
            log.info("the token file changed: Upstox requests through the governor resume")
            self._refused_stamp = self._refused_since = None
        if stamp != self._token[0]:
            self._token = (stamp, self.service._read_token() if stamp is not None else None)
        if self._token[1] is None:
            raise Refused(503, "token", NO_TOKEN, RETRY_S)
        return self._token[1], stamp

    # ---- G4: the queue -------------------------------------------------------------------------
    def _enter(self, api: str, client: str, rank: int, start: float) -> _Ticket:
        self._arrivals += 1
        future = asyncio.get_running_loop().create_future()
        ticket = _Ticket(
            (rank, self._arrivals), api, client, rank == BULK, start + WAIT_CAP_S, future
        )
        bisect.insort(self._waiting, ticket, key=lambda t: t.order)
        self._pump()
        if len(self._waiting) > MAX_WAITING:  # M4: the last in G4's order gives way, so a full
            last = self._waiting.pop()  # queue of bulk calls never turns away a repair
            last.future.set_result(Refused(503, "busy", BUSY, RETRY_S))
        return ticket

    async def _turn(self, ticket: _Ticket) -> None:
        if not ticket.future.done():
            ticket.waited = True
            await asyncio.wait((ticket.future,), timeout=ticket.deadline - time.monotonic())
            if not ticket.future.done():  # its 30 s are up
                raise Refused(503, "busy", BUSY, RETRY_S)
        if (refused := ticket.future.result()) is not None:
            raise refused

    def _wait(self, ticket: _Ticket, now: float) -> float:
        api = self.apis[ticket.api]
        if now < api.frozen_until:
            return api.frozen_until - now
        factor = 0.5 if now < api.half_until else 1.0
        return max(
            delay(api.sent, TOTAL, now, factor),
            delay(api.by_client[ticket.client], CEILINGS[ticket.client], now, factor),
        )

    def _pump(self) -> None:
        """Hand free places to waiting requests in G4's order. A request that cannot go within
        its 30 s (a frozen API, full windows, bulk hours begun) is answered now, not at 30 s."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        now, soonest, wall = time.monotonic(), math.inf, None
        for ticket in list(self._waiting):
            if ticket.future.done():  # gave up waiting
                self._waiting.remove(ticket)
                continue
            refused, wait = None, self._wait(ticket, now)
            if ticket.bulk:
                if wall is None:  # one calendar read per pump
                    wall = self.service.clock()
                    bulk_end = self.hours.end_of(wall)
                refused = _bulk_refusal(bulk_end, wall)
            if refused is None and now + wait > ticket.deadline:
                if now < self.apis[ticket.api].frozen_until:
                    refused = Refused(503, "rate-limited", SLOWED, wait)
                else:
                    refused = Refused(503, "busy", BUSY, wait)
            if refused is not None:
                self._waiting.remove(ticket)
                ticket.future.set_result(refused)
            elif wait > 0:
                soonest = min(soonest, wait)
            elif self._in_flight < MAX_IN_FLIGHT:
                self._waiting.remove(ticket)
                api = self.apis[ticket.api]
                api.sent.append(now)
                api.by_client[ticket.client].append(now)
                self._dirty()  # M2: a send is kept on disk, so a restart cannot grant it again
                self._in_flight, ticket.slot = self._in_flight + 1, True
                ticket.future.set_result(None)
        if soonest < math.inf and self._in_flight < MAX_IN_FLIGHT:
            self._timer = asyncio.get_running_loop().call_later(soonest, self._pump)

    def _leave(self, ticket: _Ticket) -> None:
        if not ticket.future.done():
            ticket.future.cancel()
        if ticket in self._waiting:
            self._waiting.remove(ticket)
        if ticket.waited:
            self._count(ticket.api, ticket.client, "waits")
        if ticket.slot:
            ticket.slot, self._in_flight = False, self._in_flight - 1
            self._pump()

    # ---- the call ------------------------------------------------------------------------------
    async def _call(self, ticket: _Ticket, target: str, waited: int):
        token, stamp = self._token_now()  # read as the call goes: a new token is used at once
        self._count(ticket.api, ticket.client, "calls")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Accept-Encoding": "identity",  # bytes to copy, never to inflate on this loop
        }
        url, http = self.service.cfg.api_base + target, self.service._client
        try:
            async with asyncio.timeout(UPSTREAM_TOTAL_S):
                async with http.stream("GET", url, headers=headers, timeout=UPSTREAM_TIMEOUT) as r:
                    status, body, kept = r.status_code, await _body(r), r.headers
        except _TooLarge:
            log.warning("REST: Upstox's answer on %s was over %d bytes", ticket.api, MAX_BODY)
            raise Refused(502, "too-large", TOO_LARGE) from None
        except (TimeoutError, httpx.HTTPError) as exc:
            what = self.service._scrub(f"{type(exc).__name__}: {exc}")
            # L3: one throttle per API, so one API's failures never hide another's for 5 minutes
            self.service._throttle(
                logging.WARNING, f"rest-{ticket.api}", f"REST: {ticket.api} failed: {what}"
            )
            timed_out = isinstance(exc, TimeoutError | httpx.TimeoutException)
            raise Refused(504 if timed_out else 502, "no-answer", NO_ANSWER) from None
        if status == 401:
            if stamp == self.service._token_mtime():
                self._token_refused(stamp, ticket.api)
            else:  # L1: refused the token the file held before; the one there now is untried
                log.info(
                    "REST: a 401 on %s for the token the file held before is ignored", ticket.api
                )
        elif status == 429 or (status >= 400 and RATE_LIMITED.search(body)):
            self._freeze(ticket)
        log.debug("REST %s %s: %d, %d bytes, waited %d ms",
                  ticket.client, ticket.api, status, len(body), waited)  # fmt: skip
        out = {"Content-Type": kept.get("Content-Type") or "application/octet-stream"}
        if encoding := kept.get("Content-Encoding"):
            out["Content-Encoding"] = encoding
        return status, body, out | {"X-Sudarshan-Waited-Ms": str(waited)}

    def _freeze(self, ticket: _Ticket) -> None:
        api, now = self.apis[ticket.api], time.monotonic()
        self._count(ticket.api, ticket.client, "429s")
        api.frozen_until, api.half_until = now + FREEZE_S, now + FREEZE_S + HALF_RATE_S
        log.warning(
            "REST: Upstox asked to slow down on %s (a call from %s): %s paused for every client "
            "for %.0f s, then at half rate for %.0f min",
            ticket.api, ticket.client, ticket.api, FREEZE_S, HALF_RATE_S / 60,
        )  # fmt: skip
        self.save()  # M2: at once, so even a crash in the next second keeps the stop
        self._pump()  # the waiting calls of that API that cannot make it are told now

    def _token_refused(self, stamp: int, api: str) -> None:
        if self._refused_stamp != stamp:
            log.warning(
                "REST: Upstox refused the Analytics Token (401 on %s): every Upstox request "
                "through the governor stops until the token file changes", api,
            )  # fmt: skip
            self._refused_stamp, self._refused_since = stamp, self.service.clock()
        for ticket in [t for t in self._waiting if not t.future.done()]:
            self._waiting.remove(ticket)
            ticket.future.set_result(Refused(503, "token", TOKEN_REFUSED, RETRY_S))

    def _refusal(self, refused: Refused, api, client: str, elapsed: int, waited: int | None):
        if api is None:
            self._not_allowed += 1
        elif refused.status == 503:
            self._count(api, client, "refusals")
        # Waited: until its turn came, or until it was refused while waiting for one.
        headers = {"X-Sudarshan-Reason": refused.reason,
                   "X-Sudarshan-Waited-Ms": str(elapsed if waited is None else waited)}  # fmt: skip
        if refused.retry_after is not None:
            headers["Retry-After"] = str(max(1, math.ceil(refused.retry_after)))
        log.debug("REST %s %s: %d %s%s", client or "-", api or "-", refused.status, refused.reason,
                  f" ({refused.detail})" if refused.detail else "")  # fmt: skip
        return refused.status, {"error": str(refused)}, headers

    # ---- service hooks -------------------------------------------------------------------------
    def close(self) -> None:
        """The feed is stopping: waiting requests are answered now, never dropped, and what the
        next start needs is written last, marked as a clean stop (M2)."""
        self._closing = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for ticket in self._waiting:
            if not ticket.future.done():
                ticket.future.set_result(Refused(503, "restarting", RESTARTING, RETRY_S))
        self._waiting.clear()
        if self._save is not None:
            self._save.cancel()
            self._save = None
        self._saver.shutdown(wait=True)  # a save already handed over lands first
        if self._keep:
            self._write(self._state(clean=True))  # on the loop: it is stopping, no tick waits

    # ---- G6 and M2: what outlives a restart ----------------------------------------------------
    def _count(self, api: str, client: str, key: str) -> None:
        self.apis[api].counts[client][key] += 1
        self._dirty()

    def _dirty(self) -> None:
        """A count, a send or a stop changed: it reaches the disk within SAVE_AFTER_S (at once
        once the feed is stopping)."""
        self._keep = True
        if self._save is None:
            wait = 0.0 if self._closing else SAVE_AFTER_S
            self._save = asyncio.get_running_loop().call_later(wait, self.save)

    def save(self) -> None:
        """Hand a copy of the state to the saver thread (L5): the JSON, the write and the fsync
        happen there. One save at a time: while the disk is slow the next copy waits a second
        instead of queueing. After the stop's own save (a call still in flight when the feed
        stops can still meet a 429), it is written here and now: the loop is stopping."""
        if self._save is not None:
            self._save.cancel()
            self._save = None
        if self._closing:
            self._write(self._state(clean=True))
            return
        if self._writing is not None and not self._writing.done():
            self._dirty()
            return
        self._writing = self._saver.submit(self._write, self._state(clean=False))

    def _state(self, clean: bool) -> tuple:
        """What the next start needs, as plain copies, with both clocks to place it in time."""
        apis = {
            name: ({client: list(sent) for client, sent in api.by_client.items()},
                   api.frozen_until, api.half_until,
                   {client: dict(n) for client, n in api.counts.items() if any(n.values())})
            for name, api in self.apis.items()
        }  # fmt: skip
        return time.monotonic(), self.service.clock(), self.since, clean, apis

    def _write(self, state: tuple) -> None:
        """rest_counts.json, atomically: the counters, every send of the last 30 minutes per API
        and client, and any stop or half rate, as epoch ms (the monotonic clock ends with the
        process). ``clean`` is true only in the last save of a stop."""
        now, wall, since, clean, apis = state
        horizon = now - WINDOWS_S[-1]

        def ms(t: float) -> int:
            return round((wall + t - now) * 1000)

        payload = {
            "version": STATE_VERSION,
            "since_ms": round(since * 1000),
            "saved_ms": round(wall * 1000),
            "clean": clean,
            "counts": {name: counts for name, (_, _, _, counts) in apis.items() if counts},
            "apis": {
                name: {
                    "sent_ms": {client: [ms(t) for t in times if t > horizon]
                                for client, times in sent.items() if times},
                    "frozen_until_ms": ms(frozen) if frozen > now else None,
                    "half_rate_until_ms": ms(half) if half > now else None,
                }
                for name, (sent, frozen, half, _) in apis.items()
            },
        }  # fmt: skip
        try:
            atomic_write_json(self._file, payload)
        except OSError as exc:
            log.warning("REST counters and windows not saved to %s: %s", self._file, exc)

    def _load(self) -> float:
        """Since when counting runs (epoch s), with the last run's counters, windows and any stop
        put back (M2). A run that ended without the save of its stop (a crash or a kill) may have
        sent in the second it had not saved yet, so that second counts as used in full: each
        client's per-second share is taken as sent now, on every API."""
        now, wall = time.monotonic(), self.service.clock()
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            if data["version"] != STATE_VERSION:
                raise ValueError(f"format {data['version']!r}")
            since, saved = float(data["since_ms"]) / 1000, float(data["saved_ms"]) / 1000
            clean = data["clean"] is True
            counts = [
                (self.apis[api].counts[client], {key: int(n.get(key, 0)) for key in COUNTS})
                for api, clients in data["counts"].items() if api in self.apis
                for client, n in clients.items() if client in CEILINGS
            ]  # fmt: skip
            kept = [
                (self.apis[name],
                 {client: [now + float(t) / 1000 - wall for t in times]
                  for client, times in entry["sent_ms"].items() if client in CEILINGS},
                 [0.0 if entry[key] is None else now + float(entry[key]) / 1000 - wall
                  for key in ("frozen_until_ms", "half_rate_until_ms")])
                for name, entry in data["apis"].items() if name in self.apis
            ]  # fmt: skip
        except FileNotFoundError:
            return wall
        except (OSError, ValueError, LookupError, TypeError, AttributeError) as exc:
            log.warning("REST counters in %s cannot be read (%r): counting afresh", self._file, exc)
            return wall
        self._keep = True
        for numbers, loaded in counts:
            numbers.update(loaded)
        horizon = now - WINDOWS_S[-1]
        for api, sent, (frozen, half) in kept:
            for client, times in sent.items():
                api.by_client[client].extend(t for t in times if t > horizon)
            api.frozen_until, api.half_until = frozen, half
        if not clean:
            log.warning(
                "REST: the last run ended without saving its stop (after %s): its unsaved second "
                "counts as every client's whole per-second share, on every API", iso(saved),
            )  # fmt: skip
            for api in self.apis.values():
                for client, limits in CEILINGS.items():
                    api.by_client[client].extend([now] * (limits[0] * math.ceil(SAVE_AFTER_S)))
        for api in self.apis.values():  # the windows read their sends oldest first
            for client, sent in api.by_client.items():
                api.by_client[client] = deque(sorted(sent))
            api.sent = deque(sorted(chain.from_iterable(api.by_client.values())))
        return since

    def health(self) -> dict:
        """G6: per API and client, never a URL, a query or the token; instants in epoch ms (X3)."""
        now, wall = time.monotonic(), self.service.clock()

        def ms(at: float | None) -> int | None:
            return None if at is None else round(at * 1000)

        def until(t: float) -> int | None:
            return ms(wall + t - now) if t > now else None

        apis = {}
        for name, api in self.apis.items():
            totals = {key: sum(count[key] for count in api.counts.values()) for key in COUNTS}
            clients = {c: dict(n) for c, n in api.counts.items() if any(n.values())}
            apis[name] = totals | {
                "frozen_until_ms": until(api.frozen_until),
                "half_rate_until_ms": until(api.half_until),
                "clients": clients,
            }
        return {
            "enabled": self.enabled,
            "counted_since_ms": ms(self.since),  # counts run on across restarts (rest_counts.json)
            "in_flight": self._in_flight,
            "waiting": len(self._waiting),
            "bulk_refused_until_ms": ms(self.hours.end_of(wall)),
            "token_refused_since_ms": ms(self._refused_since),
            "not_allowed": self._not_allowed,
            "apis": apis,
        }
