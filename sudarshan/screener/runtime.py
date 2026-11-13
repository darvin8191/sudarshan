"""Runtime 1 (M9.1, design 2.3, 2.5, S14): the feed link, claims, live 1-minute bars, pauses, and
the ``Source`` the run loop judges from.

``LiveSource`` is registered as ``service.parts["source"]`` (the runner) and ``["engine"]`` (the
engine line). Its job (``run_feed``) does, in one asyncio task:

- at start and at each new day: today's sessions from the calendar (X11), the universes the
  active screens use from the master contract (universe.py, kept in the store), the keys to
  follow (at most ``max_keys``, X10) claimed at the feed in ``full`` mode (a stock that closes by
  auction needs ``vtt`` for its print, X6n), and today's sealed bars restored from the store
  (bar_today holds one trading day: an earlier day's bars are cleared, never restored as today's);
- the feed's frames: ``__health__`` becomes ``clock.Feed`` and the exchange's status, a key's
  frame goes to ``bars.Bars.on_tick`` and is kept as the key's last frame (``ltp``, ``cp``);
  every second ``on_clock`` seals the minutes whose grace is over; sealed bars are written to the
  store on its thread, a batch a tick;
- pauses as the market alerts define them (S14): the feed not heard for 12 s, not connected or
  not live, or silence of the whole socket for over 10 s, inside a session, opens a pause; it is
  closed when the feed is back. The pauses are what ``candles.read`` marks UNVERIFIED with, with
  each key's own gaps: the stretch a key followed again was not followed (bars.follow_from),
  kept in the store with its key (MEDIUM-1).

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
from sudarshan.alerts.market.sessions import (
    day_key,
    epoch_ms,
    previous_trading_day,
    sessions_of,
)
from sudarshan.autologin.schedule import IST

from . import candles, universe
from .bars import UNFOLLOWED, Bar, Bars, Followed, Sealed
from .engine import Context, Subject, timeframes
from .runs import Member
from .store import OWNER, StoreError, Tx
from .types import (
    DAY,
    MINUTE_TFS,
    NEEDS_HISTORY,
    OVER_LIMIT,
    WEEK,
    Plan,
    Series,
    Why,
    candles_in,
)

log = logging.getLogger("sudarshan.screener")

CLIENT = "sudarshan-screener"
TICK_S = 1.0
CLAIM_RENEW_S = 60.0
#: How often the service writes that it is running (S14's seen mark): a restart's pause runs from
#: the last one, so the minutes it never saw are never judged as part of a whole candle.
SEEN_EVERY_MS = 10_000
SEEN = "seen_at"
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
        #: The store row of the open feed pause (today's pauses are kept for a restart).
        self._open_pause_id: int | None = None
        #: key -> (row id, start) of each key let go today and not followed again: its open gap row.
        self._gap_rows: dict[str, tuple[int, int]] = {}
        self._seen_written = 0
        self.keys: frozenset[str] = frozenset()
        self.claimed_at: float = 0.0
        #: Whether the feed may hold a claim of ours (an earlier run's, until dropped).
        self._held = True
        #: Whether the keys were ever worked out in this process. A restart that finds the master
        #: not ready has not, and must not let go of the claims the stop kept.
        self._keys_known = False
        self.claim_words: str | None = None
        self.master_ready = False
        #: The master download the universes were built on (OpenAlgo's completion time).
        self.built_on: str | None = None
        #: Option windows waiting for their index's first price, by universe name.
        self._waiting: dict[str, Mapping[str, Any]] = {}
        #: Keys the screens follow past the key limit (``max_keys``): not claimed, not judged.
        self._over: frozenset[str] = frozenset()
        self._index_keys: dict[str, str] = {}
        self._master_asked = 0
        self.counts = {"frames": 0, "trades": 0, "sealed": 0, "late": 0}

    # ---- runs.Source --------------------------------------------------------------------------------
    def members(self, universe_spec: Mapping[str, Any]) -> Sequence[Member]:
        return self._universes.get(_uni_key(universe_spec), [])

    def subject(self, member: Member, plan: Plan, boundary: int) -> Subject | Why | None:
        if member.key in self._over:  # cut by the key limit: never "no price yet" (LOW-6)
            return Why(OVER_LIMIT, (self.settings.max_keys,))
        grid = self.market.sessions.get(member.segment)
        for tf, need in plan.need.items():  # more than today alone can hold (MEDIUM-6)
            most = _day_holds(tf, grid)
            if most is not None and need > most:
                return Why(NEEDS_HISTORY, (need, str(tf), most))
        frame = self._last.get(member.key)
        if frame is None and not self.bars.bars(member.key):
            return None
        series: dict[Any, Series] = {}
        for tf in timeframes(plan):  # the run's own timeframe too: it says when one is due
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

    def printed_at(self, member: Member) -> int | None:
        return self.bars.printed_at(member.key, self.market.today)

    # ---- a preview (6.3): the screen judged now on the last closed candle, nothing saved -----------------
    async def preview(self, plan: Plan, stored: Mapping[str, Any], now: int) -> dict[str, Any]:
        from . import runs, scheduler, screen_ops, words

        tf = plan.tf if isinstance(plan.tf, int) else 0
        screen = scheduler.Screen(
            0,
            0,
            plan.on,
            tf,
            screen_ops.SEGMENTS_OF.get(stored["universe"]["kind"], frozenset({"NSE_EQ"})),
        )
        closed = [b for b in scheduler.boundaries(screen, self.market.sessions) if b.at <= now]
        empty = {"count": 0, "first": [], "not_checked": {"count": 0, "reasons": []}}
        if not closed:
            return {
                "label": "No candle has closed yet today; the preview waits for the first.",
                **empty,
            }
        boundary = closed[-1]
        members = list(self.members(stored["universe"]))
        if not members and _uni_key(stored["universe"]) not in self._universes:
            return {"label": words.NOT_LOADED + "; save the screen to load them.", **empty}
        if not members:
            return {"label": "This universe has no instruments today.", **empty}
        judged = await runs.evaluate(
            plan, members, self, self.context(boundary.end, self.market.today)
        )
        hits = runs.ranked_hits(judged, (stored.get("rank") or {}).get("order", "desc"))
        changes = [
            {
                "key": j.member.key,
                "symbol": j.member.symbol,
                "exchange": j.member.exchange,
                "change": "in",
                "rank": i,
            }
            for i, j in enumerate(hits)
        ] + [
            {
                "key": j.member.key,
                "symbol": j.member.symbol,
                "exchange": j.member.exchange,
                "change": "not_checked",
                "rank": None,
            }
            for j in judged
            if j.outcome.kind == "not_checked"
        ]
        values = {
            j.member.key: (
                runs.hit_values(j)
                if j.outcome.kind == "hit"
                else {"_not_checked": words.not_checked_words(j.outcome.why)}
            )
            for j in judged
            if j.outcome.kind in ("hit", "not_checked")
        }
        rows, not_checked = screen_ops.result_rows(
            stored, stored.get("name", ""), None, changes, values, [], now
        )
        return {
            "label": f"Preview on the {words.hm(boundary.start)} to {words.hm(boundary.end)} candle, not saved",
            "count": len(rows),
            "first": rows[:25],
            "not_checked": not_checked,
        }

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
        self._pauses, self._open_pause, self._open_pause_id, self._last = [], None, None, {}
        self._gap_rows = {}
        self._rebuild_market(now)
        self._restore(now)
        self._restore_pauses(now)
        return True

    def _restore_pauses(self, now: int) -> None:
        """Today's pauses after a start (release review MEDIUM-5, S14): the feed pauses an earlier
        run recorded (one it left open ends at its last seen mark), then a "service" pause from
        that mark to now, inside today's sessions. A candle that holds the minutes the service
        never saw is then "Not checked", never judged on the minutes before the stop. A pause
        that names a key is that key's gap (MEDIUM-1) and goes back to the bars."""
        sessions = list(self.market.sessions.values())
        session = self.market.today
        gaps: list[tuple[str, Pause]] = []
        let_go: dict[str, tuple[int, int]] = {}

        def read(tx: Tx) -> list[Pause]:
            raw = tx.meta_get(SEEN)
            seen = int(raw) if raw and raw.isdigit() else None
            kept: list[Pause] = []
            gaps.clear()
            let_go.clear()
            for row in tx.pauses(session):
                end = row["end"]
                if end is None and row.get("key"):  # a key let go and not back yet: still let go
                    let_go[row["key"]] = (row["id"], row["start"])
                    continue
                if end is None:
                    end = max(row["start"], seen or row["start"])
                    tx.pause_close(row["id"], end)
                if row.get("key"):
                    if end > row["start"]:  # an empty one was a stretch that was no gap
                        gaps.append((row["key"], Pause(row["start"], end, row["why"])))
                else:
                    kept.append(Pause(row["start"], end, row["why"]))
            if seen is not None and sessions and now - seen > GAP_S * 1000:
                start = max(seen, min(s.start for s in sessions))
                end = min(now, max(s.end for s in sessions))
                if end - start > GAP_S * 1000:
                    tx.pause_close(tx.pause_open(session, start, "service"), end)
                    kept.append(Pause(start, end, "service"))
                    log.warning(
                        "the screener was not running from %s to %s: candles that hold that "
                        "time are not checked",
                        _hms(start),
                        _hms(end),
                    )
            return kept

        try:
            self._pauses = self.store.write(OWNER, read)
            self.bars.restore_gaps(gaps)
            self.bars.restore_let_go({key: start for key, (_, start) in let_go.items()})
            self._gap_rows = dict(let_go)
        except StoreError:
            log.exception("today's pauses could not be read back")
        self._rebuild_market(now)

    def mark_seen(self, now: int) -> None:
        """The seen mark, every SEEN_EVERY_MS: the moment a later start's pause runs from."""
        if now - self._seen_written < SEEN_EVERY_MS:
            return
        self._seen_written = now
        try:
            self.store.write(OWNER, lambda tx: tx.meta_set(SEEN, str(now), now))
        except StoreError:
            log.exception("the screener's seen mark could not be written")

    def _restore(self, now: int) -> None:
        """Today's sealed bars from the store, and nothing older (BLOCKER-1): bar_today holds one
        trading day, so every bar that starts before today's midnight (IST) is cleared in the
        same transaction, and the bars in memory start again from today's alone, whatever was
        held before (a process that runs through midnight)."""
        since = epoch_ms(self._today, 0) if self._today is not None else 0

        def roll(tx: Tx) -> tuple[int, list]:
            return tx.bars_clear(before=since), tx.bars(since=since)

        try:
            cleared, rows = self.store.write(OWNER, roll)
        except StoreError:
            cleared, rows = 0, []
        kept: list[Sealed] = []
        sums: dict[str, int] = {}
        for r in rows:  # by key, oldest first
            sums[r["key"]] = sums.get(r["key"], 0) + r["v"]
            # the running volume through the bar (HIGH-1); a row from before schema 2 has none,
            # and the sum of the day's bars so far is the nearest figure (it lacks the pre-open)
            cum = r["cum"] if r.get("cum") is not None else sums[r["key"]]
            until = r["last_trade"] if r.get("last_trade") is not None else r["start"]
            bar = Bar(r["start"], r["o"], r["h"], r["l"], r["c"], r["v"], r["oi"] if r["oi"] is not None else -1, r["atp"] or 0, r["flags"], until)  # fmt: skip
            kept.append(Sealed(r["key"], bar, cum))
        # keys the store holds no bar of take their first frame from now on as their baseline
        # (a mid-session start never counts the volume before it into a bar)
        self.bars.restore(self.market.today, kept, since=now)
        if cleared:
            log.info("cleared %d sealed bars of earlier days from the store", cleared)
        if kept:
            log.info("restored %d sealed bars of today from the store", len(kept))

    def _master_whole(self) -> str | None:
        """When OpenAlgo's last master download completed, or None if it keeps no record. Raises
        MasterUnavailable while a download runs or the symbol table is empty (OpenAlgo empties it
        before it fills it again): a universe built then would not be whole (HIGH-3)."""
        download = self.master.download() if hasattr(self.master, "download") else None
        if download is not None and download[0] == "downloading":
            raise universe.MasterUnavailable
        if not self.master.ready():
            raise universe.MasterUnavailable
        return download[1] if download is not None else None

    def master_changed(self, now: int) -> bool:
        """Whether OpenAlgo has finished a master download since the universes were built (it
        downloads after the morning login, and again when the owner asks); asked at most once a
        minute."""
        if now - self._master_asked < 60_000 or not self.master_ready:
            return False
        self._master_asked = now
        download = self.master.download() if hasattr(self.master, "download") else None
        return download is not None and download[0] == "success" and download[1] != self.built_on

    def refresh_universes(
        self,
        specs: Sequence[Mapping[str, Any]],
        now: int,
        *,
        reuse: bool = False,
        lists: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        """The universes the active screens use, from the master; the keys to follow.

        A full rebuild (the day, the master) reads every universe again; while the master is being
        downloaded or is empty, the universes already built stay as they are and the master
        counts as not ready, so the feed job tries again. ``reuse`` follows a change of screens
        (HIGH-2): a universe already built today is kept as it is, only a new one is read from the
        master, and one no active screen uses any more is let go, so the keys shrink with the
        screens.

        ``lists`` are the items of the owner's OpenAlgo lists a watchlist universe names (the feed
        job reads them through the bridge). An option window waits, not built, until its index's
        price has come: the index is followed for it, and ``window_due`` says when to build it
        (MEDIUM-7)."""
        if self._today is None:
            return
        wanted: dict[str, Mapping[str, Any]] = {}
        for kind_spec in specs:
            wanted.setdefault(_uni_key(kind_spec), kind_spec)
        found = {n: self._universes[n] for n in wanted if reuse and n in self._universes}
        listings = dict(self._listings) if reuse else {}
        built: dict[str, list[Member]] = {}
        waiting: dict[str, Mapping[str, Any]] = {}
        todo = [s for n, s in wanted.items() if n not in found]
        built_ok = True  # false only when the master could not be read for a universe
        if todo:
            try:
                built_on = self._master_whole()
                for kind_spec in todo:
                    name = _uni_key(kind_spec)
                    extra: dict[str, Any] = {}
                    if kind_spec["kind"] == "options":
                        frame = self._index_frame(kind_spec["underlying"])
                        if not (frame and frame.get("ltp")):
                            waiting[name] = kind_spec  # built once the index's price comes
                            continue
                        extra["ltp"] = int(round(frame["ltp"] * 100))
                    elif kind_spec["kind"] == "watchlist":
                        items = (lists or {}).get(int(kind_spec["list_id"]))
                        if items is None:
                            continue  # the list could not be read: not loaded, never empty
                        extra["items"] = items
                    members, rows, missing = universe_of(
                        kind_spec, self.master, self._today, extra=extra
                    )
                    built[name], self._missing[name] = members, missing
                    listings |= rows
                    if missing:
                        log.warning(
                            "%d names of %s are not in the master contract: %s",
                            len(missing),
                            name,
                            ", ".join(missing[:5]),
                        )
                self.master_ready, self.built_on = True, built_on
            except (universe.MasterUnavailable, FileNotFoundError) as exc:
                log.warning("universes could not be built: %s", exc)
                self.master_ready = built_ok = False
                if not reuse:
                    return
                built = {}  # the new universes wait for the master; the keys still follow
        found |= built
        self._universes, self._listings = found, listings
        self._waiting = waiting
        keys: list[str] = []
        seen: set[str] = set()
        for spec in wanted.values():  # an option window's index first: its price places the window
            index_key = self._index_key(spec["underlying"]) if spec["kind"] == "options" else None
            if index_key is not None and index_key not in seen:
                seen.add(index_key)
                keys.append(index_key)
        for members in found.values():
            for member in members:
                if member.key not in seen:
                    seen.add(member.key)
                    keys.append(member.key)
        self._over = frozenset(keys[self.settings.max_keys :])
        if len(keys) > self.settings.max_keys:
            log.warning(
                "the screens follow %d keys; only the first %d are claimed (the limit)",
                len(keys),
                self.settings.max_keys,
            )
            keys = keys[: self.settings.max_keys]
        self.keys = frozenset(keys)
        # Known only once a build has succeeded: a reuse rebuild that found the master still
        # downloading built nothing, and the claims a stop kept must stay until it can.
        self._keys_known = self._keys_known or built_ok
        # a key first followed mid-session, or followed again, counts from here (MEDIUM-1)
        followed = self.bars.follow_from(self.keys, now, self.market)
        self.persist(followed.sealed)
        self._keep_gaps(followed)
        self.feed.follow(self.keys)
        self._rebuild_market(now)
        if not built:
            return
        try:
            self.store.write(
                OWNER,
                lambda tx: [
                    tx.universe_replace(
                        name, name.split(":")[0], universe.member_rows(members, listings), now
                    )
                    for name, members in built.items()
                ],
            )
        except StoreError:
            log.exception("the universes could not be written to the store")

    def _index_key(self, underlying: str) -> str | None:
        """The index's own key in the master (``NSE_INDEX|Nifty Bank`` for BANKNIFTY), never a
        guess from the text of a key (MEDIUM-7)."""
        if underlying not in self._index_keys:
            try:
                rows = self.master.rows("NSE_INDEX")
            except universe.MasterUnavailable:
                return None
            self._index_keys = {row.symbol: row.key for row in rows}
        return self._index_keys.get(underlying)

    def _index_frame(self, underlying: str) -> Mapping[str, Any] | None:
        key = self._index_key(underlying)
        return self._last.get(key) if key is not None else None

    def _uni_built(self, universe_spec: Mapping[str, Any]) -> bool:
        """Whether that universe was built today (an empty one too)."""
        return _uni_key(universe_spec) in self._universes

    def window_due(self) -> bool:
        """Whether an option window that waited for its index's price can be built now."""
        return any(
            (frame := self._index_frame(spec["underlying"])) is not None and frame.get("ltp")
            for spec in self._waiting.values()
        )

    async def claim(self) -> None:
        """The keys to follow, claimed at the feed as one whole set (it replaces the last one).
        With no key to follow the claim is dropped, so nothing the screener no longer needs stays
        on the shared socket (HIGH-2); a claim an earlier run left is dropped the same way."""
        try:
            if self.keys:
                await self.feed.put_claims(dict.fromkeys(sorted(self.keys), "full"))
            elif self._held and self._keys_known:
                await self.feed.drop_claims()
            if self.keys or self._keys_known:
                self._held = bool(self.keys)
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

    def settle(self, now: int) -> None:
        """Seal and write every minute whose grace is over at ``now``: the run loop calls it
        before it judges, since a run is due the moment its candle's last minute is sealed and
        the feed job seals on a tick of its own."""
        self.persist(self.on_clock(now))

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
                self._open_pause_id = self._keep_pause(self._open_pause)
        else:
            self._down_since = None
            if self._open_pause is not None:
                self._pauses.append(Pause(self._open_pause, now, "feed"))
                if self._open_pause_id is not None:
                    pause_id, self._open_pause_id = self._open_pause_id, None
                    self._end_pause(pause_id, now)
                self._open_pause = None
                log.info("the price feed is back; %d pause(s) today", len(self._pauses))
        self._rebuild_market(now)

    def _keep_pause(self, start: int) -> int | None:
        """A feed pause, recorded as it opens, so a restart today still knows it (MEDIUM-5)."""
        try:
            return self.store.write(
                OWNER, lambda tx: tx.pause_open(self.market.today, start, "feed")
            )
        except StoreError:
            log.exception("a pause of the price feed could not be recorded")
            return None

    def _end_pause(self, pause_id: int, end: int) -> None:
        try:
            self.store.write(OWNER, lambda tx: tx.pause_close(pause_id, end))
        except StoreError:
            log.exception("the end of a pause of the price feed could not be recorded")

    def _keep_gaps(self, followed: Followed) -> None:
        """Each key's gap is kept with its key, so a restart today still marks that key's candles
        (MEDIUM-1). A key let go gets its row at once, open from the moment it was let go, and the
        comeback closes it (at the end of its gap, or at its start when the stretch was no gap), so
        a restart in between keeps the gap from the let-go (round-3 review LOW-A2)."""
        if not (followed.gaps or followed.let_go or followed.back):
            return
        session = self.market.today
        gap_of = dict(followed.gaps)
        rows = self._gap_rows

        def write(tx: Tx) -> dict[str, tuple[int, int]]:
            opened: dict[str, tuple[int, int]] = {}
            for key in followed.back:
                row = rows.get(key)
                gap = gap_of.pop(key, None)
                if row is not None:
                    tx.pause_close(row[0], row[1] if gap is None else gap.end)
                elif gap is not None:
                    tx.pause_open(session, gap.start, gap.cause, end=gap.end, key=key)
            for key, gap in gap_of.items():  # first followed after the open: never let go
                tx.pause_open(session, gap.start, gap.cause, end=gap.end, key=key)
            for key, at in followed.let_go:
                if key not in rows:
                    opened[key] = (tx.pause_open(session, at, UNFOLLOWED, key=key), at)
            return opened

        try:
            opened = self.store.write(OWNER, write)
        except StoreError:
            log.exception("the gaps of instruments let go or followed again could not be recorded")
            return
        for key in followed.back:
            rows.pop(key, None)
        rows.update(opened)

    def persist(self, sealed: Sequence[Sealed]) -> None:
        if not sealed:
            return
        self.counts["sealed"] += len(sealed)
        rows = [
            {
                "key": s.key, "start": s.bar.start, "o": s.bar.o, "h": s.bar.h, "l": s.bar.low,
                "c": s.bar.c, "v": s.bar.v, "oi": None if s.bar.oi < 0 else s.bar.oi,
                "atp": s.bar.atp or None, "src": "ticks", "flags": s.bar.flags,
                "cum": s.cum, "last_trade": s.bar.until or None,
            }
            for s in sealed
        ]  # fmt: skip
        try:
            self.store.write(OWNER, lambda tx: tx.bars_put(rows))
        except StoreError:
            log.exception("%d sealed bars could not be written", len(rows))


def _day_holds(tf, grid) -> int | None:
    """The most candles of ``tf`` one session gives (its short last one counted); None when the
    session is not known. Before the history download (SV3) that is all a screen can read."""
    if grid is None:
        return None
    if tf in (DAY, WEEK):
        return 1
    if isinstance(tf, int) and tf > 0:
        return candles_in(tf, grid.end - grid.start)
    return None


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
            moved = _take_moved(service)
            full = (
                source.new_day(now)
                or _master_due(now, last_master_min, source)
                or source.master_changed(now)
            )
            # a screen made, edited, resumed, paused or deleted (HIGH-2), or an option window
            # whose index's price has just come (MEDIUM-7): only what is new is built
            if full or moved or source.window_due():
                try:
                    specs = _active_universes(service.store)
                except StoreError:
                    log.exception("the active screens could not be read")
                else:
                    lists = await _watchlists(service, specs)
                    source.refresh_universes(specs, now, reuse=not full, lists=lists)
                if full:
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
            source.mark_seen(now)
    finally:
        if bars_task is not None:
            bars_task.cancel()
        feed.close()
        await client.aclose()


async def _watchlists(service, specs: Sequence[Mapping[str, Any]]) -> dict[int, list]:
    """The items of the owner's lists that watchlist universes name, read through the bridge's
    internal route (the service's sealed link); a list that cannot be read is left out, so its
    universe is not loaded rather than empty."""
    link = service.parts.get("link")
    wanted = {int(s["list_id"]) for s in specs if s.get("kind") == "watchlist"}
    out: dict[int, list] = {}
    if link is None or not wanted:
        return out
    for list_id in sorted(wanted):
        try:
            found = await link.watchlist_read(list_id)
        except Exception as exc:  # noqa: BLE001  (the link's own failure words)
            log.warning("the watchlist %s a screen looks at could not be read: %s", list_id, exc)
            continue
        if found is not None:
            out[list_id] = list(found.get("items") or ())
    return out


def _take_moved(service) -> bool:
    """Whether an op changed the active screens or what they look at since the last tick."""
    take = getattr(service, "take_screens_moved", None)
    return bool(take()) if callable(take) else False


async def release_claims(service, *, waits: Sequence[float] = (0.0, 5.0, 30.0, 120.0, 600.0)):
    """The screener is off (switched off, or kept off): the feed may still keep the keys an
    earlier run claimed, so they are dropped, asked again with growing waits until the feed
    answers, as market alerts do (HIGH-2). A plain stop keeps them for the restart."""
    url = f"{service.settings.feed_url.rstrip('/')}/claims/{CLIENT}"
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        for wait in waits:
            await asyncio.sleep(wait)
            try:
                answer = await client.delete(url, timeout=5.0)
            except httpx.HTTPError:
                continue
            if answer.status_code == 200:
                log.info("the screener is off; the price feed no longer keeps its instruments")
                return
    log.warning("the screener is off, but the price feed did not answer to drop its instruments")


def _hms(at: int) -> str:
    return datetime.fromtimestamp(at / 1000, IST).strftime("%H:%M:%S")


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


__all__ = ["CLIENT", "FeedLink", "LiveSource", "release_claims", "run_feed", "universe_of"]
