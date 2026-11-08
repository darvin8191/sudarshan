"""Runtime 1 (M9.1, design 2.3, 2.5, S14): the feed link, claims, live 1-minute bars, pauses, and
the ``Source`` the run loop judges from.

``LiveSource`` is registered as ``service.parts["source"]`` (the runner) and ``["engine"]`` (the
engine line). Its job (``run_feed``) does, in one asyncio task:

- at start and at each new day: today's sessions from the calendar (X11), the universes the
  active screens use from the master contract (universe.py, kept in the store), the keys to
  follow (at most ``max_keys``, X10) claimed at the feed in ``full`` mode (a stock that closes by
  auction needs ``vtt`` for its print, X6n), and today's sealed bars restored from the store;
- the feed's frames: ``__health__`` becomes ``clock.Feed`` and the exchange's status, a key's
  frame goes to ``bars.Bars.on_tick`` and is kept as the key's last frame (``ltp``, ``cp``);
  every second ``on_clock`` seals the minutes whose grace is over; sealed bars are written to the
  store on its thread, a batch a tick;
- pauses as the market alerts define them (S14): the feed not heard for 12 s, not connected or
  not live, or silence of the whole socket for over 10 s, inside a session, opens a pause; it is
  closed when the feed is back. The pauses are what ``candles.read`` marks UNVERIFIED with.

What it does not do yet: history before today (SV3, the REST governor) and the daily stores
(SV4); a plan that needs more candles than today holds is judged "warming up" by the engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

import httpx

from sudarshan.alerts.market import feedlink as alerts_feedlink
from sudarshan.alerts.market.clock import GAP_S, HEALTH_MAX_AGE_MS, Feed, Market, Pause
from sudarshan.alerts.market.feedlink import (
    HEALTH,
    FeedRefused,
    FeedUnreachable,
    Health,
    parse_health,
)
from sudarshan.alerts.market.grain import paise
from sudarshan.alerts.market.sessions import day_key, previous_trading_day, sessions_of
from sudarshan.autologin.schedule import IST

from . import candles, universe
from .bars import Bar, Bars, Sealed
from .engine import Context, Subject
from .runs import Member
from .store import OWNER, StoreError, Tx
from .types import DAY, MINUTE_TFS, Plan, Series

log = logging.getLogger("sudarshan.screener")

CLIENT = "sudarshan-screener"
TICK_S = 1.0
CLAIM_RENEW_S = 60.0
#: Universes rebuilt from the master at start and after this moment of the day (the daily
#: download has landed by then), every day.
MASTER_AT_MIN = 9 * 60


class FeedLink(alerts_feedlink.FeedLink):
    """The alerts' feed link with this service's client id on its claims."""

    async def put_claims(
        self, keys: dict[str, str], timeout: float = alerts_feedlink.HTTP_TIMEOUT_S
    ) -> dict:
        return await self._call("PUT", f"/claims/{CLIENT}", {"keys": keys}, timeout=timeout)

    async def drop_claims(self) -> dict:
        return await self._call("DELETE", f"/claims/{CLIENT}")


def universe_of(
    kind_spec: Mapping[str, Any],
    master: universe.Master,
    today: date,
    *,
    lists_dir=None,
    extra: Mapping[str, Any] | None = None,
) -> tuple[list[Member], dict[str, universe.Listing], list[str]]:
    """(members, listings by key, names missing) of one universe spec from the master."""
    kind = kind_spec["kind"]
    cash = master.cash("NSE")
    futures = master.rows("NFO", ["FUT"])
    fno = {row.underlying for row in futures if row.underlying}
    listings = {row.key: row for row in cash.values()} | {row.key: row for row in futures}
    missing: list[str] = []
    if kind == "fno_stocks":
        members = universe.fno_stocks(cash, futures, today)
    elif kind == "fno_futures":
        members = universe.futures_of(futures, today, index=False)
    elif kind == "index_futures":
        members = universe.futures_of(futures, today, index=True)
    elif kind == "indices":
        rows = master.rows("NSE_INDEX")
        listings |= {row.key: row for row in rows}
        members = universe.indices(rows)
    elif kind == "index_list":
        names = universe.read_list(kind_spec["id"], lists_dir or universe.LISTS_DIR)
        members, missing = universe.index_list(names, cash, today, fno=fno)
    elif kind == "options":
        ltp = (extra or {}).get("ltp")
        rows = master.rows("NFO", ["CE", "PE"])
        listings |= {row.key: row for row in rows}
        members = universe.option_window(rows, kind_spec["underlying"], ltp, today) if ltp else []
    elif kind == "watchlist":
        items = (extra or {}).get("items") or []
        members, missing = universe.watchlist_members(items, {"NSE": cash}, today, fno=fno)
    else:
        members = []
    return members, listings, missing


class LiveSource:
    """runs.Source over the live bars, plus the engine line's facts and the feed job."""

    def __init__(self, store, feed: FeedLink, master: universe.Master, settings, clock, calendar):
        self.store, self.feed, self.master, self.settings = store, feed, master, settings
        self.clock, self.calendar = clock, calendar
        self.bars = Bars()
        self.market = Market(0, {})
        self._today: date | None = None
        self._universes: dict[str, list[Member]] = {}
        self._listings: dict[str, universe.Listing] = {}
        self._missing: dict[str, list[str]] = {}
        self._last: dict[str, Mapping[str, Any]] = {}
        self._health: Health | None = None
        self._down_since: int | None = None
        self._pauses: list[Pause] = []
        self._open_pause: int | None = None
        self.keys: frozenset[str] = frozenset()
        self.claimed_at: float = 0.0
        self.claim_words: str | None = None
        self.master_ready = False
        self.counts = {"frames": 0, "trades": 0, "sealed": 0, "late": 0}

    # ---- runs.Source --------------------------------------------------------------------------------
    def members(self, universe_spec: Mapping[str, Any]) -> Sequence[Member]:
        return self._universes.get(_uni_key(universe_spec), [])

    def subject(self, member: Member, plan: Plan, boundary: int) -> Subject | None:
        frame = self._last.get(member.key)
        if frame is None and not self.bars.bars(member.key):
            return None
        series: dict[Any, Series] = {}
        for tf in plan.need:
            if tf in MINUTE_TFS or tf == DAY:
                series[tf] = candles.read(self.bars, member.key, tf, self.market, boundary)
            else:
                series[tf] = Series.of(tf, [])  # weeks need history (SV3)
        cp = frame.get("cp") if frame else None
        own = self.market.sessions.get(member.segment)
        auction_end = own.end if (member.cas and own is not None) else None
        return Subject(
            member.symbol,
            series,
            cp=paise(cp),
            auction_end=auction_end,
            printed=self.bars.printed(member.key, self.market.today) if member.cas else False,
        )

    def context(self, boundary: int, session: int) -> Context:
        return Context(boundary, session, self.market.previous, opens=self._opens)

    def _opens(self, session: int) -> int | None:
        found = self.market.sessions.get("NSE_EQ")
        return found.start if found is not None and found.key == session else None

    def prices(self, member: Member) -> tuple[int | None, int | None]:
        frame = self._last.get(member.key)
        if not frame:
            return None, None
        return paise(frame.get("ltp")), paise(frame.get("cp"))

    def printed(self, member: Member) -> bool:
        return self.bars.printed(member.key, self.market.today)

    # ---- the engine line ------------------------------------------------------------------------------
    def engine_facts(self, now: int) -> dict[str, Any]:
        trading = any(s.start <= now < s.end for s in self.market.sessions.values())
        feed, since = self._feed_words(now)
        total = len(self.keys)
        without = sum(1 for key in self.keys if key not in self._last)
        return {
            "master_ready": self.master_ready or not trading,
            "trading": trading,
            "feed": feed,
            "feed_since": since,
            "loading": 0,
            "total": total,
            "without_prices": without,
        }

    def _feed_words(self, now: int) -> tuple[str, int | None]:
        health = self._health
        if health is None:
            return "silent", None
        if now - health.heard_at > HEALTH_MAX_AGE_MS:
            return "silent", health.heard_at
        if health.state in ("token_rejected", "no_token", "stopped"):
            return health.state, None
        if health.state != "connected" or not health.live:
            return "reconnecting", self._down_since
        if health.message_age_s is not None and health.message_age_s > GAP_S:
            return "reconnecting", self._down_since
        return "ok", None

    def status(self) -> dict[str, Any]:
        return {
            "keys": len(self.keys),
            "with_prices": sum(1 for k in self.keys if k in self._last),
            "universes": {name: len(members) for name, members in self._universes.items()},
            "missing": {name: len(names) for name, names in self._missing.items() if names},
            "feed": self._feed_words(self.clock())[0],
            "pauses": len(self._pauses) + (1 if self._open_pause is not None else 0),
            "claims": self.claim_words or "ok",
            **self.counts,
        }

    # ---- the day --------------------------------------------------------------------------------------
    def _rebuild_market(self, now: int) -> None:
        feed = (
            Feed(self._health.state, self._health.live, self._health.heard_at)
            if self._health
            else Feed()
        )
        pauses = tuple(self._pauses)
        if self._open_pause is not None:
            pauses += (Pause(self._open_pause, None, "feed"),)
        auction = frozenset(m.key for members in self._universes.values() for m in members if m.cas)
        self.market = Market(
            day_key(self._today) if self._today else 0,
            self.market.sessions,
            self.market.previous,
            self._health.status if self._health else {},
            feed,
            pauses,
            self.settings.replay,
            auction,
        )

    def new_day(self, now: int) -> bool:
        day = datetime.fromtimestamp(now / 1000, IST).date()
        if day == self._today:
            return False
        self._today = day
        windows = self.calendar.windows() if hasattr(self.calendar, "windows") else {}
        trading = self.calendar.is_trading_day(day)
        sessions = sessions_of(day, trading_day=trading, window=windows.get(day))
        previous = previous_trading_day(day, self.calendar.is_trading_day)
        self.market = Market(day_key(day), sessions, day_key(previous) if previous else 0)
        self._pauses, self._open_pause, self._last = [], None, {}
        self._rebuild_market(now)
        self._restore()
        return True

    def _restore(self) -> None:
        try:
            rows = self.store.read(OWNER, Tx.bars)
        except StoreError:
            return
        today = self.market.today
        kept = [
            Sealed(r["key"], Bar(r["start"], r["o"], r["h"], r["l"], r["c"], r["v"], r["oi"] if r["oi"] is not None else -1, r["atp"] or 0, r["flags"]), 0)
            for r in rows
        ]  # fmt: skip
        if kept:
            self.bars.restore(today, kept)
            log.info("restored %d sealed bars of today from the store", len(kept))

    def refresh_universes(self, specs: Sequence[Mapping[str, Any]], now: int) -> None:
        """The universes the active screens use, from the master; the keys to follow."""
        if self._today is None:
            return
        found: dict[str, list[Member]] = {}
        listings: dict[str, universe.Listing] = {}
        try:
            for kind_spec in specs:
                name = _uni_key(kind_spec)
                if name in found:
                    continue
                extra = {}
                if kind_spec["kind"] == "options":
                    frame = self._index_frame(kind_spec["underlying"])
                    extra["ltp"] = (
                        int(round(frame["ltp"] * 100)) if frame and frame.get("ltp") else None
                    )
                members, rows, missing = universe_of(
                    kind_spec, self.master, self._today, extra=extra
                )
                found[name], self._missing[name] = members, missing
                listings |= rows
                if missing:
                    log.warning(
                        "%d names of %s are not in the master contract: %s",
                        len(missing),
                        name,
                        ", ".join(missing[:5]),
                    )
            self.master_ready = True
        except (universe.MasterUnavailable, FileNotFoundError) as exc:
            log.warning("universes could not be built: %s", exc)
            self.master_ready = False
            return
        self._universes, self._listings = found, listings
        keys: list[str] = []
        seen: set[str] = set()
        for members in found.values():
            for member in members:
                if member.key not in seen:
                    seen.add(member.key)
                    keys.append(member.key)
        if len(keys) > self.settings.max_keys:
            log.warning(
                "the screens follow %d keys; only the first %d are claimed (the limit)",
                len(keys),
                self.settings.max_keys,
            )
            keys = keys[: self.settings.max_keys]
        self.keys = frozenset(keys)
        self.feed.follow(self.keys)
        self._rebuild_market(now)
        try:
            self.store.write(
                OWNER,
                lambda tx: [
                    tx.universe_replace(
                        name, name.split(":")[0], universe.member_rows(members, listings), now
                    )
                    for name, members in found.items()
                ],
            )
        except StoreError:
            log.exception("the universes could not be written to the store")

    def _index_frame(self, underlying: str) -> Mapping[str, Any] | None:
        for key, frame in self._last.items():
            if key.startswith("NSE_INDEX|") and underlying in key.upper().replace(" ", ""):
                return frame
        return None

    async def claim(self) -> None:
        if not self.keys:
            return
        try:
            await self.feed.put_claims(dict.fromkeys(sorted(self.keys), "full"))
            self.claim_words = None
        except FeedRefused as exc:
            self.claim_words = str(exc)
            log.error("the feed refused the screener's claims: %s", exc)
        except FeedUnreachable as exc:
            self.claim_words = "the feed is not answering"
            log.warning("the feed did not take the screener's claims: %s", exc)
        self.claimed_at = asyncio.get_running_loop().time()

    # ---- frames ---------------------------------------------------------------------------------------
    def on_frame(self, topic: str, payload: bytes, now: int) -> list[Sealed]:
        self.counts["frames"] += 1
        if topic == HEALTH:
            health = parse_health(payload, now)
            if health is not None:
                self._health = health
                self._watch(now)
                self._rebuild_market(now)
            return []
        try:
            frame = json.loads(payload)
        except ValueError:
            return []
        if not isinstance(frame, dict):
            return []
        frame.setdefault("key", topic)
        self._last[topic] = frame
        tick = self.bars.on_tick(frame, now, self.market)
        if tick.check in ("ok", "auction"):
            self.counts["trades"] += 1
        elif tick.check == "late_trade":
            self.counts["late"] += 1
        return list(tick.sealed)

    def on_clock(self, now: int) -> list[Sealed]:
        self._watch(now)
        return self.bars.on_clock(now, self.market)

    def _feed_up(self, now: int) -> bool:
        health = self._health
        if health is None or now - health.heard_at > HEALTH_MAX_AGE_MS:
            return False
        up = health.state == "connected" or (self.settings.replay and health.state == "replay")
        if not (up and health.live):
            return False
        return not (health.message_age_s is not None and health.message_age_s > GAP_S)

    def _watch(self, now: int) -> None:
        """Open and close feed pauses as they fall inside today's sessions (S14)."""
        up = self._feed_up(now)
        sessions = list(self.market.sessions.values())
        inside = any(s.start <= now < s.end for s in sessions)
        if not up:
            if self._down_since is None:
                self._down_since = now
            if inside and self._open_pause is None:
                self._open_pause = max(self._down_since, min(s.start for s in sessions))
                log.warning(
                    "the price feed is down; the candles from %s are marked", self._open_pause
                )
        else:
            self._down_since = None
            if self._open_pause is not None:
                self._pauses.append(Pause(self._open_pause, now, "feed"))
                self._open_pause = None
                log.info("the price feed is back; %d pause(s) today", len(self._pauses))
        self._rebuild_market(now)

    def persist(self, sealed: Sequence[Sealed]) -> None:
        if not sealed:
            return
        self.counts["sealed"] += len(sealed)
        rows = [
            {
                "key": s.key, "start": s.bar.start, "o": s.bar.o, "h": s.bar.h, "l": s.bar.low,
                "c": s.bar.c, "v": s.bar.v, "oi": None if s.bar.oi < 0 else s.bar.oi,
                "atp": s.bar.atp or None, "src": "ticks", "flags": s.bar.flags,
            }
            for s in sealed
        ]  # fmt: skip
        try:
            self.store.write(OWNER, lambda tx: tx.bars_put(rows))
        except StoreError:
            log.exception("%d sealed bars could not be written", len(rows))


def _uni_key(kind_spec: Mapping[str, Any]) -> str:
    kind = kind_spec["kind"]
    extra = kind_spec.get("id") or kind_spec.get("underlying") or kind_spec.get("list_id")
    return f"{kind}:{extra}" if extra is not None else kind


def _active_universes(store) -> list[Mapping[str, Any]]:
    rows = store.read(OWNER, lambda tx: [r for r in tx.screens() if r["status"] == "active"])
    return [r["spec"]["universe"] for r in rows]


async def run_feed(service, *, tick_s: float = TICK_S) -> None:
    """The job: the feed link and the live source, registered on the service."""
    if service.store is None:
        return
    client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
    feed = FeedLink(service.settings.feed_zmq, service.settings.feed_url, client)
    master = universe.Master(service.settings.openalgo_db) if service.settings.openalgo_db else None
    if master is None:
        log.error("OpenAlgo's database is not known (DATABASE_URL): no universe can be built")
        feed.close()
        await client.aclose()
        return
    source = LiveSource(
        service.store, feed, master, service.settings, service.clock, service.ensure_calendar()
    )
    service.parts["source"] = source
    service.parts["engine"] = source
    bars_task = None
    try:
        last_master_min = -1
        while True:
            now = service.clock()
            if source.new_day(now) or _master_due(now, last_master_min, source):
                try:
                    source.refresh_universes(_active_universes(service.store), now)
                except StoreError:
                    log.exception("the active screens could not be read")
                last_master_min = _minute(now)
                await source.claim()
            loop = asyncio.get_running_loop()
            if source.keys and loop.time() - source.claimed_at > CLAIM_RENEW_S:
                await source.claim()
            try:
                frames = await asyncio.wait_for(feed.receive(), tick_s)
            except TimeoutError:
                frames = []
            now = service.clock()
            sealed: list[Sealed] = []
            for topic, payload in frames:
                sealed.extend(source.on_frame(topic, payload, now))
            sealed.extend(source.on_clock(now))
            source.persist(sealed)
    finally:
        if bars_task is not None:
            bars_task.cancel()
        feed.close()
        await client.aclose()


def _minute(now: int) -> int:
    moment = datetime.fromtimestamp(now / 1000, IST)
    return moment.hour * 60 + moment.minute


def _master_due(now: int, last_min: int, source: LiveSource) -> bool:
    """Rebuild the universes once after 09:00 (the daily download), or every 5 minutes while
    the master is not ready."""
    minute = _minute(now)
    if not source.master_ready:
        return last_min < 0 or minute - last_min >= 5
    return last_min < MASTER_AT_MIN <= minute


__all__ = ["CLIENT", "FeedLink", "LiveSource", "run_feed", "universe_of"]
